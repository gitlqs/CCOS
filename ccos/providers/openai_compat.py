"""OpenAI-compatible provider — works with OpenAI, Grok, and any compatible API.

Supports three API paths:
- Chat Completions (``/v1/chat/completions``) — GPT-4o, GPT-4.1, etc.
- Chat Completions reasoning mode — o-series, GPT-5 series (``max_completion_tokens``)
- Responses API (``/v1/responses``) — Codex models (``gpt-5.x-codex*``)
"""

from __future__ import annotations

import json
import os
import uuid
from typing import Any, AsyncIterator

from ccos.providers.base import (
    ChunkType,
    ContentBlock,
    ImageContent,
    LLMProvider,
    Message,
    StreamChunk,
    TextContent,
    ThinkingConfig,
    ToolCallContent,
    ToolResultContent,
    ToolSchema,
)


# ---------------------------------------------------------------------------
# Model classification helpers
# ---------------------------------------------------------------------------

def _is_reasoning_model(model: str) -> bool:
    """Check if a model is an OpenAI reasoning model.

    Reasoning models require max_completion_tokens (not max_tokens),
    use the 'developer' role (not 'system'), and don't support temperature.

    Includes:
    - o-series: o1, o1-mini, o3, o3-mini, o4-mini, etc.
    - GPT-5 series: gpt-5, gpt-5-mini, gpt-5.1, gpt-5.4, etc.
    """
    # o-series: o1, o3, o4 (but not gpt-4o which starts with "gpt-")
    first = model.split("-")[0]
    if first in ("o1", "o3", "o4"):
        return True
    # GPT-5 series: gpt-5, gpt-5-mini, gpt-5.1, gpt-5.2, gpt-5.4, etc.
    if model.startswith("gpt-5"):
        return True
    return False


def _is_responses_model(model: str) -> bool:
    """Check if a model requires the Responses API instead of Chat Completions.

    Codex models (gpt-5.x-codex, codex-mini-latest, etc.) are not chat models
    and must use ``/v1/responses``.
    """
    lower = model.lower()
    return "codex" in lower


# ---------------------------------------------------------------------------
# Chat Completions message/tool conversion
# ---------------------------------------------------------------------------

def _messages_to_openai(
    messages: list[Message],
    system: str | list[str],
    *,
    model: str = "",
) -> list[dict[str, Any]]:
    """Convert internal messages -> OpenAI chat format.

    Key differences from Anthropic:
    - System prompt is a message with role='system' (or 'developer' for reasoning models)
    - tool_use  -> assistant message with tool_calls array
    - tool_result -> message with role='tool'
    """
    out: list[dict[str, Any]] = []

    # Reasoning models (o1-2024-12-17+, o3, o4, gpt-5) use 'developer' role
    sys_role = "developer" if _is_reasoning_model(model) else "system"

    # System message
    sys_text = "\n\n".join(system) if isinstance(system, list) else system
    if sys_text:
        out.append({"role": sys_role, "content": sys_text})

    for msg in messages:
        if isinstance(msg.content, str):
            out.append({"role": msg.role, "content": msg.content})
            continue

        # Separate content blocks by type
        text_parts: list[str] = []
        tool_calls: list[dict[str, Any]] = []
        tool_results: list[dict[str, Any]] = []
        image_parts: list[dict[str, Any]] = []

        for block in msg.content:
            if isinstance(block, TextContent):
                text_parts.append(block.text)
            elif isinstance(block, ToolCallContent):
                tool_calls.append({
                    "id": block.id,
                    "type": "function",
                    "function": {
                        "name": block.name,
                        "arguments": json.dumps(block.input),
                    },
                })
            elif isinstance(block, ToolResultContent):
                content_str = block.content if isinstance(block.content, str) else \
                    " ".join(c.text for c in block.content if isinstance(c, TextContent))
                tool_results.append({
                    "role": "tool",
                    "tool_call_id": block.tool_use_id,
                    "content": content_str,
                })
            elif isinstance(block, ImageContent):
                if block.source_type == "base64":
                    image_parts.append({
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{block.media_type};base64,{block.data}",
                        },
                    })
                else:
                    image_parts.append({
                        "type": "image_url",
                        "image_url": {"url": block.data},
                    })

        if msg.role == "assistant":
            m: dict[str, Any] = {"role": "assistant"}
            if text_parts:
                m["content"] = "".join(text_parts)
            else:
                m["content"] = None
            if tool_calls:
                m["tool_calls"] = tool_calls
            out.append(m)
        elif msg.role == "user":
            # Emit tool results first (each is its own message)
            for tr in tool_results:
                out.append(tr)
            # Then emit user text/images
            if text_parts or image_parts:
                if image_parts:
                    content_blocks: list[dict[str, Any]] = []
                    if text_parts:
                        content_blocks.append({"type": "text", "text": "".join(text_parts)})
                    content_blocks.extend(image_parts)
                    out.append({"role": "user", "content": content_blocks})
                else:
                    out.append({"role": "user", "content": "".join(text_parts)})

    return out


def _tools_to_openai(tools: list[ToolSchema]) -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": t.name,
                "description": t.description,
                "parameters": t.input_schema,
            },
        }
        for t in tools
    ]


# ---------------------------------------------------------------------------
# Responses API message/tool conversion
# ---------------------------------------------------------------------------

def _messages_to_responses(
    messages: list[Message],
) -> list[dict[str, Any]]:
    """Convert internal messages -> Responses API input array.

    The Responses API input format:
    - User text:          {"role": "user", "content": "..."}
    - Assistant text:     {"role": "assistant", "content": [{"type": "output_text", "text": "..."}]}
    - Tool calls:         {"type": "function_call", "call_id": "...", "name": "...", "arguments": "..."}
    - Tool results:       {"type": "function_call_output", "call_id": "...", "output": "..."}
    """
    out: list[dict[str, Any]] = []

    for msg in messages:
        if isinstance(msg.content, str):
            out.append({"role": msg.role, "content": msg.content})
            continue

        text_parts: list[str] = []
        tool_calls: list[dict[str, Any]] = []
        tool_results: list[dict[str, Any]] = []

        for block in msg.content:
            if isinstance(block, TextContent):
                text_parts.append(block.text)
            elif isinstance(block, ToolCallContent):
                tool_calls.append({
                    "type": "function_call",
                    "call_id": block.id,
                    "name": block.name,
                    "arguments": json.dumps(block.input),
                })
            elif isinstance(block, ToolResultContent):
                content_str = block.content if isinstance(block.content, str) else \
                    " ".join(c.text for c in block.content if isinstance(c, TextContent))
                tool_results.append({
                    "type": "function_call_output",
                    "call_id": block.tool_use_id,
                    "output": content_str,
                })

        if msg.role == "assistant":
            # Emit assistant text as a message item
            if text_parts:
                text = "".join(text_parts)
                out.append({
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": text}],
                })
            # Emit tool calls as individual function_call items
            for tc in tool_calls:
                out.append(tc)
        elif msg.role == "user":
            # Emit tool results first
            for tr in tool_results:
                out.append(tr)
            # Then user text
            if text_parts:
                out.append({"role": "user", "content": "".join(text_parts)})

    return out


def _tools_to_responses(tools: list[ToolSchema]) -> list[dict[str, Any]]:
    """Convert tool schemas -> Responses API tool format.

    Key difference from Chat Completions: name/description/parameters are
    top-level fields, NOT nested under a 'function' key.
    """
    return [
        {
            "type": "function",
            "name": t.name,
            "description": t.description,
            "parameters": t.input_schema,
        }
        for t in tools
    ]


# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------

class OpenAICompatProvider(LLMProvider):
    """Provider for OpenAI and any OpenAI-compatible API."""

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        provider_name: str = "openai",
        timeout: float = 600.0,
    ):
        from openai import AsyncOpenAI
        self._provider_name = provider_name
        self._api_key = api_key or os.environ.get("OPENAI_API_KEY", "")
        kwargs: dict[str, Any] = {
            "api_key": self._api_key,
            "timeout": timeout,
        }
        if base_url:
            kwargs["base_url"] = base_url
        self._client = AsyncOpenAI(**kwargs)

    @property
    def name(self) -> str:
        return self._provider_name

    async def list_models(self) -> list[str]:
        try:
            result = await self._client.models.list()
            return sorted(m.id for m in result.data)
        except Exception:
            return []

    async def stream(
        self,
        *,
        messages: list[Message],
        system: str | list[str],
        tools: list[ToolSchema] | None = None,
        model: str,
        max_tokens: int = 16384,
        temperature: float | None = None,
        thinking: ThinkingConfig | None = None,
    ) -> AsyncIterator[StreamChunk]:
        if _is_responses_model(model):
            async for chunk in self._stream_responses(
                messages=messages,
                system=system,
                tools=tools,
                model=model,
                max_tokens=max_tokens,
                temperature=temperature,
            ):
                yield chunk
        else:
            async for chunk in self._stream_chat(
                messages=messages,
                system=system,
                tools=tools,
                model=model,
                max_tokens=max_tokens,
                temperature=temperature,
            ):
                yield chunk

    # ------------------------------------------------------------------
    # Chat Completions streaming (GPT-4o, GPT-5, o-series, etc.)
    # ------------------------------------------------------------------

    async def _stream_chat(
        self,
        *,
        messages: list[Message],
        system: str | list[str],
        tools: list[ToolSchema] | None = None,
        model: str,
        max_tokens: int = 16384,
        temperature: float | None = None,
    ) -> AsyncIterator[StreamChunk]:
        reasoning = _is_reasoning_model(model)

        params: dict[str, Any] = {
            "model": model,
            "messages": _messages_to_openai(messages, system, model=model),
            "stream": True,
            "stream_options": {"include_usage": True},
        }

        if reasoning:
            # Reasoning models use max_completion_tokens and don't support temperature
            params["max_completion_tokens"] = max_tokens
        else:
            params["max_tokens"] = max_tokens
            if temperature is not None:
                params["temperature"] = temperature

        if tools:
            params["tools"] = _tools_to_openai(tools)

        # Track partial tool calls during streaming
        partial_tool_calls: dict[int, ToolCallContent] = {}
        partial_args: dict[int, str] = {}
        # Remember the stop reason from the finish_reason chunk so the
        # trailing usage-only chunk doesn't overwrite it.
        resolved_stop: str | None = None
        done_emitted = False

        response = await self._client.chat.completions.create(**params)

        try:
            async for chunk in response:
                choice = chunk.choices[0] if chunk.choices else None

                if choice is None:
                    # Usage-only chunk at the end — use the saved stop reason
                    if chunk.usage:
                        stop = resolved_stop or ("tool_use" if partial_tool_calls else "end_turn")
                        yield StreamChunk(
                            type=ChunkType.DONE,
                            stop_reason=stop,
                            input_tokens=chunk.usage.prompt_tokens or 0,
                            output_tokens=chunk.usage.completion_tokens or 0,
                        )
                        done_emitted = True
                    continue

                delta = choice.delta
                finish_reason = choice.finish_reason

                # Text content
                if delta and delta.content:
                    yield StreamChunk(type=ChunkType.TEXT, text=delta.content)

                # Tool calls
                if delta and delta.tool_calls:
                    for tc_delta in delta.tool_calls:
                        idx = tc_delta.index
                        if idx not in partial_tool_calls:
                            # New tool call
                            tc = ToolCallContent(
                                id=tc_delta.id or f"call_{uuid.uuid4().hex[:24]}",
                                name=tc_delta.function.name if tc_delta.function and tc_delta.function.name else "",
                            )
                            partial_tool_calls[idx] = tc
                            partial_args[idx] = ""
                            yield StreamChunk(type=ChunkType.TOOL_CALL_START, tool_call=tc)

                        if tc_delta.function and tc_delta.function.arguments:
                            partial_args[idx] += tc_delta.function.arguments
                            yield StreamChunk(
                                type=ChunkType.TOOL_CALL_DELTA,
                                text=tc_delta.function.arguments,
                            )

                # Finish
                if finish_reason:
                    # Finalise any open tool calls
                    for idx, tc in partial_tool_calls.items():
                        try:
                            tc.input = json.loads(partial_args.get(idx, "{}"))
                        except json.JSONDecodeError:
                            tc.input = {}
                        yield StreamChunk(type=ChunkType.TOOL_CALL_END, tool_call=tc)

                    if finish_reason == "tool_calls":
                        resolved_stop = "tool_use"
                    elif finish_reason == "length":
                        resolved_stop = "length"
                    else:
                        resolved_stop = "end_turn"

            # If stream ended without a usage chunk, emit DONE now
            if not done_emitted:
                yield StreamChunk(
                    type=ChunkType.DONE,
                    stop_reason=resolved_stop or "end_turn",
                    input_tokens=0,
                    output_tokens=0,
                )
        finally:
            # Explicitly close the OpenAI SDK stream to release the
            # underlying httpx connection.
            await response.close()

    # ------------------------------------------------------------------
    # Responses API streaming (Codex models)
    # ------------------------------------------------------------------

    async def _stream_responses(
        self,
        *,
        messages: list[Message],
        system: str | list[str],
        tools: list[ToolSchema] | None = None,
        model: str,
        max_tokens: int = 16384,
        temperature: float | None = None,
    ) -> AsyncIterator[StreamChunk]:
        """Stream using the Responses API (``/v1/responses``).

        Used for Codex models and any model that doesn't support Chat Completions.
        """
        sys_text = "\n\n".join(system) if isinstance(system, list) else system

        params: dict[str, Any] = {
            "model": model,
            "input": _messages_to_responses(messages),
            "stream": True,
            "max_output_tokens": max_tokens,
        }

        if sys_text:
            params["instructions"] = sys_text

        if temperature is not None:
            params["temperature"] = temperature

        if tools:
            params["tools"] = _tools_to_responses(tools)

        # Track in-flight function calls by item_id
        pending_calls: dict[str, ToolCallContent] = {}
        pending_args: dict[str, str] = {}
        has_tool_calls = False

        response = await self._client.responses.create(**params)

        try:
            async for event in response:
                etype = event.type

                # -- Text delta --
                if etype == "response.output_text.delta":
                    yield StreamChunk(type=ChunkType.TEXT, text=event.delta)

                # -- Function call: new item added --
                elif etype == "response.output_item.added":
                    item = event.item
                    if getattr(item, "type", None) == "function_call":
                        call_id = getattr(item, "call_id", "") or f"call_{uuid.uuid4().hex[:24]}"
                        name = getattr(item, "name", "") or ""
                        tc = ToolCallContent(id=call_id, name=name)
                        pending_calls[item.id] = tc
                        pending_args[item.id] = ""
                        has_tool_calls = True
                        yield StreamChunk(type=ChunkType.TOOL_CALL_START, tool_call=tc)

                # -- Function call arguments delta --
                elif etype == "response.function_call_arguments.delta":
                    item_id = event.item_id
                    if item_id in pending_args:
                        pending_args[item_id] += event.delta
                        yield StreamChunk(type=ChunkType.TOOL_CALL_DELTA, text=event.delta)

                # -- Function call arguments done --
                elif etype == "response.function_call_arguments.done":
                    item_id = event.item_id
                    if item_id in pending_calls:
                        tc = pending_calls[item_id]
                        # Update with final values
                        tc.name = getattr(event, "name", tc.name) or tc.name
                        tc.id = getattr(event, "call_id", tc.id) or tc.id
                        try:
                            tc.input = json.loads(getattr(event, "arguments", "{}"))
                        except json.JSONDecodeError:
                            tc.input = {}
                        yield StreamChunk(type=ChunkType.TOOL_CALL_END, tool_call=tc)

                # -- Response completed (has usage) --
                elif etype == "response.completed":
                    resp = event.response
                    usage = getattr(resp, "usage", None)
                    input_tokens = 0
                    output_tokens = 0
                    if usage:
                        input_tokens = getattr(usage, "input_tokens", 0) or 0
                        output_tokens = getattr(usage, "output_tokens", 0) or 0
                    # Check the response status for truncation
                    status = getattr(resp, "status", "completed")
                    if status == "incomplete":
                        stop = "length"
                    elif has_tool_calls:
                        stop = "tool_use"
                    else:
                        stop = "end_turn"
                    yield StreamChunk(
                        type=ChunkType.DONE,
                        stop_reason=stop,
                        input_tokens=input_tokens,
                        output_tokens=output_tokens,
                    )

                # -- Response incomplete (truncated) --
                elif etype == "response.incomplete":
                    resp = event.response
                    usage = getattr(resp, "usage", None)
                    input_tokens = 0
                    output_tokens = 0
                    if usage:
                        input_tokens = getattr(usage, "input_tokens", 0) or 0
                        output_tokens = getattr(usage, "output_tokens", 0) or 0
                    yield StreamChunk(
                        type=ChunkType.DONE,
                        stop_reason="length",
                        input_tokens=input_tokens,
                        output_tokens=output_tokens,
                    )

                # -- Response failed --
                elif etype == "response.failed":
                    resp = event.response
                    error = getattr(resp, "error", None)
                    error_msg = str(error) if error else "Unknown error"
                    yield StreamChunk(type=ChunkType.ERROR, text=error_msg)
                    yield StreamChunk(
                        type=ChunkType.DONE,
                        stop_reason="error",
                        input_tokens=0,
                        output_tokens=0,
                    )

        finally:
            if hasattr(response, "close"):
                await response.close()

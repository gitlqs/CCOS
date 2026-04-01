"""OpenAI-compatible provider — works with OpenAI, Grok, and any compatible API."""

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


def _messages_to_openai(
    messages: list[Message],
    system: str | list[str],
) -> list[dict[str, Any]]:
    """Convert internal messages → OpenAI chat format.

    Key differences from Anthropic:
    - System prompt is a message with role='system'
    - tool_use  → assistant message with tool_calls array
    - tool_result → message with role='tool'
    """
    out: list[dict[str, Any]] = []

    # System message
    sys_text = "\n\n".join(system) if isinstance(system, list) else system
    if sys_text:
        out.append({"role": "system", "content": sys_text})

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
        params: dict[str, Any] = {
            "model": model,
            "messages": _messages_to_openai(messages, system),
            "max_tokens": max_tokens,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if temperature is not None:
            params["temperature"] = temperature
        if tools:
            params["tools"] = _tools_to_openai(tools)

        # Track partial tool calls during streaming
        partial_tool_calls: dict[int, ToolCallContent] = {}
        partial_args: dict[int, str] = {}

        response = await self._client.chat.completions.create(**params)

        try:
            async for chunk in response:
                choice = chunk.choices[0] if chunk.choices else None

                if choice is None:
                    # Usage-only chunk at the end
                    if chunk.usage:
                        yield StreamChunk(
                            type=ChunkType.DONE,
                            stop_reason="end_turn" if not partial_tool_calls else "tool_use",
                            input_tokens=chunk.usage.prompt_tokens or 0,
                            output_tokens=chunk.usage.completion_tokens or 0,
                        )
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

                    stop = "tool_use" if finish_reason == "tool_calls" else "end_turn"
                    # Don't yield DONE here — wait for the usage chunk
                    if not chunk.usage:
                        yield StreamChunk(
                            type=ChunkType.DONE,
                            stop_reason=stop,
                            input_tokens=0,
                            output_tokens=0,
                        )
        finally:
            # Explicitly close the OpenAI SDK stream to release the
            # underlying httpx connection. Without this, GC of the
            # stream object schedules an async_generator_athrow task
            # that never runs, producing:
            #   "Task was destroyed but it is pending!"
            await response.close()

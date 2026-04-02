"""Anthropic (Claude) provider — direct API, Bedrock, and Vertex."""

from __future__ import annotations

import json
import os
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
    ThinkingContent,
    ToolCallContent,
    ToolResultContent,
    ToolSchema,
)


def _messages_to_api(messages: list[Message]) -> list[dict[str, Any]]:
    """Convert internal messages → Anthropic API format."""
    out: list[dict[str, Any]] = []
    for msg in messages:
        if isinstance(msg.content, str):
            out.append({"role": msg.role, "content": msg.content})
        else:
            blocks: list[dict[str, Any]] = []
            for b in msg.content:
                if isinstance(b, TextContent):
                    blocks.append({"type": "text", "text": b.text})
                elif isinstance(b, ImageContent):
                    blocks.append({
                        "type": "image",
                        "source": {
                            "type": b.source_type,
                            "media_type": b.media_type,
                            "data": b.data,
                        },
                    })
                elif isinstance(b, ToolCallContent):
                    blocks.append({
                        "type": "tool_use",
                        "id": b.id,
                        "name": b.name,
                        "input": b.input,
                    })
                elif isinstance(b, ToolResultContent):
                    content: Any
                    if isinstance(b.content, str):
                        content = b.content
                    else:
                        content = [
                            {"type": "text", "text": c.text} if isinstance(c, TextContent)
                            else {"type": "image", "source": {"type": c.source_type, "media_type": c.media_type, "data": c.data}}
                            for c in b.content
                        ]
                    blocks.append({
                        "type": "tool_result",
                        "tool_use_id": b.tool_use_id,
                        "content": content,
                        **({"is_error": True} if b.is_error else {}),
                    })
                elif isinstance(b, ThinkingContent):
                    blocks.append({
                        "type": "thinking",
                        "thinking": b.thinking,
                        "signature": b.signature,
                    })
            out.append({"role": msg.role, "content": blocks})
    return out


def _tools_to_api(tools: list[ToolSchema]) -> list[dict[str, Any]]:
    return [
        {
            "name": t.name,
            "description": t.description,
            "input_schema": t.input_schema,
        }
        for t in tools
    ]


class AnthropicProvider(LLMProvider):
    """Direct Anthropic API provider."""

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: float = 600.0,
        max_retries: int = 2,
        oauth_token: str | None = None,
    ):
        import anthropic
        self._oauth_token = oauth_token
        kwargs: dict[str, Any] = {
            "timeout": timeout,
            "max_retries": max_retries,
        }
        if oauth_token:
            # OAuth Bearer token — use auth_token parameter
            kwargs["auth_token"] = oauth_token
            kwargs["default_headers"] = {
                "anthropic-beta": "oauth-2025-04-20,claude-code-20250219",
            }
        else:
            self._api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
            kwargs["api_key"] = self._api_key
        if base_url:
            kwargs["base_url"] = base_url
        self._client = anthropic.AsyncAnthropic(**kwargs)

    @property
    def name(self) -> str:
        return "anthropic"

    @property
    def supports_thinking(self) -> bool:
        return True

    async def list_models(self) -> list[str]:
        try:
            result = await self._client.models.list(limit=100)
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
            "messages": _messages_to_api(messages),
            "max_tokens": max_tokens,
        }

        # System prompt
        if isinstance(system, list):
            params["system"] = "\n\n".join(s for s in system if s)
        elif system:
            params["system"] = system

        # Tools
        if tools:
            params["tools"] = _tools_to_api(tools)

        # Thinking
        if thinking and thinking.enabled:
            params["thinking"] = {
                "type": "enabled",
                "budget_tokens": thinking.budget_tokens,
            }
        else:
            if temperature is not None:
                params["temperature"] = temperature

        # Stream using raw events for efficiency
        async with self._client.messages.stream(**params) as stream:
            current_tool: ToolCallContent | None = None
            tool_input_json = ""

            async for event in stream:
                # --- content_block_start ---
                if event.type == "content_block_start":
                    cb = event.content_block
                    if cb.type == "tool_use":
                        current_tool = ToolCallContent(
                            id=cb.id,
                            name=cb.name,
                            input={},
                        )
                        tool_input_json = ""
                        yield StreamChunk(
                            type=ChunkType.TOOL_CALL_START,
                            tool_call=current_tool,
                        )
                    elif cb.type == "thinking":
                        yield StreamChunk(
                            type=ChunkType.THINKING,
                            text="",
                        )

                # --- content_block_delta ---
                elif event.type == "content_block_delta":
                    delta = event.delta
                    if delta.type == "text_delta":
                        yield StreamChunk(
                            type=ChunkType.TEXT,
                            text=delta.text,
                        )
                    elif delta.type == "input_json_delta":
                        tool_input_json += delta.partial_json
                        yield StreamChunk(
                            type=ChunkType.TOOL_CALL_DELTA,
                            text=delta.partial_json,
                        )
                    elif delta.type == "thinking_delta":
                        yield StreamChunk(
                            type=ChunkType.THINKING,
                            text=delta.thinking,
                        )

                # --- content_block_stop ---
                elif event.type == "content_block_stop":
                    if current_tool is not None:
                        # Parse accumulated JSON
                        try:
                            current_tool.input = json.loads(tool_input_json) if tool_input_json else {}
                        except json.JSONDecodeError:
                            current_tool.input = {}
                        yield StreamChunk(
                            type=ChunkType.TOOL_CALL_END,
                            tool_call=current_tool,
                        )
                        current_tool = None
                        tool_input_json = ""

                # --- message_stop ---
                elif event.type == "message_stop":
                    pass  # handled below

            # Final usage
            final = await stream.get_final_message()
            yield StreamChunk(
                type=ChunkType.DONE,
                stop_reason=final.stop_reason,
                input_tokens=final.usage.input_tokens,
                output_tokens=final.usage.output_tokens,
                cache_read_tokens=getattr(final.usage, "cache_read_input_tokens", 0) or 0,
                cache_creation_tokens=getattr(final.usage, "cache_creation_input_tokens", 0) or 0,
            )

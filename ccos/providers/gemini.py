"""Google Gemini provider via Generative Language REST API."""

from __future__ import annotations

import json
import os
from typing import Any, AsyncIterator

import httpx

from ccos.providers.base import (
    ChunkType,
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


def _as_role(role: str) -> str:
    return "model" if role == "assistant" else "user"


def _part_to_gemini(block: Any, tool_names: dict[str, str] | None = None) -> dict[str, Any] | None:
    if isinstance(block, TextContent):
        return {"text": block.text}
    if isinstance(block, ImageContent) and block.source_type == "base64":
        return {
            "inlineData": {
                "mimeType": block.media_type,
                "data": block.data,
            }
        }
    if isinstance(block, ImageContent) and block.source_type == "url":
        return {
            "fileData": {
                "mimeType": block.media_type,
                "fileUri": block.data,
            }
        }
    if isinstance(block, ToolCallContent):
        part: dict[str, Any] = {
            "functionCall": {
                "name": block.name,
                "args": block.input,
            }
        }
        if block.id:
            part["functionCall"]["id"] = block.id
        sig = block.metadata.get("thoughtSignature")
        if sig:
            part["thoughtSignature"] = sig
        return part
    if isinstance(block, ToolResultContent):
        response: dict[str, Any] = {"result": _tool_result_to_response(block)}
        if block.is_error:
            response["is_error"] = True
        tool_name = tool_names.get(block.tool_use_id, "tool_result") if tool_names else "tool_result"
        return {
            "functionResponse": {
                "id": block.tool_use_id,
                "name": tool_name,
                "response": response,
            }
        }
    return None


def _tool_result_to_response(block: ToolResultContent) -> Any:
    if isinstance(block.content, str):
        return block.content
    items: list[dict[str, Any]] = []
    for item in block.content:
        if isinstance(item, TextContent):
            items.append({"type": "text", "text": item.text})
        elif isinstance(item, ImageContent):
            if item.source_type == "base64":
                items.append(
                    {
                        "type": "image",
                        "inlineData": {
                            "mimeType": item.media_type,
                            "data": item.data,
                        },
                    }
                )
            else:
                items.append(
                    {
                        "type": "image",
                        "fileData": {
                            "mimeType": item.media_type,
                            "fileUri": item.data,
                        },
                    }
                )
    return items


def _message_to_gemini(msg: Message, tool_names: dict[str, str] | None = None) -> list[dict[str, Any]]:
    if isinstance(msg.content, str):
        return [{"role": _as_role(msg.role), "parts": [{"text": msg.content}]}]

    text_parts: list[dict[str, Any]] = []
    tool_parts: list[dict[str, Any]] = []
    tool_result_parts: list[dict[str, Any]] = []

    for block in msg.content:
        part = _part_to_gemini(block, tool_names)
        if not part:
            continue
        if "functionCall" in part:
            tool_parts.append(part)
        elif "functionResponse" in part:
            tool_result_parts.append(part)
        else:
            text_parts.append(part)

    out: list[dict[str, Any]] = []
    if msg.role == "assistant":
        parts = text_parts + tool_parts
        if parts:
            out.append({"role": "model", "parts": parts})
    else:
        if tool_result_parts:
            out.append({"role": "user", "parts": tool_result_parts})
        if text_parts:
            out.append({"role": "user", "parts": text_parts})
    return out


def _messages_to_gemini(messages: list[Message]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    tool_names: dict[str, str] = {}
    for msg in messages:
        if not isinstance(msg.content, list):
            out.extend(_message_to_gemini(msg, tool_names))
            continue
        for block in msg.content:
            if isinstance(block, ToolCallContent) and block.id:
                tool_names[block.id] = block.name
        out.extend(_message_to_gemini(msg, tool_names))
    return out


_GEMINI_SCHEMA_KEYS = frozenset({
    "type", "format", "title", "description", "nullable", "enum",
    "maxItems", "minItems", "properties", "required", "minProperties",
    "maxProperties", "minLength", "maxLength", "pattern", "example",
    "anyOf", "propertyOrdering", "default", "items", "minimum", "maximum",
})


def _clean_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Strip fields not supported by the Gemini API Schema object."""
    out: dict[str, Any] = {}
    for key, value in schema.items():
        if key not in _GEMINI_SCHEMA_KEYS:
            continue
        if key == "properties" and isinstance(value, dict):
            out[key] = {k: _clean_schema(v) for k, v in value.items() if isinstance(v, dict)}
        elif key == "items" and isinstance(value, dict):
            out[key] = _clean_schema(value)
        elif key == "anyOf" and isinstance(value, list):
            out[key] = [_clean_schema(v) for v in value if isinstance(v, dict)]
        else:
            out[key] = value
    return out


def _tools_to_gemini(tools: list[ToolSchema]) -> list[dict[str, Any]]:
    return [{
        "functionDeclarations": [
            {
                "name": t.name,
                "description": t.description,
                "parameters": _clean_schema(t.input_schema),
            }
            for t in tools
        ]
    }]


def _system_to_gemini(system: str | list[str]) -> dict[str, Any] | None:
    text = "\n\n".join(system) if isinstance(system, list) else system
    if not text:
        return None
    return {"parts": [{"text": text}]}


def _finish_reason(reason: str | None, has_tools: bool) -> str:
    if not reason:
        return "tool_use" if has_tools else "end_turn"
    reason = reason.upper()
    if "STOP" in reason:
        return "tool_use" if has_tools else "end_turn"
    if reason == "MAX_TOKENS":
        return "length"
    if reason in {"RECITATION", "SAFETY", "BLOCKLIST", "PROHIBITED_CONTENT", "SPII"}:
        return "end_turn"
    if "MALFORMED" in reason:
        return "error"
    return "tool_use" if has_tools else "end_turn"


class GeminiProvider(LLMProvider):
    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: float = 600.0,
    ):
        self._api_key = api_key or os.environ.get("GEMINI_API_KEY", "")
        self._base_url = (base_url or "https://generativelanguage.googleapis.com/v1beta").rstrip("/")
        self._client = httpx.AsyncClient(
            timeout=timeout,
            headers={
                "x-goog-api-key": self._api_key,
                "Content-Type": "application/json",
            },
        )

    @property
    def name(self) -> str:
        return "gemini"

    async def list_models(self) -> list[str]:
        try:
            resp = await self._client.get(f"{self._base_url}/models")
            resp.raise_for_status()
            data = resp.json()
        except Exception:
            return []

        models: list[str] = []
        for item in data.get("models", []):
            methods = set(item.get("supportedGenerationMethods") or [])
            if methods and "generateContent" not in methods:
                continue
            name = item.get("name", "")
            if name.startswith("models/"):
                name = name.split("/", 1)[1]
            if name:
                models.append(name)
        return sorted(set(models))

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
        del thinking

        payload: dict[str, Any] = {
            "contents": _messages_to_gemini(messages),
            "generationConfig": {
                "maxOutputTokens": max_tokens,
            },
        }
        sys_part = _system_to_gemini(system)
        if sys_part:
            payload["systemInstruction"] = sys_part
        if temperature is not None:
            payload["generationConfig"]["temperature"] = temperature
        if tools:
            payload["tools"] = _tools_to_gemini(tools)
            payload["toolConfig"] = {
                "functionCallingConfig": {
                    "mode": "AUTO",
                }
            }

        url = f"{self._base_url}/models/{model}:streamGenerateContent?alt=sse"
        emitted_text = ""
        emitted_tool_ids: set[str] = set()
        last_usage: dict[str, Any] = {}
        final_reason: str | None = None
        saw_tool_call = False

        try:
            async with self._client.stream("POST", url, json=payload) as resp:
                if resp.status_code >= 400:
                    await resp.aread()
                    error_text = resp.text or f"HTTP {resp.status_code}"
                    yield StreamChunk(type=ChunkType.ERROR, text=error_text)
                    yield StreamChunk(type=ChunkType.DONE, stop_reason="error")
                    return
                async for line in resp.aiter_lines():
                    line = line.strip()
                    if not line or not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        continue
                    try:
                        chunk = json.loads(data)
                    except json.JSONDecodeError:
                        continue

                    candidates = chunk.get("candidates") or []
                    if candidates:
                        cand = candidates[0]
                        final_reason = cand.get("finishReason") or final_reason
                        content = cand.get("content") or {}
                        for part in content.get("parts") or []:
                            text = part.get("text")
                            if text:
                                delta = text[len(emitted_text):] if text.startswith(emitted_text) else text
                                emitted_text = text if text.startswith(emitted_text) else emitted_text + text
                                if delta:
                                    yield StreamChunk(type=ChunkType.TEXT, text=delta)
                            fc = part.get("functionCall")
                            if fc:
                                tool_id = fc.get("id") or fc.get("name", "")
                                if tool_id in emitted_tool_ids:
                                    continue
                                emitted_tool_ids.add(tool_id)
                                saw_tool_call = True
                                meta: dict[str, Any] = {}
                                sig = part.get("thoughtSignature")
                                if sig:
                                    meta["thoughtSignature"] = sig
                                tool_call = ToolCallContent(
                                    id=fc.get("id", ""),
                                    name=fc.get("name", ""),
                                    input=fc.get("args") or {},
                                    metadata=meta,
                                )
                                yield StreamChunk(type=ChunkType.TOOL_CALL_START, tool_call=tool_call)
                                yield StreamChunk(type=ChunkType.TOOL_CALL_END, tool_call=tool_call)

                    usage = chunk.get("usageMetadata")
                    if usage:
                        last_usage = usage
        except Exception as e:
            yield StreamChunk(type=ChunkType.ERROR, text=str(e))
            yield StreamChunk(type=ChunkType.DONE, stop_reason="error")
            return

        yield StreamChunk(
            type=ChunkType.DONE,
            stop_reason=_finish_reason(final_reason, saw_tool_call),
            input_tokens=last_usage.get("promptTokenCount", 0) or 0,
            output_tokens=last_usage.get("candidatesTokenCount", 0) or 0,
            cache_read_tokens=last_usage.get("cachedContentTokenCount", 0) or 0,
        )

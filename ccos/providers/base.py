"""Core types and protocol for LLM providers.

Every provider (Anthropic, OpenAI, Ollama, …) implements the ``LLMProvider``
protocol so the rest of the system is completely model-agnostic.
"""

from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, AsyncIterator, Literal


# ---------------------------------------------------------------------------
# Content block types (provider-agnostic)
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class TextContent:
    type: Literal["text"] = "text"
    text: str = ""


@dataclass(slots=True)
class ImageContent:
    type: Literal["image"] = "image"
    source_type: Literal["base64", "url"] = "base64"
    media_type: str = "image/png"
    data: str = ""  # base64 or url


@dataclass(slots=True)
class ToolCallContent:
    """A tool invocation block inside an assistant message."""
    type: Literal["tool_use"] = "tool_use"
    id: str = ""
    name: str = ""
    input: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ToolResultContent:
    """Result sent back to the model after tool execution."""
    type: Literal["tool_result"] = "tool_result"
    tool_use_id: str = ""
    content: str | list[TextContent | ImageContent] = ""
    is_error: bool = False


@dataclass(slots=True)
class ThinkingContent:
    type: Literal["thinking"] = "thinking"
    thinking: str = ""
    signature: str = ""


ContentBlock = TextContent | ImageContent | ToolCallContent | ToolResultContent | ThinkingContent


# ---------------------------------------------------------------------------
# Message
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class Message:
    role: Literal["user", "assistant"]
    content: str | list[ContentBlock] = ""


# ---------------------------------------------------------------------------
# Tool schema (sent to any provider)
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class ToolSchema:
    name: str
    description: str
    input_schema: dict[str, Any]


# ---------------------------------------------------------------------------
# Thinking configuration
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class ThinkingConfig:
    enabled: bool = False
    budget_tokens: int = 10_000


# ---------------------------------------------------------------------------
# Streaming types
# ---------------------------------------------------------------------------

class ChunkType(str, Enum):
    TEXT = "text"
    THINKING = "thinking"
    TOOL_CALL_START = "tool_call_start"
    TOOL_CALL_DELTA = "tool_call_delta"
    TOOL_CALL_END = "tool_call_end"
    DONE = "done"
    ERROR = "error"


@dataclass(slots=True)
class StreamChunk:
    """Incremental piece of a streaming LLM response."""
    type: ChunkType
    text: str | None = None
    tool_call: ToolCallContent | None = None
    stop_reason: str | None = None
    # Token usage (only on DONE)
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0


# ---------------------------------------------------------------------------
# Completed response (assembled from stream)
# ---------------------------------------------------------------------------

@dataclass
class LLMResponse:
    """Fully assembled response from the model."""
    content: list[ContentBlock] = field(default_factory=list)
    stop_reason: str = "end_turn"
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0

    # Convenience ---------------------------------------------------------

    def get_text(self) -> str:
        parts: list[str] = []
        for block in self.content:
            if isinstance(block, TextContent):
                parts.append(block.text)
        return "".join(parts)

    def get_tool_calls(self) -> list[ToolCallContent]:
        return [b for b in self.content if isinstance(b, ToolCallContent)]

    def has_tool_calls(self) -> bool:
        return any(isinstance(b, ToolCallContent) for b in self.content)


# ---------------------------------------------------------------------------
# ToolCall helper (for ToolResult pairing)
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class ToolCall:
    id: str
    name: str
    input: dict[str, Any]


def make_tool_call_id() -> str:
    return f"toolu_{uuid.uuid4().hex[:24]}"


# ---------------------------------------------------------------------------
# Provider protocol
# ---------------------------------------------------------------------------

class LLMProvider(ABC):
    """Abstract base for all LLM providers."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable provider name (e.g. 'anthropic', 'openai')."""

    @property
    def supports_tool_use(self) -> bool:
        return True

    @property
    def supports_streaming(self) -> bool:
        return True

    @property
    def supports_thinking(self) -> bool:
        return False

    @property
    def supports_system_prompt(self) -> bool:
        return True

    async def list_models(self) -> list[str]:
        """Query available models from the provider API.

        Returns sorted list of model IDs. Empty list if API unavailable.
        """
        return []

    @abstractmethod
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
        """Stream a model response, yielding ``StreamChunk`` objects.

        The caller assembles chunks into a complete ``LLMResponse``.
        """
        ...  # pragma: no cover
        # Make this an async generator
        if False:
            yield  # type: ignore[misc]

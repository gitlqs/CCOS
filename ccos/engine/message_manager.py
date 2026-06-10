"""Message history management with context compaction support."""

from __future__ import annotations

from typing import Any

from ccos.providers.base import (
    ContentBlock,
    LLMResponse,
    Message,
    TextContent,
    ToolCallContent,
    ToolResultContent,
)

# Approximate token-to-character ratio (conservative)
_CHARS_PER_TOKEN = 4

# Context-window accounting, mirroring cc's autoCompact.ts.
# cc uses a flat 200k context window for all current models
# (MODEL_CONTEXT_WINDOW_DEFAULT) and reserves up to 20k tokens for the
# compaction summary output (MAX_OUTPUT_TOKENS_FOR_SUMMARY).
_MODEL_CONTEXT_WINDOW_DEFAULT = 200_000
_MAX_OUTPUT_TOKENS_FOR_SUMMARY = 20_000

# Buffers below the effective context window (cc/src/services/compact/autoCompact.ts).
_AUTOCOMPACT_BUFFER_TOKENS = 13_000   # AUTOCOMPACT_BUFFER_TOKENS
_MANUAL_COMPACT_BUFFER_TOKENS = 3_000  # MANUAL_COMPACT_BUFFER_TOKENS

# Per-model context windows (input tokens). cc treats all current models as
# 200k unless a 1M-context variant is in use; we key on a canonical short name.
_MODEL_CONTEXT_WINDOWS: dict[str, int] = {}

# Per-model max output tokens. cc's getMaxOutputTokensForModel returns 32k by
# default (capped to 64k); only min(maxOutput, 20k) is reserved, so any value
# >= 20k yields the same reservation.
_DEFAULT_MAX_OUTPUT_TOKENS = 32_000

# Back-compat: kept so callers passing an explicit budget still work, but the
# auto-compact decision is now derived from the model's effective window.
_DEFAULT_MAX_CONTEXT_TOKENS = _MODEL_CONTEXT_WINDOW_DEFAULT


def _context_window_for_model(model: str | None) -> int:
    """Return the context window (in tokens) for a model, defaulting to 200k.

    Mirrors cc's getContextWindowForModel: a flat default with a 1M bump for
    explicitly 1M-capable variants (Sonnet 4.x / Opus 4.6 marked ``[1m]``).
    """
    if not model:
        return _MODEL_CONTEXT_WINDOW_DEFAULT
    name = model.lower()
    if "[1m]" in name:
        return 1_000_000
    return _MODEL_CONTEXT_WINDOWS.get(model, _MODEL_CONTEXT_WINDOW_DEFAULT)


def _max_output_tokens_for_model(model: str | None) -> int:
    """Return the max output token budget for a model (cc default 32k)."""
    return _DEFAULT_MAX_OUTPUT_TOKENS


def effective_context_window(model: str | None) -> int:
    """Context window minus the tokens reserved for a compaction summary.

    cc: getEffectiveContextWindowSize = contextWindow - min(maxOutput, 20_000).
    """
    reserved = min(_max_output_tokens_for_model(model), _MAX_OUTPUT_TOKENS_FOR_SUMMARY)
    return _context_window_for_model(model) - reserved


def autocompact_threshold(model: str | None) -> int:
    """Token threshold at which auto-compaction should trigger.

    cc: getAutoCompactThreshold = effectiveContextWindow - AUTOCOMPACT_BUFFER_TOKENS.
    """
    return effective_context_window(model) - _AUTOCOMPACT_BUFFER_TOKENS


def blocking_limit(model: str | None) -> int:
    """Hard token limit above which a request must not be sent.

    cc: effectiveContextWindow - MANUAL_COMPACT_BUFFER_TOKENS (3_000).
    """
    return effective_context_window(model) - _MANUAL_COMPACT_BUFFER_TOKENS


def _estimate_message_tokens(msg: Message) -> int:
    """Rough token estimate for a message."""
    if isinstance(msg.content, str):
        return len(msg.content) // _CHARS_PER_TOKEN + 4
    total = 4  # message overhead
    for block in msg.content:
        if isinstance(block, TextContent):
            total += len(block.text) // _CHARS_PER_TOKEN
        elif isinstance(block, ToolCallContent):
            total += len(str(block.input)) // _CHARS_PER_TOKEN + 20
        elif isinstance(block, ToolResultContent):
            total += len(block.content) // _CHARS_PER_TOKEN + 10
        else:
            total += 20  # unknown block
    return total


class MessageManager:
    """Maintain conversation message history with compaction support."""

    def __init__(self, max_context_tokens: int = _DEFAULT_MAX_CONTEXT_TOKENS) -> None:
        self.messages: list[Message] = []
        self.max_context_tokens = max_context_tokens
        self._compaction_summary: str | None = None  # Summary from compaction
        # Real token usage from the most recent LLMResponse, preferred over the
        # chars//4 estimate when deciding whether to compact.
        self._last_usage_tokens: int | None = None

    def add_user(self, content: str | list[Any]) -> None:
        self.messages.append(Message(role="user", content=content))

    def add_assistant_response(self, response: LLMResponse) -> None:
        self.messages.append(Message(role="assistant", content=response.content))
        # Capture real context size from the provider when available. The
        # input_tokens reflect everything the model just saw (system + history),
        # which is a better signal than our char//4 estimate.
        usage = (response.input_tokens or 0) + (response.cache_read_tokens or 0) + (
            response.cache_creation_tokens or 0
        )
        if usage > 0:
            self._last_usage_tokens = usage

    def add_tool_results(self, results: list[ToolResultContent]) -> None:
        """Add tool results as a user message (API convention)."""
        if not results:
            return
        self.messages.append(Message(role="user", content=results))  # type: ignore[arg-type]

    def to_api_format(self) -> list[Message]:
        """Return messages ready for the provider."""
        return list(self.messages)

    def clear(self) -> None:
        self.messages.clear()
        self._compaction_summary = None
        self._last_usage_tokens = None

    def current_token_estimate(self) -> int:
        """Best available token count: real usage if known, else chars//4."""
        estimate = self.estimate_total_tokens()
        if self._last_usage_tokens is not None:
            # Prefer the larger of the two so we never under-count after adding
            # new messages since the last response.
            return max(self._last_usage_tokens, estimate)
        return estimate

    def get_turn_count(self) -> int:
        return sum(1 for m in self.messages if m.role == "user" and isinstance(m.content, str))

    def get_last_assistant_text(self) -> str:
        for msg in reversed(self.messages):
            if msg.role == "assistant":
                if isinstance(msg.content, str):
                    return msg.content
                parts = []
                for b in msg.content:
                    if isinstance(b, TextContent):
                        parts.append(b.text)
                return "".join(parts)
        return ""

    def estimate_total_tokens(self) -> int:
        """Estimate total tokens across all messages."""
        return sum(_estimate_message_tokens(m) for m in self.messages)

    def needs_compaction(self, model: str | None = None) -> bool:
        """Check if messages have crossed the model-aware auto-compact threshold.

        Mirrors cc's calculateTokenWarningState.isAboveAutoCompactThreshold:
        trigger when the token count reaches
        ``effective_context_window(model) - AUTOCOMPACT_BUFFER_TOKENS``.
        """
        return self.current_token_estimate() >= autocompact_threshold(model)

    def is_at_blocking_limit(self, model: str | None = None) -> bool:
        """Check whether the context is at cc's hard blocking limit.

        Above ``effective_context_window(model) - MANUAL_COMPACT_BUFFER_TOKENS``
        a request would overflow the context window. cc surfaces a clean
        prompt-too-long error here rather than letting the provider 400.
        """
        return self.current_token_estimate() >= blocking_limit(model)

    def compact(self, summary: str) -> int:
        """Replace old messages with a compaction summary.

        Keeps the most recent turns intact and replaces older conversation
        with a summary. Returns the number of messages removed.

        Args:
            summary: LLM-generated summary of the conversation so far.
        """
        if len(self.messages) <= 4:
            return 0  # Nothing meaningful to compact

        # Keep the last 4 messages (2 turns) intact
        keep_count = 4
        old_messages = self.messages[:-keep_count]
        recent_messages = self.messages[-keep_count:]

        if not old_messages:
            return 0

        removed = len(old_messages)

        # Build compaction marker
        compact_text = (
            f"[This conversation was compacted. Previous context summary:\n\n"
            f"{summary}\n\n"
            f"End of summary. The conversation continues below.]"
        )

        # Replace with summary + recent messages
        self.messages = [
            Message(role="user", content=compact_text),
            Message(role="assistant", content="Understood. I have the context from the summary above. Let me continue."),
            *recent_messages,
        ]

        self._compaction_summary = summary
        return removed

    def get_compact_prompt(self) -> str:
        """Generate a prompt asking the LLM to summarize the conversation for compaction."""
        # Collect all text from messages to summarize
        parts = []
        for msg in self.messages:
            role = msg.role
            if isinstance(msg.content, str):
                parts.append(f"[{role}]: {msg.content[:2000]}")
            elif isinstance(msg.content, list):
                for block in msg.content:
                    if isinstance(block, TextContent):
                        parts.append(f"[{role}]: {block.text[:1000]}")
                    elif isinstance(block, ToolCallContent):
                        parts.append(f"[{role} tool_use]: {block.name}({str(block.input)[:500]})")
                    elif isinstance(block, ToolResultContent):
                        parts.append(f"[tool_result {block.tool_use_id}]: {block.content[:500]}")

        conversation_text = "\n".join(parts[-50:])  # Last 50 entries max

        return (
            "Please provide a concise summary of this conversation so far. "
            "Focus on:\n"
            "1. What the user originally asked for\n"
            "2. What has been accomplished so far\n"
            "3. Key decisions made and their reasoning\n"
            "4. Any pending tasks or issues\n"
            "5. Important file paths, function names, or technical details mentioned\n\n"
            "Keep the summary under 2000 characters. Be specific about technical details.\n\n"
            f"Conversation:\n{conversation_text}"
        )

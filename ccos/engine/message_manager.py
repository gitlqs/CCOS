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
# Default context budget (leave room for system prompt + response)
_DEFAULT_MAX_CONTEXT_TOKENS = 180_000
_COMPACT_THRESHOLD = 0.75  # Compact when we hit 75% of budget


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

    def add_user(self, text: str) -> None:
        self.messages.append(Message(role="user", content=text))

    def add_assistant_response(self, response: LLMResponse) -> None:
        self.messages.append(Message(role="assistant", content=response.content))

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

    def needs_compaction(self) -> bool:
        """Check if messages are approaching the context limit."""
        threshold = int(self.max_context_tokens * _COMPACT_THRESHOLD)
        return self.estimate_total_tokens() > threshold

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

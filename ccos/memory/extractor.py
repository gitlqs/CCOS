"""Background memory extractor — auto-extracts memories from conversation.

After each turn where the model gives a final reply (no tool_use), a background
sub-agent analyzes the recent messages and extracts any memories worth persisting.

Key behaviors:
- Skips extraction if the main agent already wrote memories this turn
- Scans existing memories first to avoid duplicates
- Has limited tool permissions (read + grep + write to memory dir only)
- Turn-budget limited: read all in round 1, write all in round 2
"""

from __future__ import annotations

import asyncio
from typing import Any, Callable, TYPE_CHECKING

from ccos.memory.store import MemoryStore

if TYPE_CHECKING:
    from ccos.engine.query_engine import QueryEngine
    from ccos.providers.base import Message


# Minimum thresholds before extraction is considered
MIN_MESSAGES_FOR_EXTRACTION = 6  # At least 3 user + 3 assistant turns
MIN_TOOL_CALLS_FOR_EXTRACTION = 2  # At least some tool interaction


class MemoryExtractor:
    """Manages automatic memory extraction from conversations."""

    def __init__(
        self,
        store: MemoryStore,
        engine_factory: Callable[..., QueryEngine] | None = None,
    ):
        self._store = store
        self._engine_factory = engine_factory
        self._extraction_count = 0

    def should_extract(
        self,
        messages: list[Any],
        has_memory_writes_since_last: bool,
    ) -> bool:
        """Decide whether to trigger background extraction.

        Args:
            messages: Current conversation messages.
            has_memory_writes_since_last: Whether the main agent wrote memories
                in the most recent turn. If so, skip extraction.
        """
        if has_memory_writes_since_last:
            return False

        if len(messages) < MIN_MESSAGES_FOR_EXTRACTION:
            return False

        # Count tool calls in recent messages
        tool_call_count = 0
        for msg in messages[-10:]:
            content = msg.content if hasattr(msg, "content") else msg.get("content", "")
            if isinstance(content, list):
                for block in content:
                    if hasattr(block, "type") and block.type == "tool_use":
                        tool_call_count += 1

        if tool_call_count < MIN_TOOL_CALLS_FOR_EXTRACTION:
            return False

        return True

    async def extract(self, messages: list[Any]) -> None:
        """Run background extraction using a sub-agent.

        This creates a lightweight QueryEngine that analyzes the conversation
        and writes any relevant memories to the store.
        """
        if not self._engine_factory:
            return

        # Scan existing memories for the sub-agent's context
        existing = self._store.scan_all()
        existing_summary = ""
        if existing:
            lines = []
            for e in existing:
                lines.append(f"- {e.name} ({e.type.value}): {e.description}")
            existing_summary = "\n".join(lines)

        memory_dir = str(self._store.memory_dir)

        # Build extraction prompt
        prompt = self._build_extraction_prompt(
            memory_dir=memory_dir,
            existing_memories=existing_summary,
        )

        try:
            engine = self._engine_factory()
            # Feed the extraction prompt as user input
            await engine.run_turn(prompt)
            self._extraction_count += 1
        except Exception:
            # Background extraction should never crash the main app
            pass

    def _build_extraction_prompt(
        self,
        memory_dir: str,
        existing_memories: str,
    ) -> str:
        parts = [
            "You are a memory extraction agent. Your job is to analyze the recent "
            "conversation and extract any information worth remembering for future "
            "conversations.",
            "",
            f"Memory directory: {memory_dir}",
            "",
            "## Existing memories:",
            existing_memories or "(none)",
            "",
            "## Rules:",
            "1. Only save truly useful information that will help in future conversations.",
            "2. Do NOT save: code patterns, architecture, file paths, git history, "
            "debugging solutions, or ephemeral task details.",
            "3. DO save: user preferences, feedback on your approach, project context "
            "not derivable from code, references to external systems.",
            "4. Check existing memories before writing — update rather than duplicate.",
            "5. Each memory file needs YAML frontmatter with name, description, type.",
            "6. After writing memory files, update MEMORY.md index.",
            "7. Keep it minimal — only extract what is genuinely non-obvious and useful.",
            "",
            "If there is nothing worth extracting, simply say 'No new memories to extract.' "
            "and stop.",
        ]
        return "\n".join(parts)

    def run_background(self, messages: list[Any]) -> None:
        """Fire-and-forget extraction in a background thread.

        This is safe to call from the main event loop — it spawns a new thread
        with its own event loop.
        """
        import threading

        def _run() -> None:
            try:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                loop.run_until_complete(self.extract(messages))
                loop.close()
            except Exception:
                pass

        t = threading.Thread(target=_run, daemon=True)
        t.start()

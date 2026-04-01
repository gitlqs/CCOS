"""Intelligent memory recall — uses LLM to select relevant memories.

When a conversation involves a topic that may benefit from recalled memories,
this module queries a lightweight LLM to choose the most relevant entries
from the memory manifest.

The manifest contains only frontmatter (name, description, type) — NOT full
content. The LLM selects up to `max_results` entries, and the system then
loads their full content for injection into the conversation context.
"""

from __future__ import annotations

from typing import Any, Callable, TYPE_CHECKING

from ccos.memory.store import MemoryEntry, MemoryStore

if TYPE_CHECKING:
    from ccos.engine.query_engine import QueryEngine


MAX_RECALL_RESULTS = 5


class MemoryRecall:
    """Select and format relevant memories for prompt injection."""

    def __init__(self, store: MemoryStore):
        self._store = store

    def build_manifest(self) -> list[MemoryEntry]:
        """Scan all memories and return as manifest (headers only)."""
        return self._store.scan_all()

    def format_manifest_for_llm(self, entries: list[MemoryEntry]) -> str:
        """Format the manifest as a concise list for the selector LLM."""
        lines: list[str] = []
        for i, e in enumerate(entries):
            age_str = f"{int(e.age_days)}d" if e.age_days >= 1 else "<1d"
            lines.append(
                f"{i + 1}. [{e.type.value}] {e.name} — {e.description} (age: {age_str})"
            )
        return "\n".join(lines) if lines else "(no memories available)"

    async def find_relevant(
        self,
        query: str,
        engine_factory: Callable[..., QueryEngine] | None = None,
        max_results: int = MAX_RECALL_RESULTS,
    ) -> list[MemoryEntry]:
        """Use LLM to select the most relevant memories for a query.

        If no engine_factory is available, falls back to keyword matching.

        Args:
            query: The user's message or topic.
            engine_factory: Factory to create a lightweight QueryEngine for the side-query.
            max_results: Maximum number of memories to return.

        Returns:
            List of MemoryEntry objects with full content loaded.
        """
        manifest = self.build_manifest()
        if not manifest:
            return []

        # If we have few memories, just return all
        if len(manifest) <= max_results:
            return [
                self._store.load_entry(e.file_path.split("/")[-1]) or e
                for e in manifest
            ]

        # Try LLM-based selection
        if engine_factory:
            try:
                selected_indices = await self._llm_select(
                    query, manifest, engine_factory, max_results
                )
                if selected_indices:
                    return self._load_selected(manifest, selected_indices)
            except Exception:
                pass

        # Fallback: keyword matching
        return self._keyword_select(query, manifest, max_results)

    async def _llm_select(
        self,
        query: str,
        manifest: list[MemoryEntry],
        engine_factory: Callable[..., QueryEngine],
        max_results: int,
    ) -> list[int]:
        """Use a side-query LLM call to select relevant memory indices."""
        manifest_text = self.format_manifest_for_llm(manifest)

        prompt = (
            "You are a memory relevance selector. Given a user query and a list of "
            "available memories, select the most relevant ones.\n\n"
            f"User query: {query}\n\n"
            f"Available memories:\n{manifest_text}\n\n"
            f"Select up to {max_results} memories most relevant to the query. "
            "Return ONLY the numbers (comma-separated), nothing else. "
            "If none are relevant, return 'none'."
        )

        engine = engine_factory()
        result = await engine.run_turn(prompt)

        # Parse the response
        response_text = result.get_text() if hasattr(result, "get_text") else str(result)
        return self._parse_selection(response_text, len(manifest))

    def _parse_selection(self, response: str, max_index: int) -> list[int]:
        """Parse LLM response into list of 0-based indices."""
        if "none" in response.lower():
            return []

        indices: list[int] = []
        import re
        numbers = re.findall(r"\d+", response)
        for n in numbers:
            idx = int(n) - 1  # Convert 1-based to 0-based
            if 0 <= idx < max_index:
                indices.append(idx)

        return indices[:MAX_RECALL_RESULTS]

    def _keyword_select(
        self,
        query: str,
        manifest: list[MemoryEntry],
        max_results: int,
    ) -> list[MemoryEntry]:
        """Simple keyword-based fallback for memory selection."""
        query_lower = query.lower()
        words = set(query_lower.split())

        scored: list[tuple[float, MemoryEntry]] = []
        for entry in manifest:
            text = f"{entry.name} {entry.description}".lower()
            # Count matching words
            matches = sum(1 for w in words if w in text and len(w) > 2)
            if matches > 0:
                # Boost by recency
                recency = max(0, 1.0 - entry.age_days / 30.0)
                score = matches + recency * 0.5
                scored.append((score, entry))

        scored.sort(key=lambda x: x[0], reverse=True)

        results: list[MemoryEntry] = []
        for _, entry in scored[:max_results]:
            filename = entry.file_path.split("/")[-1] if entry.file_path else entry.filename
            loaded = self._store.load_entry(filename)
            if loaded:
                results.append(loaded)

        return results

    def _load_selected(
        self,
        manifest: list[MemoryEntry],
        indices: list[int],
    ) -> list[MemoryEntry]:
        """Load full content for selected entries."""
        results: list[MemoryEntry] = []
        for idx in indices:
            if 0 <= idx < len(manifest):
                entry = manifest[idx]
                filename = entry.file_path.split("/")[-1] if entry.file_path else entry.filename
                loaded = self._store.load_entry(filename)
                if loaded:
                    results.append(loaded)
        return results

    def format_for_prompt(self, entries: list[MemoryEntry]) -> str:
        """Format recalled memories for injection into conversation context.

        Each entry includes its content and any age warnings.
        """
        if not entries:
            return ""

        parts: list[str] = []
        parts.append("# Recalled Memories\n")
        parts.append(
            "The following memories were recalled as potentially relevant. "
            "Verify any claims about files, functions, or flags before relying on them.\n"
        )

        for entry in entries:
            header = f"## [{entry.type.value}] {entry.name}"
            age_warning = MemoryStore.get_age_warning(entry)
            section = f"{header}\n"
            if age_warning:
                section += f"_{age_warning}_\n\n"
            section += entry.content
            parts.append(section)

        return "\n\n".join(parts)

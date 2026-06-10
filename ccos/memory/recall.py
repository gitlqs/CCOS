"""Intelligent memory recall — uses an LLM to select relevant memories.

When a conversation involves a topic that may benefit from recalled memories,
this module queries a lightweight LLM to choose the most relevant entries from
the memory manifest.

The manifest contains only frontmatter (filename, description, type) — NOT full
content. The selector returns FILENAMES (validated against the manifest), and
the system then loads their full content for injection into the conversation
context. Each recalled memory is surfaced as a separate system-reminder meta
message with a per-memory header carrying the file path and freshness, mirroring
cc's attachments.ts memoryHeader / messages.ts relevant_memories handling.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, TYPE_CHECKING

from ccos.memory.store import MemoryEntry, MemoryStore

if TYPE_CHECKING:
    from ccos.engine.query_engine import QueryEngine


MAX_RECALL_RESULTS = 5

# Verbatim from cc/src/memdir/findRelevantMemories.ts SELECT_MEMORIES_SYSTEM_PROMPT.
SELECT_MEMORIES_SYSTEM_PROMPT = (
    "You are selecting memories that will be useful to Claude Code as it processes a user's "
    "query. You will be given the user's query and a list of available memory files with their "
    "filenames and descriptions.\n\n"
    "Return a list of filenames for the memories that will clearly be useful to Claude Code as "
    "it processes the user's query (up to 5). Only include memories that you are certain will be "
    "helpful based on their name and description.\n"
    "- If you are unsure if a memory will be useful in processing the user's query, then do not "
    "include it in your list. Be selective and discerning.\n"
    "- If there are no memories in the list that would clearly be useful, feel free to return an "
    "empty list.\n"
    "- If a list of recently-used tools is provided, do not select memories that are usage "
    "reference or API documentation for those tools (Claude Code is already exercising them). DO "
    "still select memories containing warnings, gotchas, or known issues about those tools — "
    "active use is exactly when those matter.\n"
)


@dataclass
class RecalledMemory:
    """A recalled memory ready for injection, with its rendered header."""
    entry: MemoryEntry
    header: str
    text: str  # "<header>\n\n<content>" — the body of the system-reminder


class MemoryRecall:
    """Select and format relevant memories for prompt injection."""

    def __init__(self, store: MemoryStore):
        self._store = store

    def build_manifest(self) -> list[MemoryEntry]:
        """Scan all memories and return as manifest (headers only)."""
        return self._store.scan_all()

    def format_manifest_for_llm(self, entries: list[MemoryEntry]) -> str:
        """Format the manifest as cc's formatMemoryManifest:
        ``- [type] filename (ISO-timestamp): description`` (the ``[type]`` tag is
        omitted when the type is unknown/None).
        """
        lines: list[str] = []
        for e in entries:
            tag = f"[{e.type.value}] " if e.type else ""
            filename = self._manifest_filename(e)
            ts = (
                datetime.fromtimestamp(e.modified_time, tz=timezone.utc)
                .isoformat()
                .replace("+00:00", "Z")
                if e.modified_time > 0
                else ""
            )
            if e.description:
                lines.append(f"- {tag}{filename} ({ts}): {e.description}")
            else:
                lines.append(f"- {tag}{filename} ({ts})")
        return "\n".join(lines)

    @staticmethod
    def _manifest_filename(entry: MemoryEntry) -> str:
        if entry.file_path:
            import os

            return os.path.basename(entry.file_path)
        return entry.filename

    async def find_relevant(
        self,
        query: str,
        engine_factory: Callable[..., QueryEngine] | None = None,
        max_results: int = MAX_RECALL_RESULTS,
        recent_tools: list[str] | None = None,
    ) -> list[MemoryEntry]:
        """Use an LLM to select the most relevant memories for a query.

        If no engine_factory is available, falls back to keyword matching.

        Args:
            query: The user's message or topic.
            engine_factory: Factory to create a lightweight QueryEngine.
            max_results: Maximum number of memories to return.
            recent_tools: Recently-used tool names (threaded into the selector
                prompt to suppress usage-reference noise — see cc selector).

        Returns:
            List of MemoryEntry objects with full content loaded.
        """
        manifest = self.build_manifest()
        if not manifest:
            return []

        # If we have few memories, just return all
        if len(manifest) <= max_results:
            return [self._load(e) for e in manifest if self._load(e)]

        # Try LLM-based selection
        if engine_factory:
            try:
                selected = await self._llm_select(
                    query, manifest, engine_factory, recent_tools or []
                )
                if selected:
                    return self._load_selected(manifest, selected)
            except Exception:
                pass

        # Fallback: keyword matching
        return self._keyword_select(query, manifest, max_results)

    async def _llm_select(
        self,
        query: str,
        manifest: list[MemoryEntry],
        engine_factory: Callable[..., QueryEngine],
        recent_tools: list[str],
    ) -> list[str]:
        """Side-query the selector LLM and return validated filenames."""
        manifest_text = self.format_manifest_for_llm(manifest)
        valid_filenames = {self._manifest_filename(e) for e in manifest}

        tools_section = (
            f"\n\nRecently used tools: {', '.join(recent_tools)}"
            if recent_tools
            else ""
        )

        prompt = (
            f"{SELECT_MEMORIES_SYSTEM_PROMPT}\n"
            f"Query: {query}\n\n"
            f"Available memories:\n{manifest_text}{tools_section}\n\n"
            'Respond with ONLY a JSON object of the form '
            '{"selected_memories": ["filename.md", ...]} (up to 5 filenames, '
            "exactly as they appear above). Return an empty list if none clearly apply."
        )

        engine = engine_factory()
        result = await engine.run_turn(prompt)
        response_text = result.get_text() if hasattr(result, "get_text") else str(result)
        return self._parse_selected_filenames(response_text, valid_filenames)

    @staticmethod
    def _parse_selected_filenames(response: str, valid: set[str]) -> list[str]:
        """Extract selected_memories filenames from the model's JSON output,
        validated against the manifest's filenames."""
        import json
        import re

        candidates: list[str] = []
        # Prefer a structured object if present.
        match = re.search(r"\{.*\}", response, re.DOTALL)
        if match:
            try:
                obj = json.loads(match.group(0))
                sel = obj.get("selected_memories", [])
                if isinstance(sel, list):
                    candidates = [str(s) for s in sel]
            except (ValueError, AttributeError):
                candidates = []
        if not candidates:
            # Last-resort: pick any manifest filenames mentioned verbatim.
            candidates = [f for f in valid if f in response]

        seen: set[str] = set()
        out: list[str] = []
        for f in candidates:
            if f in valid and f not in seen:
                seen.add(f)
                out.append(f)
        return out[:MAX_RECALL_RESULTS]

    def _keyword_select(
        self,
        query: str,
        manifest: list[MemoryEntry],
        max_results: int,
    ) -> list[MemoryEntry]:
        """Simple keyword-based fallback for memory selection (no-LLM path)."""
        query_lower = query.lower()
        words = set(query_lower.split())

        scored: list[tuple[float, MemoryEntry]] = []
        for entry in manifest:
            text = f"{entry.name} {entry.description}".lower()
            matches = sum(1 for w in words if w in text and len(w) > 2)
            if matches > 0:
                recency = max(0.0, 1.0 - entry.age_days / 30.0)
                score = matches + recency * 0.5
                scored.append((score, entry))

        scored.sort(key=lambda x: x[0], reverse=True)

        results: list[MemoryEntry] = []
        for _, entry in scored[:max_results]:
            loaded = self._load(entry)
            if loaded:
                results.append(loaded)
        return results

    def _load(self, entry: MemoryEntry) -> MemoryEntry | None:
        filename = self._manifest_filename(entry)
        return self._store.load_entry(filename)

    def _load_selected(
        self,
        manifest: list[MemoryEntry],
        filenames: list[str],
    ) -> list[MemoryEntry]:
        """Load full content for the selected filenames, in selection order."""
        by_name = {self._manifest_filename(e): e for e in manifest}
        results: list[MemoryEntry] = []
        for fname in filenames:
            entry = by_name.get(fname)
            if entry is None:
                continue
            loaded = self._store.load_entry(fname)
            if loaded:
                results.append(loaded)
        return results

    # -- Injection formatting ------------------------------------------------

    def build_recalled(self, entries: list[MemoryEntry]) -> list[RecalledMemory]:
        """Render each recalled memory with cc's per-memory header.

        For fresh memories (<= 1 day): ``Memory (saved <age>): <path>:``.
        For older memories: the freshness caveat, a blank line, then
        ``Memory: <path>:``. The body is ``<header>\\n\\n<content>``. Each item
        is meant to be wrapped in its own <system-reminder> meta message at the
        call site (cc messages.ts relevant_memories). There is no shared
        ``# Recalled Memories`` wrapper or preamble.
        """
        recalled: list[RecalledMemory] = []
        for entry in entries:
            header = self.memory_header(entry.file_path, entry.modified_time)
            text = f"{header}\n\n{entry.content}"
            recalled.append(RecalledMemory(entry=entry, header=header, text=text))
        return recalled

    @staticmethod
    def memory_header(path: str, modified_time: float) -> str:
        """Per-memory header (cc attachments.ts memoryHeader)."""
        freshness = MemoryStore.memory_freshness_text(modified_time)
        if freshness:
            return f"{freshness}\n\nMemory: {path}:"
        age = MemoryStore.memory_age(modified_time)
        return f"Memory (saved {age}): {path}:"

    def format_for_prompt(self, entries: list[MemoryEntry]) -> str:
        """Render recalled memories as system-reminder meta messages.

        Each memory becomes its own ``<system-reminder>`` block (cc wraps each
        relevant_memories entry individually). Returns the concatenated blocks,
        or '' when there is nothing to inject.
        """
        recalled = self.build_recalled(entries)
        if not recalled:
            return ""
        blocks = [f"<system-reminder>\n{r.text}\n</system-reminder>" for r in recalled]
        return "\n\n".join(blocks)

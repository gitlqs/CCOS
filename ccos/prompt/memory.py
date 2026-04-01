"""Load CLAUDE.md / CCOS.md memory files and auto-memory index."""

from __future__ import annotations

import os


_MEMORY_FILENAMES = ["CLAUDE.md", "CCOS.md", ".claude/CLAUDE.md"]


def load_claude_md(cwd: str) -> str:
    """Load project memory files if they exist."""
    parts: list[str] = []

    for name in _MEMORY_FILENAMES:
        path = os.path.join(cwd, name)
        if os.path.isfile(path):
            try:
                content = open(path, "r", encoding="utf-8").read().strip()
                if content:
                    parts.append(f"## {name}\n\n{content}")
            except OSError:
                continue

    # Also check home dir
    home_claude = os.path.expanduser("~/.claude/CLAUDE.md")
    if os.path.isfile(home_claude):
        try:
            content = open(home_claude, "r", encoding="utf-8").read().strip()
            if content:
                parts.append(f"## ~/.claude/CLAUDE.md (global)\n\n{content}")
        except OSError:
            pass

    # Load auto-memory index (MEMORY.md)
    try:
        from ccos.memory.store import MemoryStore
        store = MemoryStore(cwd)
        index = store.load_index()
        if index:
            parts.append(f"## Auto-Memory Index\n\n{index}")
    except Exception:
        pass

    return "\n\n".join(parts)

"""Memory store — file-system backed structured memory with YAML frontmatter.

Memory files live at ``~/.ccos/projects/<project-hash>/memory/``.
Each is a Markdown file with YAML frontmatter (name, description, type).
MEMORY.md is an index file, not a memory itself.
"""

from __future__ import annotations

import hashlib
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ccos.memory.types import MemoryType

# Limits matching Claude Code
MAX_MEMORY_FILES = 200
MAX_INDEX_LINES = 200
MAX_INDEX_BYTES = 25_000
FRONTMATTER_SCAN_LINES = 30  # Only read first 30 lines for scanning


@dataclass
class MemoryEntry:
    """A single memory file's parsed content."""
    name: str
    description: str
    type: MemoryType
    content: str = ""
    file_path: str = ""
    modified_time: float = 0.0

    @property
    def age_days(self) -> float:
        if self.modified_time <= 0:
            return 0.0
        return (time.time() - self.modified_time) / 86400.0

    @property
    def filename(self) -> str:
        """Derive a safe filename from the name."""
        safe = re.sub(r"[^\w\-]", "_", self.name.lower()).strip("_")
        return f"{safe}.md"


class MemoryStore:
    """Manages the memory directory for a project."""

    def __init__(self, cwd: str):
        self._cwd = os.path.abspath(cwd)
        self._hash = self.project_hash(self._cwd)
        self._memory_dir = self._resolve_memory_dir()
        self._memory_dir.mkdir(parents=True, exist_ok=True)

    @property
    def memory_dir(self) -> Path:
        return self._memory_dir

    @property
    def index_path(self) -> Path:
        return self._memory_dir / "MEMORY.md"

    def _resolve_memory_dir(self) -> Path:
        base = Path(os.environ.get("CCOS_CONFIG_DIR", "~/.ccos")).expanduser()
        return base / "projects" / self._hash / "memory"

    @staticmethod
    def project_hash(cwd: str) -> str:
        return hashlib.sha256(os.path.abspath(cwd).encode()).hexdigest()[:16]

    # -- Scanning (frontmatter only, fast) -----------------------------------

    def scan_all(self) -> list[MemoryEntry]:
        """Scan all .md files in memory dir, parse frontmatter headers only.

        Returns entries sorted by modification time (newest first).
        Limited to MAX_MEMORY_FILES.
        """
        entries: list[MemoryEntry] = []
        if not self._memory_dir.exists():
            return entries

        for path in self._memory_dir.glob("*.md"):
            if path.name == "MEMORY.md":
                continue
            entry = self._parse_frontmatter(path)
            if entry:
                entries.append(entry)

        # Sort by modification time, newest first
        entries.sort(key=lambda e: e.modified_time, reverse=True)
        return entries[:MAX_MEMORY_FILES]

    def _parse_frontmatter(self, path: Path) -> MemoryEntry | None:
        """Parse YAML frontmatter from first 30 lines. Does NOT read full content."""
        try:
            lines: list[str] = []
            with open(path, "r", encoding="utf-8") as f:
                for i, line in enumerate(f):
                    if i >= FRONTMATTER_SCAN_LINES:
                        break
                    lines.append(line)
        except OSError:
            return None

        if not lines or lines[0].strip() != "---":
            return None

        # Find closing ---
        end_idx = -1
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                end_idx = i
                break
        if end_idx < 0:
            return None

        # Parse YAML fields manually (avoid pyyaml dependency)
        fm: dict[str, str] = {}
        for line in lines[1:end_idx]:
            m = re.match(r"^(\w+)\s*:\s*(.+)$", line.strip())
            if m:
                fm[m.group(1)] = m.group(2).strip().strip('"').strip("'")

        name = fm.get("name", path.stem)
        description = fm.get("description", "")
        type_str = fm.get("type", "project")

        try:
            mem_type = MemoryType(type_str)
        except ValueError:
            mem_type = MemoryType.PROJECT

        mtime = path.stat().st_mtime if path.exists() else 0.0

        return MemoryEntry(
            name=name,
            description=description,
            type=mem_type,
            content="",  # Not loaded during scan
            file_path=str(path),
            modified_time=mtime,
        )

    # -- Full entry loading --------------------------------------------------

    def load_entry(self, name_or_file: str) -> MemoryEntry | None:
        """Load a memory entry with full content."""
        # Try as filename first
        path = self._memory_dir / name_or_file
        if not path.exists():
            # Try deriving filename from name
            safe = re.sub(r"[^\w\-]", "_", name_or_file.lower()).strip("_")
            path = self._memory_dir / f"{safe}.md"
        if not path.exists():
            return None

        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            return None

        # Split frontmatter and content
        entry = self._parse_frontmatter(path)
        if not entry:
            return None

        # Extract content after frontmatter
        lines = text.split("\n")
        end_idx = -1
        if lines and lines[0].strip() == "---":
            for i in range(1, len(lines)):
                if lines[i].strip() == "---":
                    end_idx = i
                    break
        if end_idx >= 0:
            entry.content = "\n".join(lines[end_idx + 1:]).strip()
        else:
            entry.content = text.strip()

        return entry

    # -- Writing -------------------------------------------------------------

    def save_entry(self, entry: MemoryEntry) -> None:
        """Write a memory entry as a .md file with YAML frontmatter."""
        filename = entry.filename
        path = self._memory_dir / filename
        entry.file_path = str(path)

        frontmatter = (
            f"---\n"
            f"name: {entry.name}\n"
            f"description: {entry.description}\n"
            f"type: {entry.type.value}\n"
            f"---\n"
        )
        content = f"{frontmatter}\n{entry.content}\n"
        path.write_text(content, encoding="utf-8")
        entry.modified_time = path.stat().st_mtime

        # Update index after write
        self.update_index()

    def delete_entry(self, name_or_file: str) -> bool:
        """Delete a memory entry file. Returns True if deleted."""
        path = self._memory_dir / name_or_file
        if not path.exists():
            safe = re.sub(r"[^\w\-]", "_", name_or_file.lower()).strip("_")
            path = self._memory_dir / f"{safe}.md"
        if path.exists() and path.name != "MEMORY.md":
            path.unlink()
            self.update_index()
            return True
        return False

    # -- Index management ----------------------------------------------------

    def load_index(self) -> str:
        """Read MEMORY.md content. Truncate at 200 lines / 25KB."""
        if not self.index_path.exists():
            return ""
        try:
            text = self.index_path.read_text(encoding="utf-8")
        except OSError:
            return ""

        lines = text.split("\n")
        if len(lines) > MAX_INDEX_LINES:
            lines = lines[:MAX_INDEX_LINES]
            lines.append(
                f"\n⚠️ Index truncated at {MAX_INDEX_LINES} lines. "
                f"Some memories may not be listed."
            )
            text = "\n".join(lines)

        if len(text.encode("utf-8")) > MAX_INDEX_BYTES:
            text = text[:MAX_INDEX_BYTES]
            text += "\n⚠️ Index truncated at 25KB."

        return text

    def update_index(self) -> None:
        """Regenerate MEMORY.md from all memory files."""
        entries = self.scan_all()
        lines: list[str] = []
        for entry in entries:
            filename = os.path.basename(entry.file_path)
            desc = entry.description[:120] if entry.description else entry.name
            lines.append(f"- [{entry.name}]({filename}) — {desc}")

        content = "\n".join(lines) + "\n" if lines else ""
        self.index_path.write_text(content, encoding="utf-8")

    # -- Age warnings --------------------------------------------------------

    @staticmethod
    def get_age_warning(entry: MemoryEntry) -> str:
        """Return age warning string for stale memories."""
        days = entry.age_days
        if days < 1:
            return ""
        if days < 7:
            return (
                f"⚠️ This memory is {int(days)} day(s) old. "
                "Claims about code behavior or file:line citations may be outdated. "
                "Verify against current code before asserting as fact."
            )
        if days < 30:
            return (
                f"⚠️ This memory is {int(days)} days old. "
                "It may be significantly outdated. Verify before relying on it."
            )
        return (
            f"⚠️ This memory is {int(days)} days old and likely stale. "
            "Strongly recommend verifying against current state."
        )

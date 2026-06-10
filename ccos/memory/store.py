"""Memory store — file-system backed structured memory with YAML frontmatter.

Memory files live at ``~/.ccos/projects/<project-slug>/memory/``.
Each is a Markdown file with YAML frontmatter (name, description, type).
MEMORY.md is an index file, not a memory itself — it is authored by the agent
(main + extraction sub-agent) as one-line topic hooks, per cc's two-step save
contract. The store reads and truncates it; it does not regenerate it.
"""

from __future__ import annotations

import os
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from ccos.memory.types import MemoryType, parse_memory_type

# Limits matching Claude Code (cc/src/memdir/memdir.ts)
MAX_MEMORY_FILES = 200
MAX_INDEX_LINES = 200  # MAX_ENTRYPOINT_LINES
MAX_INDEX_BYTES = 25_000  # MAX_ENTRYPOINT_BYTES
FRONTMATTER_SCAN_LINES = 30  # Only read first 30 lines for scanning

ENTRYPOINT_NAME = "MEMORY.md"

# Characters that require quoting in YAML values (mirrors cc's YAML_SPECIAL_CHARS
# in utils/frontmatterParser.ts). ': ' (colon+space) is matched as a substring.
_YAML_SPECIAL_CHARS = re.compile(r"[{}\[\]*&#!|>%@`]|: ")


@dataclass
class MemoryEntry:
    """A single memory file's parsed content."""
    name: str
    description: str
    type: MemoryType | None
    content: str = ""
    file_path: str = ""
    modified_time: float = 0.0

    @property
    def age_days(self) -> int:
        """Whole days since last modification (floor, clamped to >= 0)."""
        return MemoryStore.memory_age_days(self.modified_time)

    @property
    def filename(self) -> str:
        """Derive a safe filename from the name."""
        safe = re.sub(r"[^\w\-]", "_", self.name.lower()).strip("_")
        return f"{safe}.md"


def _format_file_size(num_bytes: int) -> str:
    """Mirror cc's formatFileSize for the truncation warning reason."""
    if num_bytes < 1024:
        return f"{num_bytes}B"
    kb = num_bytes / 1024
    if kb < 1024:
        # cc uses a single-decimal KB rendering (e.g. "24.4KB").
        return f"{kb:.1f}KB"
    mb = kb / 1024
    return f"{mb:.1f}MB"


class MemoryStore:
    """Manages the memory directory for a project."""

    def __init__(self, cwd: str):
        self._cwd = os.path.abspath(cwd)
        self._slug = self.project_slug(self._cwd)
        self._memory_dir = self._resolve_memory_dir()
        self._memory_dir.mkdir(parents=True, exist_ok=True)

    @property
    def memory_dir(self) -> Path:
        return self._memory_dir

    @property
    def index_path(self) -> Path:
        return self._memory_dir / ENTRYPOINT_NAME

    def _resolve_memory_dir(self) -> Path:
        base = Path(os.environ.get("CCOS_CONFIG_DIR", "~/.ccos")).expanduser()
        return base / "projects" / self._slug / "memory"

    # -- Directory keying ----------------------------------------------------

    @staticmethod
    def _canonical_git_root(cwd: str) -> str:
        """Return the canonical git root for ``cwd`` so all worktrees of a repo
        share one memory directory (cc paths.ts getAutoMemBase /
        findCanonicalGitRoot). Falls back to ``cwd`` when not in a repo.

        ``git rev-parse --git-common-dir`` resolves to the shared ``.git``
        directory even from inside a linked worktree; its parent is the main
        repository working directory.
        """
        try:
            out = subprocess.run(
                ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=5,
            )
        except (OSError, subprocess.SubprocessError):
            return cwd
        if out.returncode != 0:
            return cwd
        common_dir = out.stdout.strip()
        if not common_dir:
            return cwd
        common_path = Path(common_dir)
        # A non-bare repo's common dir is "<root>/.git"; its parent is the root.
        if common_path.name == ".git":
            return str(common_path.parent)
        # Bare repo: use the common dir itself as the stable identity.
        return str(common_path)

    @staticmethod
    def sanitize_path(name: str) -> str:
        """Make a string safe as a directory name (cc sanitizePath).

        Replaces every non-alphanumeric character with '-'. For paths longer
        than the per-component filesystem limit, truncate and append a short
        hash suffix for uniqueness.
        """
        sanitized = re.sub(r"[^a-zA-Z0-9]", "-", name)
        max_len = 200  # MAX_SANITIZED_LENGTH
        if len(sanitized) <= max_len:
            return sanitized
        import hashlib

        digest = int(hashlib.sha256(name.encode()).hexdigest(), 16)
        suffix = _to_base36(digest)
        return f"{sanitized[:max_len]}-{suffix}"

    @classmethod
    def project_slug(cls, cwd: str) -> str:
        """Human-readable, worktree-stable directory slug for ``cwd``."""
        root = cls._canonical_git_root(os.path.abspath(cwd))
        return cls.sanitize_path(root)

    # -- Scanning (frontmatter only, fast) -----------------------------------

    def scan_all(self) -> list[MemoryEntry]:
        """Scan all .md files in memory dir, parse frontmatter headers only.

        Returns entries sorted by modification time (newest first).
        Limited to MAX_MEMORY_FILES.
        """
        entries: list[MemoryEntry] = []
        if not self._memory_dir.exists():
            return entries

        for path in self._memory_dir.rglob("*.md"):
            if path.name == ENTRYPOINT_NAME:
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

        fm = self._parse_frontmatter_fields(lines[1:end_idx])

        name = fm.get("name") or path.stem
        description = fm.get("description", "")
        # Unknown/missing type degrades to None (cc parseMemoryType), so the
        # manifest tag is omitted rather than silently coerced to "project".
        mem_type = parse_memory_type(fm.get("type"))

        mtime = path.stat().st_mtime if path.exists() else 0.0

        return MemoryEntry(
            name=name,
            description=description,
            type=mem_type,
            content="",  # Not loaded during scan
            file_path=str(path),
            modified_time=mtime,
        )

    @staticmethod
    def _parse_frontmatter_fields(body_lines: list[str]) -> dict[str, str]:
        """Parse ``key: value`` frontmatter lines, handling quoted/escaped values.

        pyyaml is intentionally not a dependency, so this is a hand-rolled parser
        that nonetheless handles the cases cc's YAML parser does for memory
        frontmatter: surrounding single/double quotes are stripped and the value
        is unescaped. Lines without a top-level ``key:`` (list items, indented
        continuations) are ignored.
        """
        fm: dict[str, str] = {}
        for raw in body_lines:
            line = raw.rstrip("\n")
            # Only simple top-level keys (not indented, not list items).
            m = re.match(r"^([A-Za-z_][\w-]*)\s*:\s*(.*)$", line)
            if not m:
                continue
            key, value = m.group(1), m.group(2).strip()
            fm[key] = _unquote_yaml_scalar(value)
        return fm

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
        """Write a memory entry as a .md file with YAML frontmatter.

        Note: this does NOT regenerate MEMORY.md. Per cc's two-step contract,
        the index is authored by the agent as one-line topic hooks. ``/memory``
        CLI callers that want a generated fallback index can call
        ``ensure_index`` explicitly.
        """
        filename = entry.filename
        path = self._memory_dir / filename
        entry.file_path = str(path)

        type_value = entry.type.value if entry.type else ""
        frontmatter_lines = [
            "---",
            f"name: {_quote_yaml_scalar(entry.name)}",
            f"description: {_quote_yaml_scalar(entry.description)}",
        ]
        if type_value:
            frontmatter_lines.append(f"type: {type_value}")
        frontmatter_lines.append("---")
        frontmatter = "\n".join(frontmatter_lines) + "\n"

        content = f"{frontmatter}\n{entry.content}\n"
        path.write_text(content, encoding="utf-8")
        entry.modified_time = path.stat().st_mtime

    def delete_entry(self, name_or_file: str) -> bool:
        """Delete a memory entry file. Returns True if deleted."""
        path = self._memory_dir / name_or_file
        if not path.exists():
            safe = re.sub(r"[^\w\-]", "_", name_or_file.lower()).strip("_")
            path = self._memory_dir / f"{safe}.md"
        if path.exists() and path.name != ENTRYPOINT_NAME:
            path.unlink()
            return True
        return False

    # -- Index management ----------------------------------------------------

    def load_index(self) -> str:
        """Read MEMORY.md content, truncated to the line AND byte caps.

        Mirrors cc's truncateEntrypointContent: trim, line-truncate to 200,
        then byte-truncate at the last newline before 25,000 bytes, appending a
        single plain-text WARNING line naming which cap fired.
        """
        if not self.index_path.exists():
            return ""
        try:
            text = self.index_path.read_text(encoding="utf-8")
        except OSError:
            return ""
        return self._truncate_entrypoint(text)

    @staticmethod
    def _truncate_entrypoint(raw: str) -> str:
        trimmed = raw.strip()
        if not trimmed:
            return ""

        content_lines = trimmed.split("\n")
        line_count = len(content_lines)
        byte_count = len(trimmed.encode("utf-8"))

        was_line_truncated = line_count > MAX_INDEX_LINES
        # Check original byte count — long lines are the failure mode the byte
        # cap targets, so post-line-truncation size would understate the warning.
        was_byte_truncated = byte_count > MAX_INDEX_BYTES

        if not was_line_truncated and not was_byte_truncated:
            return trimmed

        truncated = (
            "\n".join(content_lines[:MAX_INDEX_LINES])
            if was_line_truncated
            else trimmed
        )

        truncated_bytes = truncated.encode("utf-8")
        if len(truncated_bytes) > MAX_INDEX_BYTES:
            head = truncated_bytes[:MAX_INDEX_BYTES]
            cut_at = head.rfind(b"\n")
            if cut_at > 0:
                truncated = head[:cut_at].decode("utf-8", errors="ignore")
            else:
                truncated = head.decode("utf-8", errors="ignore")

        if was_byte_truncated and not was_line_truncated:
            reason = (
                f"{_format_file_size(byte_count)} "
                f"(limit: {_format_file_size(MAX_INDEX_BYTES)}) — index entries are too long"
            )
        elif was_line_truncated and not was_byte_truncated:
            reason = f"{line_count} lines (limit: {MAX_INDEX_LINES})"
        else:
            reason = f"{line_count} lines and {_format_file_size(byte_count)}"

        return (
            truncated
            + f"\n\n> WARNING: {ENTRYPOINT_NAME} is {reason}. Only part of it was loaded. "
            "Keep index entries to one line under ~200 chars; move detail into topic files."
        )

    def ensure_index(self) -> None:
        """Generate a fallback MEMORY.md from scanned files ONLY when absent.

        The agent is the author of MEMORY.md (cc two-step contract). This is a
        safety net for the manual ``/memory`` CLI path where there is no agent
        to author the index — it never overwrites an agent-authored index.
        """
        if self.index_path.exists():
            return
        entries = self.scan_all()
        if not entries:
            return
        lines: list[str] = []
        for entry in entries:
            filename = os.path.basename(entry.file_path)
            desc = entry.description[:120] if entry.description else entry.name
            lines.append(f"- [{entry.name}]({filename}) — {desc}")
        self.index_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    # -- Age / freshness -----------------------------------------------------

    @staticmethod
    def memory_age_days(modified_time: float) -> int:
        """Whole days since ``modified_time`` (floor, clamped to >= 0).

        Mirrors cc memoryAge.ts memoryAgeDays.
        """
        if modified_time <= 0:
            return 0
        import math

        days = math.floor((time.time() - modified_time) / 86400.0)
        return max(0, days)

    @staticmethod
    def memory_age(modified_time: float) -> str:
        """Human-readable age: 'today' / 'yesterday' / 'N days ago' (cc memoryAge)."""
        d = MemoryStore.memory_age_days(modified_time)
        if d == 0:
            return "today"
        if d == 1:
            return "yesterday"
        return f"{d} days ago"

    @staticmethod
    def memory_freshness_text(modified_time: float) -> str:
        """Plain-text staleness caveat for memories > 1 day old.

        Verbatim from cc memoryAge.ts memoryFreshnessText. Returns '' for fresh
        (today/yesterday) memories — a warning there is noise.
        """
        d = MemoryStore.memory_age_days(modified_time)
        if d <= 1:
            return ""
        return (
            f"This memory is {d} days old. "
            "Memories are point-in-time observations, not live state — "
            "claims about code behavior or file:line citations may be outdated. "
            "Verify against current code before asserting as fact."
        )

    @staticmethod
    def get_age_warning(entry: MemoryEntry) -> str:
        """Back-compat shim for CLI consumers (``/memory show``).

        Delegates to the single-tier cc freshness text. Kept so the public API
        other (non-owned) modules rely on does not break.
        """
        return MemoryStore.memory_freshness_text(entry.modified_time)


def _to_base36(num: int) -> str:
    if num == 0:
        return "0"
    digits = "0123456789abcdefghijklmnopqrstuvwxyz"
    out: list[str] = []
    while num:
        num, rem = divmod(num, 36)
        out.append(digits[rem])
    return "".join(reversed(out))


def _unquote_yaml_scalar(value: str) -> str:
    """Strip surrounding quotes and unescape a frontmatter scalar value."""
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        inner = value[1:-1]
        if value[0] == '"':
            # Unescape the escapes quoteProblematicValues introduces.
            inner = inner.replace('\\"', '"').replace("\\\\", "\\")
        else:
            inner = inner.replace("''", "'")
        return inner
    return value


def _quote_yaml_scalar(value: str) -> str:
    """Quote/escape a frontmatter scalar value when it contains YAML-special
    characters (mirrors cc quoteProblematicValues). Leaves plain values bare.
    """
    if value == "":
        return '""'
    if _YAML_SPECIAL_CHARS.search(value):
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return value

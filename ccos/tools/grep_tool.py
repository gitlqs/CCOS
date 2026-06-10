"""Grep tool — content search using ripgrep or pure Python fallback."""

from __future__ import annotations

import os
import re
import subprocess
from typing import Any

from ccos.tools.base import Tool, ToolContext, ToolOutput
from ccos.utils.paths import to_relative
from ccos.utils.platform_info import has_ripgrep

_DEFAULT_HEAD_LIMIT = 250
_MAX_RESULT_CHARS = 20_000
_VCS_DIRS = {".git", ".svn", ".hg", ".bzr", ".jj", ".sl"}


class GrepTool(Tool):
    name = "Grep"
    description = (
        "A powerful search tool built on ripgrep\n"
        "\n"
        "  Usage:\n"
        "  - ALWAYS use Grep for search tasks. NEVER invoke `grep` or `rg` as a Bash command. "
        "The Grep tool has been optimized for correct permissions and access.\n"
        "  - Supports full regex syntax (e.g., \"log.*Error\", \"function\\s+\\w+\")\n"
        "  - Filter files with glob parameter (e.g., \"*.js\", \"**/*.tsx\") or type parameter "
        "(e.g., \"js\", \"py\", \"rust\")\n"
        "  - Output modes: \"content\" shows matching lines, \"files_with_matches\" shows only "
        "file paths (default), \"count\" shows match counts\n"
        "  - Use Agent tool for open-ended searches requiring multiple rounds\n"
        "  - Pattern syntax: Uses ripgrep (not grep) - literal braces need escaping (use "
        "`interface\\{\\}` to find `interface{}` in Go code)\n"
        "  - Multiline matching: By default patterns match within single lines only. For "
        "cross-line patterns like `struct \\{[\\s\\S]*?field`, use `multiline: true`"
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "pattern": {
                "type": "string",
                "description": "The regular expression pattern to search for in file contents",
            },
            "path": {
                "type": "string",
                "description": "File or directory to search in (rg PATH). Defaults to current working directory.",
            },
            "glob": {
                "type": "string",
                "description": "Glob pattern to filter files (e.g. \"*.js\", \"*.{ts,tsx}\") - maps to rg --glob",
            },
            "output_mode": {
                "type": "string",
                "enum": ["content", "files_with_matches", "count"],
                "description": (
                    "Output mode: \"content\" shows matching lines (supports -A/-B/-C context, "
                    "-n line numbers, head_limit), \"files_with_matches\" shows file paths "
                    "(supports head_limit), \"count\" shows match counts (supports head_limit). "
                    "Defaults to \"files_with_matches\"."
                ),
            },
            "-A": {
                "type": "integer",
                "description": "Number of lines to show after each match (rg -A). Requires output_mode: \"content\", ignored otherwise.",
            },
            "-B": {
                "type": "integer",
                "description": "Number of lines to show before each match (rg -B). Requires output_mode: \"content\", ignored otherwise.",
            },
            "-C": {
                "type": "integer",
                "description": "Alias for context.",
            },
            "context": {
                "type": "integer",
                "description": "Number of lines to show before and after each match (rg -C). Requires output_mode: \"content\", ignored otherwise.",
            },
            "-n": {
                "type": "boolean",
                "description": "Show line numbers in output (rg -n). Requires output_mode: \"content\", ignored otherwise. Defaults to true.",
            },
            "-i": {
                "type": "boolean",
                "description": "Case insensitive search (rg -i)",
            },
            "type": {
                "type": "string",
                "description": (
                    "File type to search (rg --type). Common types: js, py, rust, go, java, etc. "
                    "More efficient than include for standard file types."
                ),
            },
            "head_limit": {
                "type": "integer",
                "description": (
                    "Limit output to first N lines/entries, equivalent to \"| head -N\". Works "
                    "across all output modes: content (limits output lines), files_with_matches "
                    "(limits file paths), count (limits count entries). Defaults to 250 when "
                    "unspecified. Pass 0 for unlimited (use sparingly — large result sets waste context)."
                ),
            },
            "offset": {
                "type": "integer",
                "description": (
                    "Skip first N lines/entries before applying head_limit, equivalent to "
                    "\"| tail -n +N | head -N\". Works across all output modes. Defaults to 0."
                ),
            },
            "multiline": {
                "type": "boolean",
                "description": (
                    "Enable multiline mode where . matches newlines and patterns can span lines "
                    "(rg -U --multiline-dotall). Default: false."
                ),
            },
        },
        "required": ["pattern"],
        "additionalProperties": False,
    }

    def is_read_only(self, params: dict[str, Any]) -> bool:
        return True

    @staticmethod
    def _apply_head_limit(
        items: list[str], limit: int | None, offset: int = 0
    ) -> tuple[list[str], int | None]:
        """Mirror cc's applyHeadLimit.

        Returns (sliced_items, applied_limit). ``applied_limit`` is only set when
        truncation actually occurred, so callers know when to show pagination info.
        ``limit == 0`` is the unlimited escape hatch; ``limit is None`` uses the default.
        """
        if limit == 0:
            return items[offset:], None
        effective = limit if limit is not None else _DEFAULT_HEAD_LIMIT
        sliced = items[offset:offset + effective]
        was_truncated = len(items) - offset > effective
        return sliced, (effective if was_truncated else None)

    @staticmethod
    def _format_limit_info(applied_limit: int | None, applied_offset: int) -> str:
        parts: list[str] = []
        if applied_limit is not None:
            parts.append(f"limit: {applied_limit}")
        if applied_offset:
            parts.append(f"offset: {applied_offset}")
        return ", ".join(parts)

    async def execute(self, params: dict[str, Any], ctx: ToolContext) -> ToolOutput:
        pattern = params["pattern"]
        search_path = params.get("path", ctx.cwd) or ctx.cwd
        output_mode = params.get("output_mode", "files_with_matches")
        head_limit = params.get("head_limit")  # None = default 250; 0 = unlimited
        offset = params.get("offset", 0)

        search_path = os.path.expanduser(search_path)
        if not os.path.isabs(search_path):
            search_path = os.path.normpath(os.path.join(ctx.cwd, search_path))

        if has_ripgrep():
            return self._rg_search(params, pattern, search_path, output_mode, head_limit, offset, ctx)
        else:
            return self._python_search(params, pattern, search_path, output_mode, head_limit, offset, ctx)

    def _rg_search(
        self,
        params: dict[str, Any],
        pattern: str,
        search_path: str,
        output_mode: str,
        head_limit: int | None,
        offset: int,
        ctx: ToolContext,
    ) -> ToolOutput:
        # Search hidden files but exclude VCS metadata directories (matches cc).
        cmd = ["rg", "--no-heading", "--hidden"]
        for d in (".git", ".svn", ".hg", ".bzr", ".jj", ".sl"):
            cmd.extend(["--glob", f"!{d}"])
        cmd.extend(["--max-columns", "500"])

        # Output mode
        if output_mode == "files_with_matches":
            cmd.append("--files-with-matches")
        elif output_mode == "count":
            cmd.append("--count")
        else:
            # content mode
            if params.get("-n", True):
                cmd.append("--line-number")

        # Context lines (content mode only): context > -C > -B/-A (cc precedence).
        if output_mode not in ("files_with_matches", "count"):
            if params.get("context") is not None:
                cmd.extend(["-C", str(params["context"])])
            elif params.get("-C") is not None:
                cmd.extend(["-C", str(params["-C"])])
            else:
                if params.get("-B") is not None:
                    cmd.extend(["-B", str(params["-B"])])
                if params.get("-A") is not None:
                    cmd.extend(["-A", str(params["-A"])])

        # Case insensitive
        if params.get("-i"):
            cmd.append("-i")

        # Multiline
        if params.get("multiline"):
            cmd.extend(["-U", "--multiline-dotall"])

        # File type
        if params.get("type"):
            cmd.extend(["--type", params["type"]])

        # Glob filter
        if params.get("glob"):
            cmd.extend(["--glob", params["glob"]])

        cmd.extend(["--", pattern, search_path])

        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=30, cwd=ctx.cwd,
            )
            output = result.stdout
        except subprocess.TimeoutExpired:
            return ToolOutput(content="Search timed out after 30s. Try a more specific pattern.", is_error=True)
        except FileNotFoundError:
            return self._python_search(params, pattern, search_path, output_mode, head_limit, offset, ctx)

        raw_lines = output.split("\n") if output.strip() else []
        raw_lines = [ln for ln in raw_lines if ln != ""]

        if output_mode == "files_with_matches":
            # Sort by mtime descending (newest first) with filename tiebreaker.
            def _mtime(p: str) -> float:
                try:
                    return os.path.getmtime(p)
                except OSError:
                    return 0.0

            raw_lines.sort(key=lambda p: (-_mtime(p), p))
            limited, applied_limit = self._apply_head_limit(raw_lines, head_limit, offset)
            filenames = [to_relative(p, ctx.cwd) for p in limited]
            return self._format_files(filenames, applied_limit, offset)

        if output_mode == "count":
            limited, applied_limit = self._apply_head_limit(raw_lines, head_limit, offset)
            count_lines: list[str] = []
            for line in limited:
                idx = line.rfind(":")
                if idx > 0:
                    count_lines.append(to_relative(line[:idx], ctx.cwd) + line[idx:])
                else:
                    count_lines.append(line)
            return self._format_count(count_lines, applied_limit, offset)

        # content mode
        limited, applied_limit = self._apply_head_limit(raw_lines, head_limit, offset)
        content_lines: list[str] = []
        for line in limited:
            idx = line.find(":")
            if idx > 0:
                content_lines.append(to_relative(line[:idx], ctx.cwd) + line[idx:])
            else:
                content_lines.append(line)
        return self._format_content(content_lines, applied_limit, offset)

    def _python_search(
        self,
        params: dict[str, Any],
        pattern: str,
        search_path: str,
        output_mode: str,
        head_limit: int | None,
        offset: int,
        ctx: ToolContext,
    ) -> ToolOutput:
        """Pure Python fallback when ripgrep is not available."""
        flags = re.IGNORECASE if params.get("-i") else 0
        if params.get("multiline"):
            flags |= re.DOTALL | re.MULTILINE

        try:
            regex = re.compile(pattern, flags)
        except re.error as e:
            return ToolOutput(content=f"Invalid regex pattern: {e}", is_error=True)

        search = os.path.normpath(search_path)

        if os.path.isfile(search):
            files = [search]
        else:
            files = []
            for root, dirs, fnames in os.walk(search):
                # Skip VCS dirs
                dirs[:] = [d for d in dirs if d not in _VCS_DIRS]
                for fname in fnames:
                    files.append(os.path.join(root, fname))

        glob_pattern = params.get("glob")
        type_filter = params.get("type")

        # Collect matches per mode, tracking absolute path for mtime sorting.
        file_matches: list[str] = []  # abs paths (files_with_matches)
        count_lines: list[tuple[str, int]] = []  # (rel, count)
        content_lines: list[str] = []

        for fpath in files:
            # Apply glob/type filters
            if glob_pattern:
                from fnmatch import fnmatch
                if not fnmatch(os.path.basename(fpath), glob_pattern):
                    continue
            if type_filter:
                ext_map = {"py": ".py", "js": ".js", "ts": ".ts", "tsx": ".tsx", "rs": ".rs", "go": ".go", "java": ".java"}
                expected = ext_map.get(type_filter, f".{type_filter}")
                if not fpath.endswith(expected):
                    continue

            try:
                with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read()
            except (OSError, PermissionError):
                continue

            rel = to_relative(fpath, ctx.cwd)

            if output_mode == "files_with_matches":
                if regex.search(content):
                    file_matches.append(fpath)
            elif output_mode == "count":
                matches = regex.findall(content)
                if matches:
                    count_lines.append((rel, len(matches)))
            else:
                for i, line in enumerate(content.split("\n"), 1):
                    if regex.search(line):
                        if params.get("-n", True):
                            content_lines.append(f"{rel}:{i}:{line.rstrip()}")
                        else:
                            content_lines.append(f"{rel}:{line.rstrip()}")

        if output_mode == "files_with_matches":
            def _mtime(p: str) -> float:
                try:
                    return os.path.getmtime(p)
                except OSError:
                    return 0.0

            file_matches.sort(key=lambda p: (-_mtime(p), p))
            limited, applied_limit = self._apply_head_limit(file_matches, head_limit, offset)
            filenames = [to_relative(p, ctx.cwd) for p in limited]
            return self._format_files(filenames, applied_limit, offset)

        if output_mode == "count":
            raw = [f"{rel}:{n}" for rel, n in count_lines]
            limited, applied_limit = self._apply_head_limit(raw, head_limit, offset)
            return self._format_count(limited, applied_limit, offset)

        limited, applied_limit = self._apply_head_limit(content_lines, head_limit, offset)
        return self._format_content(limited, applied_limit, offset)

    # --- result formatting (mirrors cc mapToolResultToToolResultBlockParam) ---

    @staticmethod
    def _cap(text: str) -> str:
        if len(text) > _MAX_RESULT_CHARS:
            return text[:_MAX_RESULT_CHARS] + "\n\n... (output truncated)"
        return text

    def _format_files(
        self, filenames: list[str], applied_limit: int | None, offset: int
    ) -> ToolOutput:
        if not filenames:
            return ToolOutput(content="No files found")
        limit_info = self._format_limit_info(applied_limit, offset)
        n = len(filenames)
        header = f"Found {n} file{'s' if n != 1 else ''}"
        if limit_info:
            header += f" {limit_info}"
        return ToolOutput(content=self._cap(header + "\n" + "\n".join(filenames)))

    def _format_content(
        self, lines: list[str], applied_limit: int | None, offset: int
    ) -> ToolOutput:
        if not lines:
            return ToolOutput(content="No matches found")
        body = self._cap("\n".join(lines))
        limit_info = self._format_limit_info(applied_limit, offset)
        if limit_info:
            body += f"\n\n[Showing results with pagination = {limit_info}]"
        return ToolOutput(content=body)

    def _format_count(
        self, lines: list[str], applied_limit: int | None, offset: int
    ) -> ToolOutput:
        if not lines:
            return ToolOutput(content="No matches found")
        total = 0
        files = 0
        for line in lines:
            idx = line.rfind(":")
            if idx > 0:
                try:
                    total += int(line[idx + 1:])
                    files += 1
                except ValueError:
                    pass
        body = self._cap("\n".join(lines))
        limit_info = self._format_limit_info(applied_limit, offset)
        summary = (
            f"\n\nFound {total} total "
            f"{'occurrence' if total == 1 else 'occurrences'} across {files} "
            f"{'file' if files == 1 else 'files'}."
        )
        if limit_info:
            summary += f" with pagination = {limit_info}"
        return ToolOutput(content=body + summary)

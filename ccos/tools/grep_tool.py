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
        "A powerful search tool built on ripgrep.\n\n"
        "Usage:\n"
        "- ALWAYS use Grep for search tasks. NEVER invoke grep or rg as a Bash command.\n"
        "- Supports full regex syntax (e.g., \"log.*Error\", \"function\\s+\\w+\")\n"
        "- Filter files with glob parameter (e.g., \"*.js\", \"**/*.tsx\")\n"
        "- Output modes: \"content\" shows matching lines, \"files_with_matches\" shows only file paths (default), \"count\" shows match counts"
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
                "description": "File or directory to search in. Defaults to current working directory.",
            },
            "glob": {
                "type": "string",
                "description": "Glob pattern to filter files (e.g. \"*.js\", \"*.{ts,tsx}\")",
            },
            "output_mode": {
                "type": "string",
                "enum": ["content", "files_with_matches", "count"],
                "description": "Output mode. Defaults to 'files_with_matches'.",
            },
            "-A": {
                "type": "integer",
                "description": "Number of lines to show after each match",
            },
            "-B": {
                "type": "integer",
                "description": "Number of lines to show before each match",
            },
            "-C": {
                "type": "integer",
                "description": "Number of context lines before and after each match",
            },
            "-n": {
                "type": "boolean",
                "description": "Show line numbers in output. Defaults to true.",
            },
            "-i": {
                "type": "boolean",
                "description": "Case insensitive search",
            },
            "type": {
                "type": "string",
                "description": "File type to search (e.g., js, py, rust, go)",
            },
            "head_limit": {
                "type": "integer",
                "description": "Limit output to first N lines/entries. Defaults to 250.",
            },
            "offset": {
                "type": "integer",
                "description": "Skip first N lines/entries before applying head_limit.",
            },
            "multiline": {
                "type": "boolean",
                "description": "Enable multiline mode. Default: false.",
            },
        },
        "required": ["pattern"],
        "additionalProperties": False,
    }

    def is_read_only(self, params: dict[str, Any]) -> bool:
        return True

    async def execute(self, params: dict[str, Any], ctx: ToolContext) -> ToolOutput:
        pattern = params["pattern"]
        search_path = params.get("path", ctx.cwd) or ctx.cwd
        output_mode = params.get("output_mode", "files_with_matches")
        head_limit = params.get("head_limit", _DEFAULT_HEAD_LIMIT)
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
        head_limit: int,
        offset: int,
        ctx: ToolContext,
    ) -> ToolOutput:
        cmd = ["rg", "--no-heading", "--max-columns", "500", "--max-columns-preview"]

        # Output mode
        if output_mode == "files_with_matches":
            cmd.append("--files-with-matches")
        elif output_mode == "count":
            cmd.append("--count")
        else:
            # content mode
            if params.get("-n", True):
                cmd.append("--line-number")

        # Context lines
        if params.get("-C"):
            cmd.extend(["-C", str(params["-C"])])
        else:
            if params.get("-A"):
                cmd.extend(["-A", str(params["-A"])])
            if params.get("-B"):
                cmd.extend(["-B", str(params["-B"])])

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

        # Sort by mtime
        cmd.append("--sort=modified")

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

        if not output.strip():
            return ToolOutput(content=f"No matches found for pattern: {pattern}")

        # Apply offset + limit
        lines = output.strip().split("\n")
        if offset > 0:
            lines = lines[offset:]
        if head_limit > 0:
            lines = lines[:head_limit]

        # Convert to relative paths
        processed: list[str] = []
        for line in lines:
            processed.append(line.replace(search_path, to_relative(search_path, ctx.cwd)))

        result_text = "\n".join(processed)
        if len(result_text) > _MAX_RESULT_CHARS:
            result_text = result_text[:_MAX_RESULT_CHARS] + "\n\n... (output truncated)"

        return ToolOutput(content=result_text)

    def _python_search(
        self,
        params: dict[str, Any],
        pattern: str,
        search_path: str,
        output_mode: str,
        head_limit: int,
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

        results: list[str] = []
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
                    results.append(rel)
            elif output_mode == "count":
                matches = regex.findall(content)
                if matches:
                    results.append(f"{rel}:{len(matches)}")
            else:
                for i, line in enumerate(content.split("\n"), 1):
                    if regex.search(line):
                        results.append(f"{rel}:{i}:{line.rstrip()}")

            if len(results) >= offset + head_limit + 100:
                break

        if not results:
            return ToolOutput(content=f"No matches found for pattern: {pattern}")

        # Apply offset + limit
        if offset > 0:
            results = results[offset:]
        if head_limit > 0:
            results = results[:head_limit]

        result_text = "\n".join(results)
        if len(result_text) > _MAX_RESULT_CHARS:
            result_text = result_text[:_MAX_RESULT_CHARS] + "\n\n... (output truncated)"

        return ToolOutput(content=result_text)

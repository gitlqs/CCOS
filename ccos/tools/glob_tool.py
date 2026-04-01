"""Glob tool — fast file pattern matching."""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

from ccos.tools.base import Tool, ToolContext, ToolOutput
from ccos.utils.paths import to_relative

_MAX_RESULTS = 100


class GlobTool(Tool):
    name = "Glob"
    description = (
        "Fast file pattern matching tool that works with any codebase size.\n"
        "- Supports glob patterns like \"**/*.js\" or \"src/**/*.ts\"\n"
        "- Returns matching file paths sorted by modification time\n"
        "- Use this tool when you need to find files by name patterns"
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "pattern": {
                "type": "string",
                "description": "The glob pattern to match files against",
            },
            "path": {
                "type": "string",
                "description": (
                    "The directory to search in. If not specified, the current working directory will be used. "
                    "IMPORTANT: Omit this field to use the default directory."
                ),
            },
        },
        "required": ["pattern"],
        "additionalProperties": False,
    }

    def is_read_only(self, params: dict[str, Any]) -> bool:
        return True

    async def execute(self, params: dict[str, Any], ctx: ToolContext) -> ToolOutput:
        pattern = params["pattern"]
        search_path = params.get("path", ctx.cwd)

        if not search_path:
            search_path = ctx.cwd

        search_path = os.path.expanduser(search_path)
        if not os.path.isabs(search_path):
            search_path = os.path.normpath(os.path.join(ctx.cwd, search_path))

        if not os.path.isdir(search_path):
            return ToolOutput(
                content=f"Error: Directory not found: {search_path}",
                is_error=True,
            )

        start = time.monotonic()
        try:
            matches: list[tuple[float, str]] = []
            p = Path(search_path)
            for match in p.glob(pattern):
                if match.is_file():
                    # Skip .git internals
                    parts = match.parts
                    if ".git" in parts:
                        continue
                    try:
                        mtime = match.stat().st_mtime
                    except OSError:
                        mtime = 0
                    matches.append((mtime, str(match)))

            # Sort by modification time (newest first)
            matches.sort(key=lambda x: x[0], reverse=True)

            duration_ms = (time.monotonic() - start) * 1000
            truncated = len(matches) > _MAX_RESULTS
            matches = matches[:_MAX_RESULTS]

            # Convert to relative paths
            filenames = [to_relative(m[1], ctx.cwd) for m in matches]

            if not filenames:
                return ToolOutput(content=f"No files found matching pattern: {pattern}")

            result = "\n".join(filenames)
            if truncated:
                result += f"\n\n(Results truncated. Showing first {_MAX_RESULTS} of {len(matches)} files. Use a more specific pattern.)"

            return ToolOutput(content=result)

        except Exception as e:
            return ToolOutput(content=f"Error during glob search: {e}", is_error=True)

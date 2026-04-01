"""FileRead tool — read files with line numbers, images, and PDF support."""

from __future__ import annotations

import base64
import mimetypes
import os
from typing import Any

from ccos.tools.base import Tool, ToolContext, ToolOutput

_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".svg", ".ico"}
_DEFAULT_LIMIT = 2000


class FileReadTool(Tool):
    name = "Read"
    description = (
        "Reads a file from the local filesystem. You can access any file directly by using this tool.\n"
        "Assume this tool is able to read all files on the machine. If the User provides a path to a file assume that path is valid.\n\n"
        "Usage:\n"
        "- The file_path parameter must be an absolute path, not a relative path\n"
        "- By default, it reads up to 2000 lines starting from the beginning of the file\n"
        "- When you already know which part of the file you need, only read that part.\n"
        "- Results are returned using cat -n format, with line numbers starting at 1\n"
        "- This tool can read images (PNG, JPG, etc). When reading an image file the contents are presented visually.\n"
        "- If you read a file that exists but has empty contents you will receive a system reminder warning."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "The absolute path to the file to read",
            },
            "offset": {
                "type": "integer",
                "description": "The line number to start reading from (1-indexed). Only provide if the file is too large to read at once.",
            },
            "limit": {
                "type": "integer",
                "description": "The number of lines to read. Only provide if the file is too large to read at once.",
            },
        },
        "required": ["file_path"],
        "additionalProperties": False,
    }

    def is_read_only(self, params: dict[str, Any]) -> bool:
        return True

    async def execute(self, params: dict[str, Any], ctx: ToolContext) -> ToolOutput:
        file_path = params["file_path"]
        offset = params.get("offset", 0)
        limit = params.get("limit", _DEFAULT_LIMIT)

        # Expand path
        file_path = os.path.expanduser(file_path)
        if not os.path.isabs(file_path):
            file_path = os.path.normpath(os.path.join(ctx.cwd, file_path))

        if not os.path.exists(file_path):
            return ToolOutput(content=f"Error: File not found: {file_path}", is_error=True)

        if os.path.isdir(file_path):
            return ToolOutput(
                content=f"Error: {file_path} is a directory, not a file. Use Bash with 'ls' to list directory contents.",
                is_error=True,
            )

        # Check for image
        ext = os.path.splitext(file_path)[1].lower()
        if ext in _IMAGE_EXTENSIONS:
            return await self._read_image(file_path, ctx)

        # Read text file
        try:
            with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                all_lines = f.readlines()
        except UnicodeDecodeError:
            try:
                with open(file_path, "r", encoding="utf-16-le", errors="replace") as f:
                    all_lines = f.readlines()
            except Exception as e:
                return ToolOutput(content=f"Error reading file: {e}", is_error=True)
        except PermissionError:
            return ToolOutput(content=f"Error: Permission denied: {file_path}", is_error=True)
        except Exception as e:
            return ToolOutput(content=f"Error reading file: {e}", is_error=True)

        if not all_lines:
            ctx.record_read(file_path)
            return ToolOutput(content="(empty file)")

        # Apply offset/limit
        start = max(0, offset)
        end = start + limit
        selected = all_lines[start:end]

        # Format with line numbers (cat -n style)
        lines_out: list[str] = []
        for i, line in enumerate(selected, start=start + 1):
            lines_out.append(f"{i}\t{line.rstrip()}")

        ctx.record_read(file_path)
        result = "\n".join(lines_out)

        if end < len(all_lines):
            result += f"\n\n(File has {len(all_lines)} total lines. Showing lines {start + 1}-{end}.)"

        return ToolOutput(content=result)

    async def _read_image(self, file_path: str, ctx: ToolContext) -> ToolOutput:
        try:
            with open(file_path, "rb") as f:
                data = base64.b64encode(f.read()).decode("ascii")
            mime = mimetypes.guess_type(file_path)[0] or "image/png"
            ctx.record_read(file_path)
            return ToolOutput(
                content=f"[Image: {os.path.basename(file_path)}]",
                images=[{"media_type": mime, "data": data}],
            )
        except Exception as e:
            return ToolOutput(content=f"Error reading image: {e}", is_error=True)

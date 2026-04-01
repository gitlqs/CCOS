"""FileWrite tool — create or overwrite files."""

from __future__ import annotations

import os
from typing import Any

from ccos.tools.base import Tool, ToolContext, ToolOutput
from ccos.utils.paths import ensure_parent


class FileWriteTool(Tool):
    name = "Write"
    description = (
        "Writes a file to the local filesystem.\n\n"
        "Usage:\n"
        "- This tool will overwrite the existing file if there is one at the provided path.\n"
        "- If this is an existing file, you MUST use the Read tool first to read the file's contents.\n"
        "- Prefer the Edit tool for modifying existing files — it only sends the diff.\n"
        "- NEVER create documentation files (*.md) or README files unless explicitly requested."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "The absolute path to the file to write (must be absolute, not relative)",
            },
            "content": {
                "type": "string",
                "description": "The content to write to the file",
            },
        },
        "required": ["file_path", "content"],
        "additionalProperties": False,
    }

    async def execute(self, params: dict[str, Any], ctx: ToolContext) -> ToolOutput:
        file_path = params["file_path"]
        content = params["content"]

        file_path = os.path.expanduser(file_path)
        if not os.path.isabs(file_path):
            file_path = os.path.normpath(os.path.join(ctx.cwd, file_path))

        exists = os.path.exists(file_path)

        # If file exists, it must have been read first
        if exists and not ctx.was_read(file_path):
            return ToolOutput(
                content=(
                    f"Error: You must read the file before overwriting it. "
                    f"Use the Read tool first on: {file_path}"
                ),
                is_error=True,
            )

        # Check for external modifications since read
        if exists and ctx.was_modified_since_read(file_path):
            return ToolOutput(
                content=(
                    f"Error: File {file_path} has been modified since you last read it. "
                    f"Read it again before writing."
                ),
                is_error=True,
            )

        try:
            ensure_parent(file_path)
            with open(file_path, "w", encoding="utf-8", newline="\n") as f:
                f.write(content)
            ctx.record_read(file_path)

            if exists:
                return ToolOutput(content=f"The file {file_path} has been updated successfully.")
            else:
                return ToolOutput(content=f"File created successfully at: {file_path}")
        except PermissionError:
            return ToolOutput(content=f"Error: Permission denied: {file_path}", is_error=True)
        except Exception as e:
            return ToolOutput(content=f"Error writing file: {e}", is_error=True)

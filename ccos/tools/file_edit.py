"""FileEdit tool — exact string replacement in files."""

from __future__ import annotations

import os
from typing import Any

from ccos.tools.base import Tool, ToolContext, ToolOutput


class FileEditTool(Tool):
    name = "Edit"
    description = (
        "Performs exact string replacements in files.\n\n"
        "Usage:\n"
        "- You must use the Read tool at least once before editing a file.\n"
        "- The edit will FAIL if old_string is not unique in the file. Provide more surrounding context to make it unique, "
        "or use replace_all to change every instance.\n"
        "- Use replace_all for renaming variables or strings across the file."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "The absolute path to the file to modify",
            },
            "old_string": {
                "type": "string",
                "description": "The text to replace",
            },
            "new_string": {
                "type": "string",
                "description": "The text to replace it with (must be different from old_string)",
            },
            "replace_all": {
                "type": "boolean",
                "description": "Replace all occurrences of old_string (default false)",
                "default": False,
            },
        },
        "required": ["file_path", "old_string", "new_string"],
        "additionalProperties": False,
    }

    async def execute(self, params: dict[str, Any], ctx: ToolContext) -> ToolOutput:
        file_path = params["file_path"]
        old_string = params["old_string"]
        new_string = params["new_string"]
        replace_all = params.get("replace_all", False)

        file_path = os.path.expanduser(file_path)
        if not os.path.isabs(file_path):
            file_path = os.path.normpath(os.path.join(ctx.cwd, file_path))

        # Validation
        if old_string == new_string:
            return ToolOutput(
                content="Error: old_string and new_string are identical. No changes needed.",
                is_error=True,
            )

        if not os.path.exists(file_path):
            return ToolOutput(content=f"Error: File not found: {file_path}", is_error=True)

        if not ctx.was_read(file_path):
            return ToolOutput(
                content=(
                    f"Error: You must read the file before editing it. "
                    f"Use the Read tool first on: {file_path}"
                ),
                is_error=True,
            )

        if ctx.was_modified_since_read(file_path):
            return ToolOutput(
                content=(
                    f"Error: File {file_path} has been modified since you last read it. "
                    f"Read it again before editing."
                ),
                is_error=True,
            )

        # Read current content
        try:
            with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
        except Exception as e:
            return ToolOutput(content=f"Error reading file: {e}", is_error=True)

        # Check old_string exists
        count = content.count(old_string)
        if count == 0:
            return ToolOutput(
                content=(
                    f"Error: old_string not found in {file_path}. "
                    f"Make sure the string matches exactly, including whitespace and indentation."
                ),
                is_error=True,
            )

        if count > 1 and not replace_all:
            return ToolOutput(
                content=(
                    f"Error: old_string appears {count} times in {file_path}. "
                    f"Either provide more context to make it unique, or set replace_all=true."
                ),
                is_error=True,
            )

        # Perform replacement
        if replace_all:
            new_content = content.replace(old_string, new_string)
            replaced = count
        else:
            new_content = content.replace(old_string, new_string, 1)
            replaced = 1

        try:
            with open(file_path, "w", encoding="utf-8", newline="\n") as f:
                f.write(new_content)
            ctx.record_read(file_path)

            if replaced == 1:
                return ToolOutput(content=f"The file {file_path} has been updated.")
            else:
                return ToolOutput(content=f"The file {file_path} has been updated. Replaced {replaced} occurrences.")
        except Exception as e:
            return ToolOutput(content=f"Error writing file: {e}", is_error=True)

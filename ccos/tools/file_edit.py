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
        "- You must use your `Read` tool at least once in the conversation before editing. This tool will error if you attempt an edit without reading the file. \n"
        "- When editing text from Read tool output, ensure you preserve the exact indentation (tabs/spaces) as it appears AFTER the line number prefix. The line number prefix format is: line number + tab. Everything after that is the actual file content to match. Never include any part of the line number prefix in the old_string or new_string.\n"
        "- ALWAYS prefer editing existing files in the codebase. NEVER write new files unless explicitly required.\n"
        "- Only use emojis if the user explicitly requests it. Avoid adding emojis to files unless asked.\n"
        "- The edit will FAIL if `old_string` is not unique in the file. Either provide a larger string with more surrounding context to make it unique or use `replace_all` to change every instance of `old_string`.\n"
        "- Use `replace_all` for replacing and renaming strings across the file. This parameter is useful if you want to rename a variable for instance."
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

        # Validation — order mirrors cc's validateInput.
        if old_string == new_string:
            return ToolOutput(
                content="No changes to make: old_string and new_string are exactly the same.",
                is_error=True,
            )

        exists = os.path.exists(file_path)

        # New-file creation: missing file + empty old_string writes new_string.
        if not exists:
            if old_string == "":
                try:
                    from ccos.utils.paths import ensure_parent

                    ensure_parent(file_path)
                    with open(file_path, "w", encoding="utf-8", newline="\n") as f:
                        f.write(new_string)
                    ctx.record_read(file_path)
                    return ToolOutput(content=f"File created successfully at: {file_path}")
                except Exception as e:
                    return ToolOutput(content=f"Error writing file: {e}", is_error=True)
            return ToolOutput(content=f"Error: File not found: {file_path}", is_error=True)

        # Read current content (needed for the empty-old_string-on-empty-file case).
        try:
            with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
        except Exception as e:
            return ToolOutput(content=f"Error reading file: {e}", is_error=True)

        # Existing file with empty old_string: only valid if the file is empty.
        if old_string == "":
            if content.strip() != "":
                return ToolOutput(
                    content="Cannot create new file - file already exists.",
                    is_error=True,
                )
            try:
                with open(file_path, "w", encoding="utf-8", newline="\n") as f:
                    f.write(new_string)
                ctx.record_read(file_path)
                return ToolOutput(content=f"The file {file_path} has been updated successfully.")
            except Exception as e:
                return ToolOutput(content=f"Error writing file: {e}", is_error=True)

        # Jupyter notebooks must go through NotebookEdit.
        if file_path.endswith(".ipynb"):
            return ToolOutput(
                content="File is a Jupyter Notebook. Use the NotebookEdit to edit this file.",
                is_error=True,
            )

        if not ctx.was_read(file_path):
            return ToolOutput(
                content="File has not been read yet. Read it first before writing to it.",
                is_error=True,
            )

        if ctx.was_modified_since_read(file_path):
            return ToolOutput(
                content="File has been modified since read, either by the user or by a linter. Read it again before attempting to write it.",
                is_error=True,
            )

        # Check old_string exists
        count = content.count(old_string)
        if count == 0:
            return ToolOutput(
                content=f"String to replace not found in file.\nString: {old_string}",
                is_error=True,
            )

        if count > 1 and not replace_all:
            return ToolOutput(
                content=(
                    f"Found {count} matches of the string to replace, but replace_all is false. "
                    f"To replace all occurrences, set replace_all to true. "
                    f"To replace only one occurrence, please provide more context to uniquely "
                    f"identify the instance.\nString: {old_string}"
                ),
                is_error=True,
            )

        # Perform replacement
        if replace_all:
            new_content = content.replace(old_string, new_string)
        else:
            new_content = content.replace(old_string, new_string, 1)

        try:
            with open(file_path, "w", encoding="utf-8", newline="\n") as f:
                f.write(new_content)
            ctx.record_read(file_path)

            if replace_all:
                return ToolOutput(
                    content=f"The file {file_path} has been updated. All occurrences were successfully replaced."
                )
            return ToolOutput(content=f"The file {file_path} has been updated successfully.")
        except Exception as e:
            return ToolOutput(content=f"Error writing file: {e}", is_error=True)

"""NotebookEdit tool -- edit Jupyter notebook cells."""

from __future__ import annotations

import json
import os
import re
from typing import Any

from ccos.tools.base import Tool, ToolContext, ToolOutput

_CELL_ID_RE = re.compile(r"^cell-(\d+)$")


def _parse_cell_id(cell_id: str) -> int | None:
    """Parse a 'cell-N' style id into its numeric index, else None."""
    m = _CELL_ID_RE.match(cell_id)
    if m is None:
        return None
    return int(m.group(1))


class NotebookEditTool(Tool):
    name = "NotebookEdit"
    description = (
        "Completely replaces the contents of a specific cell in a Jupyter notebook (.ipynb file) "
        "with new source. Jupyter notebooks are interactive documents that combine code, text, and "
        "visualizations, commonly used for data analysis and scientific computing. The notebook_path "
        "parameter must be an absolute path, not a relative path. The cell_number is 0-indexed. Use "
        "edit_mode=insert to add a new cell at the index specified by cell_number. Use edit_mode=delete "
        "to delete the cell at the index specified by cell_number."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "notebook_path": {
                "type": "string",
                "description": "The absolute path to the Jupyter notebook file to edit (must be absolute, not relative)",
            },
            "cell_id": {
                "type": "string",
                "description": "The ID of the cell to edit. When inserting a new cell, the new cell will be inserted after the cell with this ID, or at the beginning if not specified.",
            },
            "new_source": {
                "type": "string",
                "description": "The new source for the cell",
            },
            "cell_type": {
                "type": "string",
                "enum": ["code", "markdown"],
                "description": "The type of the cell (code or markdown). If not specified, it defaults to the current cell type. If using edit_mode=insert, this is required.",
            },
            "edit_mode": {
                "type": "string",
                "enum": ["replace", "insert", "delete"],
                "description": "The type of edit to make (replace, insert, delete). Defaults to replace.",
            },
        },
        "required": ["notebook_path", "new_source"],
        "additionalProperties": False,
    }

    async def execute(self, params: dict[str, Any], ctx: ToolContext) -> ToolOutput:
        nb_path = params["notebook_path"]
        new_source = params["new_source"]
        cell_id = params.get("cell_id")
        cell_type = params.get("cell_type")
        edit_mode = params.get("edit_mode", "replace")

        nb_path = os.path.expanduser(nb_path)
        if not os.path.isabs(nb_path):
            nb_path = os.path.normpath(os.path.join(ctx.cwd, nb_path))

        if not nb_path.endswith(".ipynb"):
            return ToolOutput(
                content="File must be a Jupyter notebook (.ipynb file). For editing other file types, use the FileEdit tool.",
                is_error=True,
            )

        if edit_mode not in ("replace", "insert", "delete"):
            return ToolOutput(
                content="Edit mode must be replace, insert, or delete.",
                is_error=True,
            )

        if edit_mode == "insert" and not cell_type:
            return ToolOutput(
                content="Cell type is required when using edit_mode=insert.",
                is_error=True,
            )

        if not os.path.exists(nb_path):
            return ToolOutput(content="Notebook file does not exist.", is_error=True)

        if not ctx.was_read(nb_path):
            return ToolOutput(
                content="File has not been read yet. Read it first before writing to it.",
                is_error=True,
            )

        if ctx.was_modified_since_read(nb_path):
            return ToolOutput(
                content="File has been modified since read, either by the user or by a linter. Read it again before attempting to write it.",
                is_error=True,
            )

        try:
            with open(nb_path, "r", encoding="utf-8") as f:
                notebook = json.load(f)
        except Exception:
            return ToolOutput(content="Notebook is not valid JSON.", is_error=True)

        cells = notebook.get("cells", [])

        # Resolve cell_id → index. Match by actual id first, then 'cell-N'.
        if not cell_id:
            if edit_mode != "insert":
                return ToolOutput(
                    content="Cell ID must be specified when not inserting a new cell.",
                    is_error=True,
                )
            cell_index = 0  # default to beginning when inserting with no cell_id
        else:
            cell_index = next(
                (i for i, c in enumerate(cells) if c.get("id") == cell_id), -1
            )
            if cell_index == -1:
                parsed = _parse_cell_id(cell_id)
                if parsed is not None:
                    if parsed < 0 or parsed >= len(cells):
                        return ToolOutput(
                            content=f"Cell with index {parsed} does not exist in notebook.",
                            is_error=True,
                        )
                    cell_index = parsed
                else:
                    return ToolOutput(
                        content=f'Cell with ID "{cell_id}" not found in notebook.',
                        is_error=True,
                    )
            if edit_mode == "insert":
                cell_index += 1  # insert AFTER the cell with this ID

        # Convert replace to insert if replacing one past the end.
        if edit_mode == "replace" and cell_index == len(cells):
            edit_mode = "insert"
            if not cell_type:
                cell_type = "code"

        if edit_mode == "delete":
            del cells[cell_index]
            msg = f"Deleted cell {cell_id}"

        elif edit_mode == "insert":
            new_type = cell_type or "code"
            new_cell: dict[str, Any] = {
                "cell_type": new_type,
                "source": new_source,
                "metadata": {},
            }
            if new_type == "code":
                new_cell["execution_count"] = None
                new_cell["outputs"] = []
            cells.insert(cell_index, new_cell)
            msg = f"Inserted cell {cell_id} with {new_source}"

        else:  # replace
            target = cells[cell_index]
            target["source"] = new_source
            if target.get("cell_type") == "code":
                target["execution_count"] = None
                target["outputs"] = []
            if cell_type and cell_type != target.get("cell_type"):
                target["cell_type"] = cell_type
            msg = f"Updated cell {cell_id} with {new_source}"

        try:
            with open(nb_path, "w", encoding="utf-8", newline="\n") as f:
                json.dump(notebook, f, indent=1, ensure_ascii=False)
            ctx.record_read(nb_path)
            return ToolOutput(content=msg)
        except Exception as e:
            return ToolOutput(content=f"Error writing notebook: {e}", is_error=True)

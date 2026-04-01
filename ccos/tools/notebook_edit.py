"""NotebookEdit tool -- edit Jupyter notebook cells."""

from __future__ import annotations

import json
import os
from typing import Any

from ccos.tools.base import Tool, ToolContext, ToolOutput


class NotebookEditTool(Tool):
    name = "NotebookEdit"
    description = (
        "Edit cells in a Jupyter notebook (.ipynb file).\n\n"
        "Operations:\n"
        "- edit: Replace a cell's source content\n"
        "- insert: Add a new cell at a position\n"
        "- delete: Remove a cell\n\n"
        "Cell indices are 0-based. Use the Read tool first to see the notebook."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "notebook_path": {
                "type": "string",
                "description": "Absolute path to the .ipynb file",
            },
            "operation": {
                "type": "string",
                "enum": ["edit", "insert", "delete"],
                "description": "The operation to perform",
            },
            "cell_index": {
                "type": "integer",
                "description": "0-based index of the cell to edit/delete, or position to insert at",
            },
            "cell_type": {
                "type": "string",
                "enum": ["code", "markdown", "raw"],
                "description": "Type of cell (for insert/edit). Default: code",
            },
            "source": {
                "type": "string",
                "description": "New cell source content (for edit/insert operations)",
            },
        },
        "required": ["notebook_path", "operation", "cell_index"],
        "additionalProperties": False,
    }

    async def execute(self, params: dict[str, Any], ctx: ToolContext) -> ToolOutput:
        nb_path = params["notebook_path"]
        operation = params["operation"]
        cell_index = params["cell_index"]
        cell_type = params.get("cell_type", "code")
        source = params.get("source", "")

        nb_path = os.path.expanduser(nb_path)
        if not os.path.isabs(nb_path):
            nb_path = os.path.normpath(os.path.join(ctx.cwd, nb_path))

        if not os.path.exists(nb_path):
            return ToolOutput(content=f"Error: File not found: {nb_path}", is_error=True)

        if not ctx.was_read(nb_path):
            return ToolOutput(
                content="Error: You must read the notebook before editing it.",
                is_error=True,
            )

        try:
            with open(nb_path, "r", encoding="utf-8") as f:
                notebook = json.load(f)
        except Exception as e:
            return ToolOutput(content=f"Error reading notebook: {e}", is_error=True)

        cells = notebook.get("cells", [])

        if operation == "edit":
            if cell_index < 0 or cell_index >= len(cells):
                return ToolOutput(
                    content=f"Error: Cell index {cell_index} out of range (0-{len(cells)-1})",
                    is_error=True,
                )
            cells[cell_index]["source"] = source.split("\n") if source else []
            cells[cell_index]["cell_type"] = cell_type
            msg = f"Cell {cell_index} updated."

        elif operation == "insert":
            if cell_index < 0 or cell_index > len(cells):
                return ToolOutput(
                    content=f"Error: Insert position {cell_index} out of range (0-{len(cells)})",
                    is_error=True,
                )
            new_cell = {
                "cell_type": cell_type,
                "source": source.split("\n") if source else [],
                "metadata": {},
            }
            if cell_type == "code":
                new_cell["outputs"] = []
                new_cell["execution_count"] = None
            cells.insert(cell_index, new_cell)
            msg = f"New {cell_type} cell inserted at position {cell_index}."

        elif operation == "delete":
            if cell_index < 0 or cell_index >= len(cells):
                return ToolOutput(
                    content=f"Error: Cell index {cell_index} out of range (0-{len(cells)-1})",
                    is_error=True,
                )
            cells.pop(cell_index)
            msg = f"Cell {cell_index} deleted."

        else:
            return ToolOutput(content=f"Error: Unknown operation: {operation}", is_error=True)

        try:
            with open(nb_path, "w", encoding="utf-8", newline="\n") as f:
                json.dump(notebook, f, indent=1, ensure_ascii=False)
            ctx.record_read(nb_path)
            return ToolOutput(content=msg)
        except Exception as e:
            return ToolOutput(content=f"Error writing notebook: {e}", is_error=True)

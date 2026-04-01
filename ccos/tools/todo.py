"""TodoWrite tool -- session task list management."""

from __future__ import annotations

import json
from typing import Any

from ccos.tools.base import Tool, ToolContext, ToolOutput


# Session-level task store
_todos: list[dict[str, str]] = []


def get_todos() -> list[dict[str, str]]:
    return _todos


class TodoWriteTool(Tool):
    name = "TodoWrite"
    description = (
        "Create and manage a structured task list for the current coding session.\n"
        "Helps track progress, organize complex tasks, and show the user your plan.\n\n"
        "Task states: pending, in_progress, completed.\n"
        "Keep exactly ONE task as in_progress at any time.\n"
        "Mark tasks complete IMMEDIATELY after finishing."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "todos": {
                "type": "array",
                "description": "The updated todo list",
                "items": {
                    "type": "object",
                    "properties": {
                        "content": {
                            "type": "string",
                            "description": "Task description (imperative form)",
                        },
                        "status": {
                            "type": "string",
                            "enum": ["pending", "in_progress", "completed"],
                        },
                        "activeForm": {
                            "type": "string",
                            "description": "Present continuous form shown during execution",
                        },
                    },
                    "required": ["content", "status", "activeForm"],
                },
            },
        },
        "required": ["todos"],
        "additionalProperties": False,
    }

    def is_read_only(self, params: dict[str, Any]) -> bool:
        return True  # Internal state only, no filesystem changes

    async def execute(self, params: dict[str, Any], ctx: ToolContext) -> ToolOutput:
        todos = params.get("todos", [])
        _todos.clear()
        _todos.extend(todos)

        # Format summary
        lines = ["Todos have been modified successfully.\n"]
        for i, t in enumerate(todos, 1):
            status = t.get("status", "pending")
            icon = {"pending": "[ ]", "in_progress": "[>]", "completed": "[x]"}.get(status, "[ ]")
            lines.append(f"{i}. {icon} {t.get('content', '')}")

        return ToolOutput(content="\n".join(lines))

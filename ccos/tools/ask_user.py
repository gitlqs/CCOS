"""AskUserQuestion tool -- directly ask the user a question during execution."""

from __future__ import annotations

from typing import Any

from rich.console import Console
from rich.panel import Panel

from ccos.tools.base import Tool, ToolContext, ToolOutput


class AskUserQuestionTool(Tool):
    name = "AskUserQuestion"
    description = (
        "Ask the user a question to get clarification or additional information.\n\n"
        "Use this tool when:\n"
        "- You need clarification about the user's intent\n"
        "- You need to choose between multiple valid approaches\n"
        "- You need information that isn't available in the codebase\n"
        "- You want to confirm before taking a potentially risky action\n\n"
        "The user's response will be returned as the tool result."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "question": {
                "type": "string",
                "description": "The question to ask the user",
            },
        },
        "required": ["question"],
        "additionalProperties": False,
    }

    def is_read_only(self, params: dict[str, Any]) -> bool:
        return True  # Just asking a question, no side effects

    async def execute(self, params: dict[str, Any], ctx: ToolContext) -> ToolOutput:
        question = params["question"]
        console = Console()

        console.print(Panel(
            question,
            title="[yellow]Question[/yellow]",
            border_style="yellow",
        ))

        try:
            answer = console.input("[yellow]Your answer: [/yellow]")
            return ToolOutput(content=answer.strip() or "(no response)")
        except (EOFError, KeyboardInterrupt):
            return ToolOutput(content="(user did not respond)")

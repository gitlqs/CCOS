"""Plan mode tools -- EnterPlanMode and ExitPlanMode."""

from __future__ import annotations

from typing import Any

from ccos.tools.base import Tool, ToolContext, ToolOutput


class EnterPlanModeTool(Tool):
    name = "EnterPlanMode"
    description = (
        "Requests permission to enter plan mode for complex tasks requiring "
        "exploration and design.\n\n"
        "Use this when you need to:\n"
        "- Thoroughly explore the codebase before making changes\n"
        "- Design an implementation approach for a complex task\n"
        "- Consider multiple approaches and their trade-offs\n\n"
        "In plan mode, you should NOT edit any files except the plan file. "
        "Focus on reading code and designing your approach."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {},
        "required": [],
        "additionalProperties": False,
    }

    def __init__(self, plan_manager: Any = None):
        self._plan_manager = plan_manager

    def is_read_only(self, params: dict[str, Any]) -> bool:
        return True

    async def execute(self, params: dict[str, Any], ctx: ToolContext) -> ToolOutput:
        if self._plan_manager is None:
            return ToolOutput(
                content="Error: Plan mode not configured.",
                is_error=True,
            )

        if self._plan_manager.is_plan_mode:
            return ToolOutput(
                content="You are already in plan mode.",
            )

        self._plan_manager.enter_plan_mode("default")

        instructions = (
            "Entered plan mode. You should now focus on exploring the codebase "
            "and designing an implementation approach.\n\n"
            "In plan mode, you should:\n"
            "1. Thoroughly explore the codebase to understand existing patterns\n"
            "2. Identify similar features and architectural approaches\n"
            "3. Consider multiple approaches and their trade-offs\n"
            "4. Use AskUserQuestion if you need to clarify the approach\n"
            "5. Design a concrete implementation strategy\n"
            "6. Write your plan to the plan file using the Write tool\n"
            "7. When ready, use ExitPlanMode to present your plan for approval\n\n"
            "Remember: DO NOT write or edit any source files yet. "
            "This is a read-only exploration and planning phase. "
            "Only the plan file should be written to."
        )
        return ToolOutput(content=instructions)


class ExitPlanModeTool(Tool):
    name = "ExitPlanMode"
    description = (
        "Exit plan mode and present your plan for user approval.\n\n"
        "Call this after you have:\n"
        "1. Explored the codebase thoroughly\n"
        "2. Written your implementation plan to the plan file\n"
        "3. Are ready to start coding\n\n"
        "The user will review the plan and approve or reject it."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {},
        "required": [],
        "additionalProperties": False,
    }

    def __init__(self, plan_manager: Any = None, session_id: str = ""):
        self._plan_manager = plan_manager
        self._session_id = session_id

    async def execute(self, params: dict[str, Any], ctx: ToolContext) -> ToolOutput:
        if self._plan_manager is None:
            return ToolOutput(
                content="Error: Plan mode not configured.",
                is_error=True,
            )

        if not self._plan_manager.is_plan_mode:
            return ToolOutput(
                content=(
                    "You are not in plan mode. This tool is only for exiting plan mode "
                    "after writing a plan. If your plan was already approved, continue "
                    "with implementation."
                ),
                is_error=True,
            )

        # Read the plan
        plan = self._plan_manager.get_plan(self._session_id)
        file_path = self._plan_manager.get_plan_file_path(self._session_id)

        # Exit plan mode
        self._plan_manager.exit_plan_mode()

        if not plan or plan.strip() == "":
            return ToolOutput(
                content="User has approved exiting plan mode. You can now proceed.",
            )

        return ToolOutput(
            content=(
                f"User has approved your plan. You can now start coding. "
                f"Start with updating your todo list if applicable.\n\n"
                f"Your plan has been saved to: {file_path}\n"
                f"You can refer back to it if needed during implementation.\n\n"
                f"## Approved Plan:\n{plan}"
            ),
        )

"""Plan mode tools -- EnterPlanMode and ExitPlanMode."""

from __future__ import annotations

from typing import Any

from ccos.tools.base import Tool, ToolContext, ToolOutput


# cc EnterPlanModeTool.description() short line + getEnterPlanModeToolPromptExternal()
# body (EnterPlanModeTool/prompt.ts, non-ant external branch with the
# WHAT_HAPPENS_SECTION included), verbatim.
_ENTER_DESCRIPTION = (
    "Requests permission to enter plan mode for complex tasks requiring "
    "exploration and design\n\n"
    "Use this tool proactively when you're about to start a non-trivial "
    "implementation task. Getting user sign-off on your approach before writing "
    "code prevents wasted effort and ensures alignment. This tool transitions you "
    "into plan mode where you can explore the codebase and design an "
    "implementation approach for user approval.\n\n"
    "## When to Use This Tool\n\n"
    "**Prefer using EnterPlanMode** for implementation tasks unless they're "
    "simple. Use it when ANY of these conditions apply:\n\n"
    "1. **New Feature Implementation**: Adding meaningful new functionality\n"
    '   - Example: "Add a logout button" - where should it go? What should happen '
    "on click?\n"
    '   - Example: "Add form validation" - what rules? What error messages?\n\n'
    "2. **Multiple Valid Approaches**: The task can be solved in several different "
    "ways\n"
    '   - Example: "Add caching to the API" - could use Redis, in-memory, '
    "file-based, etc.\n"
    '   - Example: "Improve performance" - many optimization strategies possible\n\n'
    "3. **Code Modifications**: Changes that affect existing behavior or "
    "structure\n"
    '   - Example: "Update the login flow" - what exactly should change?\n'
    '   - Example: "Refactor this component" - what\'s the target architecture?\n\n'
    "4. **Architectural Decisions**: The task requires choosing between patterns "
    "or technologies\n"
    '   - Example: "Add real-time updates" - WebSockets vs SSE vs polling\n'
    '   - Example: "Implement state management" - Redux vs Context vs custom '
    "solution\n\n"
    "5. **Multi-File Changes**: The task will likely touch more than 2-3 files\n"
    '   - Example: "Refactor the authentication system"\n'
    '   - Example: "Add a new API endpoint with tests"\n\n'
    "6. **Unclear Requirements**: You need to explore before understanding the "
    "full scope\n"
    '   - Example: "Make the app faster" - need to profile and identify '
    "bottlenecks\n"
    '   - Example: "Fix the bug in checkout" - need to investigate root cause\n\n'
    "7. **User Preferences Matter**: The implementation could reasonably go "
    "multiple ways\n"
    "   - If you would use AskUserQuestion to clarify the approach, use "
    "EnterPlanMode instead\n"
    "   - Plan mode lets you explore first, then present options with context\n\n"
    "## When NOT to Use This Tool\n\n"
    "Only skip EnterPlanMode for simple tasks:\n"
    "- Single-line or few-line fixes (typos, obvious bugs, small tweaks)\n"
    "- Adding a single function with clear requirements\n"
    "- Tasks where the user has given very specific, detailed instructions\n"
    "- Pure research/exploration tasks (use the Agent tool with explore agent "
    "instead)\n\n"
    "## What Happens in Plan Mode\n\n"
    "In plan mode, you'll:\n"
    "1. Thoroughly explore the codebase using Glob, Grep, and Read tools\n"
    "2. Understand existing patterns and architecture\n"
    "3. Design an implementation approach\n"
    "4. Present your plan to the user for approval\n"
    "5. Use AskUserQuestion if you need to clarify approaches\n"
    "6. Exit plan mode with ExitPlanMode when ready to implement\n\n"
    "## Examples\n\n"
    "### GOOD - Use EnterPlanMode:\n"
    'User: "Add user authentication to the app"\n'
    "- Requires architectural decisions (session vs JWT, where to store tokens, "
    "middleware structure)\n\n"
    'User: "Optimize the database queries"\n'
    "- Multiple approaches possible, need to profile first, significant impact\n\n"
    'User: "Implement dark mode"\n'
    "- Architectural decision on theme system, affects many components\n\n"
    'User: "Add a delete button to the user profile"\n'
    "- Seems simple but involves: where to place it, confirmation dialog, API "
    "call, error handling, state updates\n\n"
    'User: "Update the error handling in the API"\n'
    "- Affects multiple files, user should approve the approach\n\n"
    "### BAD - Don't use EnterPlanMode:\n"
    'User: "Fix the typo in the README"\n'
    "- Straightforward, no planning needed\n\n"
    'User: "Add a console.log to debug this function"\n'
    "- Simple, obvious implementation\n\n"
    'User: "What files handle routing?"\n'
    "- Research task, not implementation planning\n\n"
    "## Important Notes\n\n"
    "- This tool REQUIRES user approval - they must consent to entering plan "
    "mode\n"
    "- If unsure whether to use it, err on the side of planning - it's better to "
    "get alignment upfront than to redo work\n"
    "- Users appreciate being consulted before significant changes are made to "
    "their codebase"
)


# cc ExitPlanModeV2Tool.description() short line + EXIT_PLAN_MODE_V2_TOOL_PROMPT
# body (ExitPlanModeTool/prompt.ts), verbatim.
_EXIT_DESCRIPTION = (
    "Prompts the user to exit plan mode and start coding\n\n"
    "Use this tool when you are in plan mode and have finished writing your plan "
    "to the plan file and are ready for user approval.\n\n"
    "## How This Tool Works\n"
    "- You should have already written your plan to the plan file specified in "
    "the plan mode system message\n"
    "- This tool does NOT take the plan content as a parameter - it will read the "
    "plan from the file you wrote\n"
    "- This tool simply signals that you're done planning and ready for the user "
    "to review and approve\n"
    "- The user will see the contents of your plan file when they review it\n\n"
    "## When to Use This Tool\n"
    "IMPORTANT: Only use this tool when the task requires planning the "
    "implementation steps of a task that requires writing code. For research "
    "tasks where you're gathering information, searching files, reading files or "
    "in general trying to understand the codebase - do NOT use this tool.\n\n"
    "## Before Using This Tool\n"
    "Ensure your plan is complete and unambiguous:\n"
    "- If you have unresolved questions about requirements or approach, use "
    "AskUserQuestion first (in earlier phases)\n"
    "- Once your plan is finalized, use THIS tool to request approval\n\n"
    "**Important:** Do NOT use AskUserQuestion to ask \"Is this plan okay?\" or "
    '"Should I proceed?" - that\'s exactly what THIS tool does. ExitPlanMode '
    "inherently requests user approval of your plan.\n\n"
    "## Examples\n\n"
    '1. Initial task: "Search for and understand the implementation of vim mode '
    'in the codebase" - Do not use the exit plan mode tool because you are not '
    "planning the implementation steps of a task.\n"
    '2. Initial task: "Help me implement yank mode for vim" - Use the exit plan '
    "mode tool after you have finished planning the implementation steps of the "
    "task.\n"
    '3. Initial task: "Add a new feature to handle user authentication" - If '
    "unsure about auth method (OAuth, JWT, etc.), use AskUserQuestion first, then "
    "use exit plan mode tool after clarifying the approach."
)


class EnterPlanModeTool(Tool):
    name = "EnterPlanMode"
    description = _ENTER_DESCRIPTION
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

        # cc mapToolResultToToolResultBlockParam (non-interview branch), verbatim.
        instructions = (
            "Entered plan mode. You should now focus on exploring the codebase "
            "and designing an implementation approach.\n\n"
            "In plan mode, you should:\n"
            "1. Thoroughly explore the codebase to understand existing patterns\n"
            "2. Identify similar features and architectural approaches\n"
            "3. Consider multiple approaches and their trade-offs\n"
            "4. Use AskUserQuestion if you need to clarify the approach\n"
            "5. Design a concrete implementation strategy\n"
            "6. When ready, use ExitPlanMode to present your plan for approval\n\n"
            "Remember: DO NOT write or edit any files yet. This is a read-only "
            "exploration and planning phase."
        )
        return ToolOutput(content=instructions)


class ExitPlanModeTool(Tool):
    name = "ExitPlanMode"
    description = _EXIT_DESCRIPTION
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

        # cc mapToolResultToToolResultBlockParam (non-empty plan branch), verbatim.
        # Note: "if applicable" has NO trailing period, header is "## Approved Plan:".
        return ToolOutput(
            content=(
                f"User has approved your plan. You can now start coding. "
                f"Start with updating your todo list if applicable\n\n"
                f"Your plan has been saved to: {file_path}\n"
                f"You can refer back to it if needed during implementation.\n\n"
                f"## Approved Plan:\n{plan}"
            ),
        )

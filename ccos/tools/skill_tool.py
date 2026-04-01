"""Skill tool — allows the AI model to invoke skills programmatically.

This is the bridge between the LLM and the skill system. The model calls
this tool with a skill name and optional arguments, and the skill content
is either injected into the conversation (inline) or executed in a
sub-agent (forked).
"""

from __future__ import annotations

from typing import Any

from ccos.tools.base import PermissionCheck, PermissionDecision, Tool, ToolContext, ToolOutput


class SkillTool(Tool):
    name = "Skill"
    description = (
        "Execute a skill within the main conversation.\n\n"
        "When users ask you to perform tasks, check if any of the available skills match. "
        "Skills provide specialized capabilities and domain knowledge.\n\n"
        "When users reference a \"slash command\" or \"/<something>\" (e.g., \"/commit\", "
        "\"/review-pr\"), they are referring to a skill. Use this tool to invoke it.\n\n"
        "How to invoke:\n"
        "- Use this tool with the skill name and optional arguments\n"
        "- Examples:\n"
        "  - `skill: \"pdf\"` - invoke the pdf skill\n"
        "  - `skill: \"commit\", args: \"-m 'Fix bug'\"` - invoke with arguments\n"
        "  - `skill: \"review-pr\", args: \"123\"` - invoke with arguments\n"
        "  - `skill: \"ns:command\"` - invoke using namespaced name\n\n"
        "Important:\n"
        "- Available skills are listed in system-reminder messages in the conversation\n"
        "- When a skill matches the user's request, invoke it BEFORE generating any other response\n"
        "- NEVER mention a skill without actually calling this tool\n"
        "- Do not invoke a skill that is already running\n"
        "- If you see a <command-name> tag in the current conversation turn, the skill has "
        "ALREADY been loaded - follow the instructions directly instead of calling this tool again"
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "skill": {
                "type": "string",
                "description": "The skill name. E.g., \"commit\", \"review-pr\", or \"pdf\"",
            },
            "args": {
                "type": "string",
                "description": "Optional arguments for the skill",
            },
        },
        "required": ["skill"],
        "additionalProperties": False,
    }

    def __init__(self) -> None:
        # Wired by App after construction
        self._skill_registry: Any = None  # SkillRegistry
        self._skill_executor: Any = None  # SkillExecutor

    def is_read_only(self, params: dict[str, Any]) -> bool:
        """Skills can have side effects, so not read-only by default."""
        return False

    def check_permissions(self, params: dict[str, Any], ctx: ToolContext) -> PermissionCheck:
        """Check if skill invocation is allowed.

        Skills with only safe properties (no hooks, no allowed-tools that expand
        permissions) are auto-allowed. Others require user confirmation.
        """
        if self._skill_registry is None:
            return PermissionCheck(PermissionDecision.DENY, "Skill system not initialized")

        skill_name = params.get("skill", "")
        skill = self._skill_registry.get(skill_name)

        if skill is None:
            return PermissionCheck(PermissionDecision.DENY, f"Unknown skill: {skill_name}")

        if skill.disable_model_invocation:
            return PermissionCheck(
                PermissionDecision.DENY,
                f"Skill '{skill_name}' has model invocation disabled",
            )

        # Auto-allow safe skills (no hooks, no custom allowed-tools)
        is_safe = (
            not skill.hooks
            and not skill.allowed_tools
            and not skill.shell
        )
        if is_safe:
            return PermissionCheck(PermissionDecision.ALLOW)

        return PermissionCheck(PermissionDecision.ASK, f"Skill '{skill_name}' requests execution")

    async def execute(self, params: dict[str, Any], ctx: ToolContext) -> ToolOutput:
        skill_name = params.get("skill", "")
        args = params.get("args", "")

        if self._skill_registry is None or self._skill_executor is None:
            return ToolOutput(
                content="Error: Skill system not initialized.",
                is_error=True,
            )

        # Find the skill
        skill = self._skill_registry.get(skill_name)
        if skill is None:
            # Provide helpful error with available skills
            available = self._skill_registry.get_model_invocable()
            names = [s.name for s in available[:20]]
            return ToolOutput(
                content=(
                    f"Error: Unknown skill '{skill_name}'.\n"
                    f"Available skills: {', '.join(names) if names else '(none)'}"
                ),
                is_error=True,
            )

        if skill.disable_model_invocation:
            return ToolOutput(
                content=f"Error: Skill '{skill_name}' cannot be invoked by the model.",
                is_error=True,
            )

        try:
            result = await self._skill_executor.execute(skill, args)
            return ToolOutput(content=result)
        except Exception as e:
            return ToolOutput(
                content=f"Error executing skill '{skill_name}': {e}",
                is_error=True,
            )

"""Skill executor — handles inline and forked skill execution.

Inline:  Skill content is expanded into the conversation as a user message.
Forked:  Skill runs in a separate sub-agent with its own context.
"""

from __future__ import annotations

from typing import Any, Callable

from ccos.skills.arguments import parse_arguments, substitute_arguments, substitute_variables
from ccos.skills.registry import SkillRegistry
from ccos.skills.types import ExecutionContext, SkillDefinition


class SkillExecutor:
    """Executes skills (inline expansion or forked sub-agent)."""

    def __init__(
        self,
        skill_registry: SkillRegistry,
        engine_factory: Callable[..., Any] | None = None,
        session_id: str = "",
    ) -> None:
        self._registry = skill_registry
        self._engine_factory = engine_factory
        self._session_id = session_id

    @property
    def session_id(self) -> str:
        return self._session_id

    @session_id.setter
    def session_id(self, value: str) -> None:
        self._session_id = value

    def prepare_skill_content(
        self,
        skill: SkillDefinition,
        args: str = "",
    ) -> str:
        """Prepare skill content with all substitutions applied.

        1. Prepend base directory header
        2. Substitute arguments ($ARGUMENTS, $0, $name, etc.)
        3. Substitute variables (${CCOS_SKILL_DIR}, ${CCOS_SESSION_ID})

        Returns the fully resolved content string.
        """
        content = skill.content

        # Prepend base directory
        if skill.skill_dir:
            content = f"Base directory for this skill: {skill.skill_dir}\n\n{content}"

        # Substitute arguments
        content = substitute_arguments(
            content,
            args,
            skill.argument_names if skill.argument_names else None,
        )

        # Substitute environment variables
        content = substitute_variables(
            content,
            skill_dir=skill.skill_dir,
            session_id=self._session_id,
        )

        return content

    async def execute_inline(
        self,
        skill: SkillDefinition,
        args: str = "",
    ) -> str:
        """Execute an inline skill.

        Returns the prepared content to be injected into the conversation.
        """
        content = self.prepare_skill_content(skill, args)

        # Track invocation
        self._registry.add_invoked_skill(
            name=skill.name,
            path=skill.loaded_from,
            content=content,
        )

        return content

    async def execute_forked(
        self,
        skill: SkillDefinition,
        args: str = "",
    ) -> str:
        """Execute a forked skill in a sub-agent.

        Returns the sub-agent's final text response.
        """
        if self._engine_factory is None:
            return f"Error: Cannot execute forked skill '{skill.name}' — no engine factory configured."

        content = self.prepare_skill_content(skill, args)

        # Track invocation
        self._registry.add_invoked_skill(
            name=skill.name,
            path=skill.loaded_from,
            content=content,
        )

        try:
            # Create sub-engine with optional model override
            model_override = skill.model if skill.model and skill.model != "inherit" else ""
            sub_engine = self._engine_factory(model_override=model_override)
            result = await sub_engine.run_turn(content)
            return result or f"(Skill '{skill.name}' returned no output)"
        except Exception as e:
            return f"Error executing skill '{skill.name}': {e}"

    async def execute(
        self,
        skill: SkillDefinition,
        args: str = "",
    ) -> str:
        """Execute a skill (auto-detect inline vs forked).

        Returns content string (for inline) or result (for forked).
        """
        if skill.context == ExecutionContext.FORK:
            return await self.execute_forked(skill, args)
        return await self.execute_inline(skill, args)

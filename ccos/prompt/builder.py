"""Dynamic system prompt builder — assembles all sections into the final prompt."""

from __future__ import annotations

from ccos.prompt.context import get_env_info
from ccos.prompt.memory import load_claude_md
from ccos.prompt.sections import (
    get_actions_section,
    get_auto_memory_section,
    get_bash_instructions,
    get_doing_tasks_section,
    get_git_commit_section,
    get_intro_section,
    get_output_efficiency_section,
    get_pr_section,
    get_system_section,
    get_tone_section,
    get_tools_section,
)
from ccos.tools.base import Tool

# Avoid circular import — SkillDefinition only needed for type hints
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from ccos.skills.types import SkillDefinition


DYNAMIC_BOUNDARY = "__SYSTEM_PROMPT_DYNAMIC_BOUNDARY__"


class PromptBuilder:
    """Assemble the system prompt from modular sections.

    Mirrors CC's prompt structure: static cacheable sections first,
    then a dynamic boundary, then session-specific content (memory, env).
    """

    def build(
        self,
        *,
        tools: list[Tool],
        model: str,
        cwd: str,
        provider_name: str = "anthropic",
        plan_mode: bool = False,
        plan_content: str = "",
        skills: list[SkillDefinition] | None = None,
        co_author: str = "",
    ) -> str:
        sections: list[str] = []

        # ── Static sections (cacheable across sessions) ──────────
        sections.append(get_intro_section())
        sections.append(get_system_section())
        sections.append(get_doing_tasks_section())
        sections.append(get_actions_section())
        sections.append(get_tools_section(tools))
        sections.append(get_tone_section())
        sections.append(get_output_efficiency_section())
        sections.append(get_git_commit_section(co_author=co_author))
        sections.append(get_pr_section())
        sections.append(get_bash_instructions())

        # ── Skills section ───────────────────────────────────────
        if skills:
            sections.append(self._build_skills_section(skills))

        # ── Dynamic boundary ─────────────────────────────────────
        sections.append(DYNAMIC_BOUNDARY)

        # ── Plan mode context ────────────────────────────────────
        if plan_mode:
            plan_section = (
                "# Plan mode\n\n"
                "You are currently in PLAN MODE. In this mode:\n"
                "- Focus on understanding the task and creating a detailed plan\n"
                "- You may read files and explore the codebase\n"
                "- Do NOT make changes to files (except the plan file)\n"
                "- Write your plan to the plan file when ready\n"
                "- Use ExitPlanMode when the plan is complete and approved"
            )
            if plan_content:
                plan_section += f"\n\nCurrent plan:\n{plan_content}"
            sections.append(plan_section)

        # ── Auto-memory system prompt ────────────────────────────
        try:
            from ccos.memory.store import MemoryStore
            store = MemoryStore(cwd)
            sections.append(get_auto_memory_section(str(store.memory_dir)))
        except Exception:
            pass

        # ── Memory (CLAUDE.md files + auto-memory index) ─────────
        memory = load_claude_md(cwd)
        if memory:
            sections.append(f"# Project memory\n\n{memory}")

        # ── Environment information ──────────────────────────────
        sections.append(get_env_info(cwd, model, provider_name))

        # Filter empty sections and join
        return "\n\n".join(s for s in sections if s and s != DYNAMIC_BOUNDARY)

    @staticmethod
    def _build_skills_section(skills: list[SkillDefinition]) -> str:
        """Build the system prompt section describing available skills."""
        from ccos.skills.types import SkillDefinition as SD  # avoid import at module level

        lines = [
            "# Available Skills",
            "",
            "The following skills are available. Use the Skill tool to invoke them.",
            "When a user references a slash command (e.g., /commit, /review-pr), "
            "they are referring to a skill — invoke it using the Skill tool.",
            "",
        ]

        for skill in skills:
            hint = f" {skill.argument_hint}" if skill.argument_hint else ""
            desc = f" - {skill.description}" if skill.description else ""
            when = f"\n  When to use: {skill.when_to_use}" if skill.when_to_use else ""
            lines.append(f"- `/{skill.name}{hint}`{desc}{when}")

        return "\n".join(lines)

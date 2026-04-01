"""Skill registry — manages loaded skills and provides lookup.

Integrates with CommandRegistry to register skills as slash commands,
and provides the skill list for the SkillTool (model invocation).
"""

from __future__ import annotations

import fnmatch
from typing import Any, Callable

from ccos.skills.types import SkillDefinition


class SkillRegistry:
    """Central registry for all loaded skills."""

    def __init__(self) -> None:
        self._skills: dict[str, SkillDefinition] = {}
        # Conditional skills (path-filtered, not yet activated)
        self._conditional: dict[str, SkillDefinition] = {}
        # Dynamic skills (discovered during session)
        self._dynamic: dict[str, SkillDefinition] = {}
        # Invoked skills (for compaction survival)
        self._invoked: dict[str, dict[str, Any]] = {}

    def register(self, skill: SkillDefinition) -> None:
        """Register a skill. Conditional skills go to a separate store."""
        if skill.is_conditional:
            self._conditional[skill.name] = skill
        else:
            self._skills[skill.name] = skill

    def unregister(self, name: str) -> bool:
        """Remove a skill by name. Returns True if removed."""
        removed = False
        if name in self._skills:
            del self._skills[name]
            removed = True
        if name in self._conditional:
            del self._conditional[name]
            removed = True
        if name in self._dynamic:
            del self._dynamic[name]
            removed = True
        return removed

    def get(self, name: str) -> SkillDefinition | None:
        """Look up a skill by name."""
        return (
            self._skills.get(name)
            or self._dynamic.get(name)
            or self._conditional.get(name)
        )

    def get_all(self) -> list[SkillDefinition]:
        """Return all active (non-conditional) skills."""
        combined = {**self._skills, **self._dynamic}
        return sorted(combined.values(), key=lambda s: s.name)

    def get_all_including_conditional(self) -> list[SkillDefinition]:
        """Return all skills including conditional ones."""
        combined = {**self._skills, **self._dynamic, **self._conditional}
        return sorted(combined.values(), key=lambda s: s.name)

    def get_user_invocable(self) -> list[SkillDefinition]:
        """Return skills that users can invoke via /name."""
        return [s for s in self.get_all() if s.user_invocable]

    def get_model_invocable(self) -> list[SkillDefinition]:
        """Return skills the model can invoke via SkillTool."""
        return [s for s in self.get_all() if not s.disable_model_invocation]

    def names(self) -> list[str]:
        """Return all active skill names."""
        return [s.name for s in self.get_all()]

    def activate_conditional_for_paths(self, file_paths: list[str]) -> list[str]:
        """Activate conditional skills whose path patterns match the given files.

        Returns list of newly activated skill names.
        """
        activated: list[str] = []
        to_remove: list[str] = []

        for name, skill in self._conditional.items():
            for pattern in skill.paths:
                for fpath in file_paths:
                    # Normalize to forward slashes for matching
                    normalized = fpath.replace("\\", "/")
                    if fnmatch.fnmatch(normalized, pattern):
                        self._dynamic[name] = skill
                        to_remove.append(name)
                        activated.append(name)
                        break
                if name in to_remove:
                    break

        for name in to_remove:
            self._conditional.pop(name, None)

        return activated

    def add_invoked_skill(
        self,
        name: str,
        path: str,
        content: str,
    ) -> None:
        """Track that a skill was invoked (for compaction survival)."""
        self._invoked[name] = {
            "path": path,
            "content": content,
        }

    def get_invoked(self) -> dict[str, dict[str, Any]]:
        """Return all invoked skills."""
        return dict(self._invoked)

    def clear_invoked(self) -> None:
        """Clear all invoked skill tracking."""
        self._invoked.clear()

    def reload(self, skills: list[SkillDefinition]) -> None:
        """Replace all skills with a fresh list (e.g., after reload from disk)."""
        self._skills.clear()
        self._conditional.clear()
        self._dynamic.clear()
        for skill in skills:
            self.register(skill)

"""Skill data types."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class SkillSource(str, Enum):
    """Where a skill was loaded from."""
    PROJECT = "project"        # .ccos/skills/
    USER = "user"              # ~/.ccos/skills/
    LEGACY_PROJECT = "legacy"  # .ccos/commands/
    LEGACY_USER = "legacy_user"  # ~/.ccos/commands/
    BUNDLED = "bundled"        # Built-in
    MCP = "mcp"                # From MCP server


class ExecutionContext(str, Enum):
    INLINE = "inline"   # Expand into conversation
    FORK = "fork"       # Run as sub-agent


@dataclass
class SkillDefinition:
    """A parsed skill definition (from SKILL.md frontmatter + content)."""

    # Identity
    name: str                           # Resolved name (e.g., "my-skill" or "ns:my-skill")
    display_name: str = ""              # User-facing display name
    description: str = ""               # Short description
    when_to_use: str = ""               # Detailed usage scenarios for model

    # Content
    content: str = ""                   # Markdown content (body after frontmatter)
    skill_dir: str = ""                 # Directory containing SKILL.md
    loaded_from: str = ""               # Full path to the SKILL.md file
    source: SkillSource = SkillSource.PROJECT

    # Arguments
    argument_names: list[str] = field(default_factory=list)
    argument_hint: str = ""             # e.g., "[file] [line]"

    # Tools
    allowed_tools: list[str] = field(default_factory=list)

    # Execution
    context: ExecutionContext = ExecutionContext.INLINE
    agent: str = ""                     # Agent type for forked skills
    model: str = ""                     # Model override ("" = default, "inherit" = parent)
    effort: str = ""                    # Thinking effort level

    # Visibility
    user_invocable: bool = True         # Whether /skill-name works for users
    disable_model_invocation: bool = False  # Block model from using SkillTool

    # Metadata
    version: str = ""
    paths: list[str] = field(default_factory=list)  # Glob patterns for conditional activation

    # Hooks
    hooks: dict[str, Any] = field(default_factory=dict)

    # Shell preference
    shell: str = ""                     # "bash" | "powershell"

    @property
    def has_arguments(self) -> bool:
        return bool(self.argument_names)

    @property
    def is_conditional(self) -> bool:
        """Conditional skills activate only when matching files are touched."""
        return bool(self.paths)

    def user_facing_name(self) -> str:
        return self.display_name or self.name

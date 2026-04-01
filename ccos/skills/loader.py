"""Skill loader — discovers and parses skills from disk.

Skill directories (in priority order):
  1. User:     ~/.ccos/skills/<name>/SKILL.md
  2. Project:  <cwd>/.ccos/skills/<name>/SKILL.md
  3. Legacy user:     ~/.ccos/commands/<name>.md
  4. Legacy project:  <cwd>/.ccos/commands/<name>.md  (or <name>/SKILL.md)

Skills are markdown files with optional YAML frontmatter.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from ccos.skills.frontmatter import (
    normalize_bool_field,
    normalize_list_field,
    parse_frontmatter,
)
from ccos.skills.types import ExecutionContext, SkillDefinition, SkillSource


def get_user_skills_dir() -> Path:
    """Return the canonical user-level skills directory (~/.ccos/skills)."""
    return Path.home() / ".ccos" / "skills"


def get_skill_directories(cwd: str) -> list[tuple[Path, SkillSource]]:
    """Return all skill directories to scan, in priority order.

    User-level (~/.ccos/) takes precedence over project-level.
    """
    dirs: list[tuple[Path, SkillSource]] = []
    home = Path.home()

    # User skills (highest priority)
    user_skills = home / ".ccos" / "skills"
    if user_skills.is_dir():
        dirs.append((user_skills, SkillSource.USER))

    # Project skills
    project_skills = Path(cwd) / ".ccos" / "skills"
    if project_skills.is_dir():
        dirs.append((project_skills, SkillSource.PROJECT))

    # Legacy: user commands
    user_commands = home / ".ccos" / "commands"
    if user_commands.is_dir():
        dirs.append((user_commands, SkillSource.LEGACY_USER))

    # Legacy: project commands
    project_commands = Path(cwd) / ".ccos" / "commands"
    if project_commands.is_dir():
        dirs.append((project_commands, SkillSource.LEGACY_PROJECT))

    return dirs


def load_all_skills(cwd: str) -> list[SkillDefinition]:
    """Load all skills from all skill directories.

    Returns deduplicated list (first-wins by name).
    """
    seen_names: set[str] = set()
    skills: list[SkillDefinition] = []

    for skill_dir, source in get_skill_directories(cwd):
        for skill in _load_skills_from_dir(skill_dir, source):
            if skill.name not in seen_names:
                seen_names.add(skill.name)
                skills.append(skill)

    return skills


def load_skill_by_name(name: str, cwd: str) -> SkillDefinition | None:
    """Load a specific skill by name from disk."""
    for skill in load_all_skills(cwd):
        if skill.name == name:
            return skill
    return None


def _load_skills_from_dir(
    base_dir: Path,
    source: SkillSource,
    prefix: str = "",
) -> list[SkillDefinition]:
    """Recursively load skills from a directory.

    Modern format:  base_dir/<name>/SKILL.md
    Legacy format:  base_dir/<name>.md
    Nested:         base_dir/<ns>/<name>/SKILL.md  ->  name = "ns:name"
    """
    skills: list[SkillDefinition] = []

    if not base_dir.is_dir():
        return skills

    try:
        entries = sorted(base_dir.iterdir())
    except PermissionError:
        return skills

    for entry in entries:
        if entry.name.startswith(".") or entry.name.startswith("_"):
            continue

        if entry.is_dir():
            # Check for SKILL.md in this directory
            skill_file = entry / "SKILL.md"
            if skill_file.is_file():
                name = f"{prefix}{entry.name}" if not prefix else f"{prefix}:{entry.name}"
                skill = _parse_skill_file(skill_file, name, source)
                if skill:
                    skills.append(skill)
            else:
                # Recurse into subdirectory for nested namespaces
                sub_prefix = f"{prefix}:{entry.name}" if prefix else entry.name
                skills.extend(_load_skills_from_dir(entry, source, sub_prefix))

        elif entry.is_file() and entry.suffix == ".md":
            # Legacy: direct .md files (commands style)
            name_stem = entry.stem
            if prefix:
                name_stem = f"{prefix}:{name_stem}"
            skill = _parse_skill_file(entry, name_stem, source)
            if skill:
                skills.append(skill)

    return skills


def _parse_skill_file(
    path: Path,
    name: str,
    source: SkillSource,
) -> SkillDefinition | None:
    """Parse a single skill file into a SkillDefinition."""
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None

    frontmatter, content = parse_frontmatter(raw)

    # Build definition
    skill = SkillDefinition(
        name=name,
        content=content,
        skill_dir=str(path.parent),
        loaded_from=str(path),
        source=source,
    )

    # Parse frontmatter fields
    if "name" in frontmatter:
        skill.display_name = str(frontmatter["name"])

    if "description" in frontmatter:
        skill.description = str(frontmatter["description"])
    elif content:
        # Extract first line/sentence as description
        first_line = content.split("\n")[0].strip()
        if first_line and not first_line.startswith("#"):
            skill.description = first_line[:120]

    if "when_to_use" in frontmatter:
        skill.when_to_use = str(frontmatter["when_to_use"])

    # Arguments
    if "arguments" in frontmatter:
        skill.argument_names = normalize_list_field(frontmatter["arguments"])

    if "argument-hint" in frontmatter:
        skill.argument_hint = str(frontmatter["argument-hint"])
    elif skill.argument_names:
        # Auto-generate hint from argument names
        skill.argument_hint = " ".join(f"[{a}]" for a in skill.argument_names)

    # Tools
    if "allowed-tools" in frontmatter:
        skill.allowed_tools = normalize_list_field(frontmatter["allowed-tools"])

    # Execution
    if "context" in frontmatter:
        ctx_val = str(frontmatter["context"]).lower()
        if ctx_val == "fork":
            skill.context = ExecutionContext.FORK
        else:
            skill.context = ExecutionContext.INLINE

    if "agent" in frontmatter:
        skill.agent = str(frontmatter["agent"])

    if "model" in frontmatter:
        skill.model = str(frontmatter["model"])

    if "effort" in frontmatter:
        skill.effort = str(frontmatter["effort"])

    # Visibility
    if "user-invocable" in frontmatter:
        skill.user_invocable = normalize_bool_field(frontmatter["user-invocable"])

    if "disable-model-invocation" in frontmatter:
        skill.disable_model_invocation = normalize_bool_field(
            frontmatter["disable-model-invocation"], default=False
        )

    # Metadata
    if "version" in frontmatter:
        skill.version = str(frontmatter["version"])

    if "paths" in frontmatter:
        skill.paths = normalize_list_field(frontmatter["paths"])

    # Hooks
    if "hooks" in frontmatter and isinstance(frontmatter["hooks"], dict):
        skill.hooks = frontmatter["hooks"]

    # Shell
    if "shell" in frontmatter:
        skill.shell = str(frontmatter["shell"]).lower()

    return skill


def create_skill_template(
    name: str,
    cwd: str = "",
    description: str = "",
    arguments: str = "",
    allowed_tools: list[str] | None = None,
    user_invocable: bool = True,
) -> Path:
    """Create a new skill from a template.

    Creates ~/.ccos/skills/<name>/SKILL.md with frontmatter.
    Returns the path to the created file.
    """
    skill_dir = get_user_skills_dir() / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_file = skill_dir / "SKILL.md"

    # Build frontmatter
    lines = ["---"]
    if description:
        lines.append(f'description: "{description}"')
    if arguments:
        lines.append(f'arguments: "{arguments}"')
    if allowed_tools:
        lines.append("allowed-tools:")
        for tool in allowed_tools:
            lines.append(f"  - {tool}")
    if not user_invocable:
        lines.append("user-invocable: false")
    lines.append("---")
    lines.append("")
    lines.append(f"# {name}")
    lines.append("")
    lines.append("Your skill instructions go here.")
    lines.append("")
    lines.append("The AI will follow these instructions when the skill is invoked.")
    lines.append("")

    skill_file.write_text("\n".join(lines), encoding="utf-8")
    return skill_file


def delete_skill(name: str, cwd: str = "") -> bool:
    """Delete a skill by name.

    Searches user-level first, then project-level.
    Returns True if deleted, False if not found.
    """
    import shutil

    home = Path.home()

    # User-level skills (~/.ccos/skills/<name>/)
    user_dir = home / ".ccos" / "skills" / name
    if user_dir.is_dir():
        shutil.rmtree(user_dir)
        return True

    # User-level legacy (~/.ccos/commands/<name>.md)
    user_legacy = home / ".ccos" / "commands" / f"{name}.md"
    if user_legacy.is_file():
        user_legacy.unlink()
        return True

    if cwd:
        # Project-level skills
        project_dir = Path(cwd) / ".ccos" / "skills" / name
        if project_dir.is_dir():
            shutil.rmtree(project_dir)
            return True

        # Project-level legacy
        project_legacy = Path(cwd) / ".ccos" / "commands" / f"{name}.md"
        if project_legacy.is_file():
            project_legacy.unlink()
            return True

    return False

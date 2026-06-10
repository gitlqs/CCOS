"""Skill loader — discovers and parses skills from disk.

Skill directories (in priority order):
  1. Managed:  <managed>/.ccos/skills/<name>/SKILL.md      (policy, highest)
  2. User:     ~/.ccos/skills/<name>/SKILL.md
  3. Project:  every <dir>/.ccos/skills from cwd up to the git root
               (most-specific first)
  4. Legacy user:     ~/.ccos/commands/<name>.md
  5. Legacy project:  <cwd>/.ccos/commands/<name>.md  (or <name>/SKILL.md)

Modern /skills/ directories are scanned FLATLY: only <base>/<name>/SKILL.md is
loaded (no recursion, no namespacing, single .md files ignored). Recursive
namespacing and single-.md support apply only to the legacy /commands/ dirs.

Skills are markdown files with optional YAML frontmatter.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import Any

from ccos.skills.arguments import parse_argument_names
from ccos.skills.frontmatter import (
    parse_bool_field,
    parse_frontmatter,
)
from ccos.skills.types import ExecutionContext, SkillDefinition, SkillSource

# Files named SKILL.md (case-insensitive) are the modern skill manifest.
_SKILL_FILE_RE = re.compile(r"^skill\.md$", re.IGNORECASE)
_BRACE_GROUP_RE = re.compile(r"^([^{]*)\{([^}]+)\}(.*)$")
_HEADER_RE = re.compile(r"^#+\s+(.+)$")


def get_user_skills_dir() -> Path:
    """Return the canonical user-level skills directory (~/.ccos/skills)."""
    return Path.home() / ".ccos" / "skills"


def get_managed_skills_dir() -> Path | None:
    """Return the platform managed/policy skills directory, if applicable.

    Mirrors cc's getManagedFilePath() + '.claude/skills' tier, using CCOS's
    '.ccos' config dir. Returns None when no managed root applies.
    """
    if sys.platform == "win32":
        program_data = os.environ.get("PROGRAMDATA")
        if program_data:
            return Path(program_data) / "CCOS" / ".ccos" / "skills"
        return None
    if sys.platform == "darwin":
        return Path("/Library/Application Support/CCOS/.ccos/skills")
    # Linux / other POSIX
    return Path("/etc/ccos/.ccos/skills")


def _find_git_root(start: Path) -> Path | None:
    """Walk up from ``start`` looking for a directory containing .git."""
    current = start.resolve()
    while True:
        if (current / ".git").exists():
            return current
        parent = current.parent
        if parent == current:
            return None
        current = parent


def get_project_dirs_up_to_root(cwd: str) -> list[Path]:
    """Collect every <dir>/.ccos/skills from cwd up to the git root (or home).

    Returns existing directories, most-specific (cwd) first. Stops at the git
    root if cwd is inside a repo, otherwise stops before home. Mirrors cc's
    getProjectDirsUpToHome('skills', cwd).
    """
    home = Path.home().resolve()
    git_root = _find_git_root(Path(cwd))
    current = Path(cwd).resolve()
    dirs: list[Path] = []

    while True:
        # Don't scan home here — it is loaded separately as the user dir.
        if current == home:
            break

        candidate = current / ".ccos" / "skills"
        if candidate.is_dir():
            dirs.append(candidate)

        # Stop after processing the git root.
        if git_root is not None and current == git_root:
            break

        parent = current.parent
        if parent == current:
            break
        current = parent

    return dirs


def get_skill_directories(cwd: str) -> list[tuple[Path, SkillSource]]:
    """Return all skill directories to scan, in priority order.

    Precedence: managed > user > project(deepest..shallowest) > legacy.
    """
    dirs: list[tuple[Path, SkillSource]] = []
    home = Path.home()

    # Managed/policy skills (highest priority)
    managed_skills = get_managed_skills_dir()
    if managed_skills is not None and managed_skills.is_dir():
        dirs.append((managed_skills, SkillSource.MANAGED))

    # User skills
    user_skills = home / ".ccos" / "skills"
    if user_skills.is_dir():
        dirs.append((user_skills, SkillSource.USER))

    # Project skills — walk cwd up to the git root, most-specific first.
    for project_skills in get_project_dirs_up_to_root(cwd):
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


_LEGACY_SOURCES = (SkillSource.LEGACY_USER, SkillSource.LEGACY_PROJECT)


def load_all_skills(cwd: str) -> list[SkillDefinition]:
    """Load all skills from all skill directories.

    Deduplicated by physical file identity (realpath, first-wins) to handle
    symlinks/overlap, then by name (first-wins) so precedence is preserved.
    """
    seen_names: set[str] = set()
    seen_files: set[str] = set()
    skills: list[SkillDefinition] = []

    for skill_dir, source in get_skill_directories(cwd):
        if source in _LEGACY_SOURCES:
            loaded = _load_legacy_skills_from_dir(skill_dir, source)
        else:
            loaded = _load_modern_skills_from_dir(skill_dir, source)

        for skill in loaded:
            # Dedup by physical file (handles symlinks / overlapping dirs).
            try:
                file_id = os.path.realpath(skill.loaded_from)
            except OSError:
                file_id = ""
            if file_id and file_id in seen_files:
                continue

            if skill.name in seen_names:
                continue

            if file_id:
                seen_files.add(file_id)
            seen_names.add(skill.name)
            skills.append(skill)

    return skills


def load_skill_by_name(name: str, cwd: str) -> SkillDefinition | None:
    """Load a specific skill by name from disk."""
    for skill in load_all_skills(cwd):
        if skill.name == name:
            return skill
    return None


def _load_modern_skills_from_dir(
    base_dir: Path,
    source: SkillSource,
) -> list[SkillDefinition]:
    """Flat scan of a modern /skills/ directory.

    Only ``base_dir/<name>/SKILL.md`` is loaded. No recursion, no namespacing,
    and bare ``<name>.md`` files are ignored (matching cc's
    loadSkillsFromSkillsDir).
    """
    skills: list[SkillDefinition] = []

    if not base_dir.is_dir():
        return skills

    try:
        entries = sorted(base_dir.iterdir())
    except (PermissionError, OSError):
        return skills

    for entry in entries:
        if entry.name.startswith(".") or entry.name.startswith("_"):
            continue
        if not entry.is_dir():
            # Single .md files are NOT supported in /skills/ directories.
            continue

        skill_file = entry / "SKILL.md"
        if not skill_file.is_file():
            continue

        skill = _parse_skill_file(skill_file, entry.name, source)
        if skill:
            skills.append(skill)

    return skills


def _load_legacy_skills_from_dir(
    base_dir: Path,
    source: SkillSource,
    prefix: str = "",
) -> list[SkillDefinition]:
    """Recursively load skills from a legacy /commands/ directory.

    Legacy format:  base_dir/<name>.md
    Skill format:   base_dir/<name>/SKILL.md            ->  name = "<name>"
    Nested:         base_dir/<ns>/<name>/SKILL.md       ->  name = "ns:name"
    Nested cmd:     base_dir/<ns>/<name>.md             ->  name = "ns:name"
    """
    skills: list[SkillDefinition] = []

    if not base_dir.is_dir():
        return skills

    try:
        entries = sorted(base_dir.iterdir())
    except (PermissionError, OSError):
        return skills

    for entry in entries:
        if entry.name.startswith(".") or entry.name.startswith("_"):
            continue

        if entry.is_dir():
            # A directory holding a SKILL.md takes the directory's name.
            skill_file = entry / "SKILL.md"
            if skill_file.is_file():
                name = f"{prefix}:{entry.name}" if prefix else entry.name
                skill = _parse_skill_file(skill_file, name, source)
                if skill:
                    skills.append(skill)
            else:
                # Recurse, building the namespace prefix.
                sub_prefix = f"{prefix}:{entry.name}" if prefix else entry.name
                skills.extend(
                    _load_legacy_skills_from_dir(entry, source, sub_prefix)
                )

        elif entry.is_file() and entry.suffix == ".md":
            # Legacy single .md files (commands style).
            name_stem = entry.stem
            if prefix:
                name_stem = f"{prefix}:{name_stem}"
            skill = _parse_skill_file(entry, name_stem, source)
            if skill:
                skills.append(skill)

    return skills


def parse_allowed_tools(value: Any) -> list[str]:
    """Parse the ``allowed-tools`` frontmatter field.

    Mirrors cc's parseSlashCommandToolsFromFrontmatter: accepts a string or a
    YAML list; strings are comma-split while respecting parentheses (so specs
    like ``Bash(git status:*)`` survive); ``*`` collapses the whole list to
    ``['*']``; missing/empty yields ``[]``.
    """
    if value is None:
        return []

    if isinstance(value, list):
        items: list[str] = []
        for entry in value:
            items.extend(_split_tool_string(str(entry)))
    elif isinstance(value, str):
        items = _split_tool_string(value)
    else:
        return []

    if "*" in items:
        return ["*"]
    return items


def _split_tool_string(value: str) -> list[str]:
    """Split a tool string on commas, but not inside parentheses."""
    parts: list[str] = []
    current = ""
    depth = 0
    for ch in value:
        if ch == "(":
            depth += 1
            current += ch
        elif ch == ")":
            depth = max(0, depth - 1)
            current += ch
        elif ch == "," and depth == 0:
            trimmed = current.strip()
            if trimmed:
                parts.append(trimmed)
            current = ""
        else:
            current += ch
    trimmed = current.strip()
    if trimmed:
        parts.append(trimmed)
    return parts


def _expand_braces(pattern: str) -> list[str]:
    """Expand brace groups in a glob pattern (cc's expandBraces)."""
    match = _BRACE_GROUP_RE.match(pattern)
    if not match:
        return [pattern]

    prefix = match.group(1) or ""
    alternatives = match.group(2) or ""
    suffix = match.group(3) or ""

    expanded: list[str] = []
    for alt in alternatives.split(","):
        combined = prefix + alt.strip() + suffix
        expanded.extend(_expand_braces(combined))
    return expanded


def _split_path_in_frontmatter(value: Any) -> list[str]:
    """Comma-split (honoring braces) then brace-expand a paths value.

    Mirrors cc's splitPathInFrontmatter.
    """
    if isinstance(value, list):
        out: list[str] = []
        for item in value:
            out.extend(_split_path_in_frontmatter(item))
        return out
    if not isinstance(value, str):
        return []

    parts: list[str] = []
    current = ""
    brace_depth = 0
    for ch in value:
        if ch == "{":
            brace_depth += 1
            current += ch
        elif ch == "}":
            brace_depth -= 1
            current += ch
        elif ch == "," and brace_depth == 0:
            trimmed = current.strip()
            if trimmed:
                parts.append(trimmed)
            current = ""
        else:
            current += ch
    trimmed = current.strip()
    if trimmed:
        parts.append(trimmed)

    expanded: list[str] = []
    for part in parts:
        if part:
            expanded.extend(_expand_braces(part))
    return expanded


def parse_skill_paths(value: Any) -> list[str]:
    """Parse the ``paths`` frontmatter field into normalized glob patterns.

    Mirrors cc's parseSkillPaths: comma-split honoring braces + brace-expand,
    strip a trailing ``/**`` from each pattern, drop empties, and treat an
    all-``**`` result as no paths (returns []).
    """
    if not value:
        return []

    patterns: list[str] = []
    for pattern in _split_path_in_frontmatter(value):
        if pattern.endswith("/**"):
            pattern = pattern[:-3]
        if pattern:
            patterns.append(pattern)

    if not patterns or all(p == "**" for p in patterns):
        return []

    return patterns


def _extract_description_from_markdown(
    content: str,
    default_label: str,
) -> str:
    """Derive a description from the first non-empty markdown line.

    Strips a leading header marker, truncates to 100 chars (``text[:97] +
    '...'``), and falls back to ``default_label`` if there is no non-empty
    line. Mirrors cc's extractDescriptionFromMarkdown.
    """
    for line in content.split("\n"):
        trimmed = line.strip()
        if trimmed:
            header = _HEADER_RE.match(trimmed)
            text = header.group(1) if header else trimmed
            if len(text) > 100:
                return text[:97] + "..."
            return text
    return default_label


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

    is_legacy = source in _LEGACY_SOURCES
    default_desc_label = "Custom command" if is_legacy else "Skill"

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

    raw_description = frontmatter.get("description")
    if isinstance(raw_description, str) and raw_description.strip():
        skill.description = raw_description.strip()
    else:
        # Fall back to the markdown body (cc's extractDescriptionFromMarkdown).
        skill.description = _extract_description_from_markdown(
            content, default_desc_label
        )

    if "when_to_use" in frontmatter:
        skill.when_to_use = str(frontmatter["when_to_use"])

    # Arguments
    if "arguments" in frontmatter:
        skill.argument_names = parse_argument_names(frontmatter["arguments"])

    # argument-hint comes ONLY from the literal frontmatter field; cc never
    # synthesizes one from the argument names.
    if "argument-hint" in frontmatter:
        skill.argument_hint = str(frontmatter["argument-hint"])

    # Tools
    if "allowed-tools" in frontmatter:
        skill.allowed_tools = parse_allowed_tools(frontmatter["allowed-tools"])

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

    # Visibility — strict cc boolean semantics (only true / "true").
    if "user-invocable" in frontmatter:
        skill.user_invocable = parse_bool_field(frontmatter["user-invocable"])

    if "disable-model-invocation" in frontmatter:
        skill.disable_model_invocation = parse_bool_field(
            frontmatter["disable-model-invocation"]
        )

    # Metadata
    if "version" in frontmatter:
        skill.version = str(frontmatter["version"])

    if "paths" in frontmatter:
        skill.paths = parse_skill_paths(frontmatter["paths"])

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

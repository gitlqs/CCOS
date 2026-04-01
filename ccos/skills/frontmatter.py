"""YAML frontmatter parser for SKILL.md files.

Parses files in the format:
  ---
  description: My skill
  arguments: "foo bar"
  allowed-tools:
    - Bash
    - Read
  ---
  Markdown content here...
"""

from __future__ import annotations

import re
from typing import Any


_FRONTMATTER_RE = re.compile(r"^---\s*\n([\s\S]*?)---\s*\n?", re.MULTILINE)


def parse_frontmatter(markdown: str) -> tuple[dict[str, Any], str]:
    """Parse YAML frontmatter from a markdown string.

    Returns (frontmatter_dict, remaining_content).
    If no frontmatter is found, returns ({}, full_text).
    """
    match = _FRONTMATTER_RE.match(markdown)
    if not match:
        return {}, markdown.strip()

    yaml_text = match.group(1)
    content = markdown[match.end():].strip()

    # Parse YAML manually (avoid pyyaml dependency)
    frontmatter = _parse_simple_yaml(yaml_text)
    return frontmatter, content


def _parse_simple_yaml(text: str) -> dict[str, Any]:
    """Parse simple YAML (flat key-value pairs, lists).

    Handles:
      key: value
      key: "quoted value"
      key:
        - item1
        - item2
      key: |
        multi
        line
    """
    result: dict[str, Any] = {}
    lines = text.split("\n")
    i = 0

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        # Skip empty lines and comments
        if not stripped or stripped.startswith("#"):
            i += 1
            continue

        # Parse key: value
        colon_idx = stripped.find(":")
        if colon_idx == -1:
            i += 1
            continue

        key = stripped[:colon_idx].strip()
        value_part = stripped[colon_idx + 1:].strip()

        if value_part == "" or value_part == "|":
            # Could be a list or block scalar
            is_block_scalar = value_part == "|"
            # Check next lines for list items or block content
            items: list[str] = []
            block_lines: list[str] = []
            j = i + 1
            while j < len(lines):
                next_line = lines[j]
                next_stripped = next_line.strip()
                # Check indentation
                if not next_line or (next_line[0] == " " or next_line[0] == "\t"):
                    if next_stripped.startswith("- "):
                        items.append(_unquote(next_stripped[2:].strip()))
                    elif next_stripped == "-":
                        items.append("")
                    elif is_block_scalar or (not next_stripped.startswith("-") and next_stripped):
                        block_lines.append(next_stripped)
                    elif not next_stripped:
                        if is_block_scalar:
                            block_lines.append("")
                        else:
                            break
                    j += 1
                else:
                    break

            if items:
                result[key] = items
            elif block_lines:
                result[key] = "\n".join(block_lines)
            else:
                result[key] = ""
            i = j
        else:
            # Inline value
            result[key] = _unquote(value_part)
            i += 1

    return result


def _unquote(s: str) -> str:
    """Remove surrounding quotes from a string."""
    if len(s) >= 2:
        if (s[0] == '"' and s[-1] == '"') or (s[0] == "'" and s[-1] == "'"):
            return s[1:-1]
    return s


def normalize_list_field(value: Any) -> list[str]:
    """Normalize a frontmatter field that can be a string or list into a list."""
    if isinstance(value, list):
        return [str(v) for v in value]
    if isinstance(value, str):
        # Could be space-separated or single value
        return [v.strip() for v in value.split() if v.strip()]
    return []


def normalize_bool_field(value: Any, default: bool = True) -> bool:
    """Normalize a frontmatter field to boolean."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.lower() in ("true", "yes", "1", "on")
    return default

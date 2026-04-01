"""Argument parsing and substitution for skills.

Supports:
  $ARGUMENTS          -> Full argument string
  $ARGUMENTS[0]       -> First argument (0-indexed)
  $ARGUMENTS[1]       -> Second argument, etc.
  $0, $1, $2          -> Shorthand for indexed args
  $name               -> Named argument (from frontmatter 'arguments' field)
  ${CCOS_SKILL_DIR}   -> Skill's directory path
  ${CCOS_SESSION_ID}  -> Current session ID
"""

from __future__ import annotations

import re
import shlex


def parse_arguments(args: str) -> list[str]:
    """Parse argument string into a list, respecting quotes.

    Uses shlex for proper quote handling:
      'foo "hello world" baz'  ->  ["foo", "hello world", "baz"]
      'foo bar baz'            ->  ["foo", "bar", "baz"]

    Falls back to whitespace split on parse failure.
    """
    if not args or not args.strip():
        return []
    try:
        return shlex.split(args)
    except ValueError:
        return args.split()


def substitute_arguments(
    content: str,
    args_str: str,
    argument_names: list[str] | None = None,
) -> str:
    """Substitute argument placeholders in skill content.

    Replaces:
      $ARGUMENTS       -> full args string
      $ARGUMENTS[N]    -> Nth argument (0-indexed)
      $N               -> Nth argument (0-indexed, single/double digit)
      $name            -> Named argument by position (from argument_names)
    """
    if not content:
        return content

    parsed = parse_arguments(args_str)

    # Replace $ARGUMENTS[N] first (before $ARGUMENTS)
    def replace_indexed(m: re.Match) -> str:
        idx = int(m.group(1))
        if idx < len(parsed):
            return parsed[idx]
        return m.group(0)  # Leave unreplaced if out of range

    content = re.sub(r"\$ARGUMENTS\[(\d+)\]", replace_indexed, content)

    # Replace $ARGUMENTS with full string
    content = content.replace("$ARGUMENTS", args_str)

    # Replace $N (positional shorthand: $0, $1, ... $99)
    def replace_positional(m: re.Match) -> str:
        idx = int(m.group(1))
        if idx < len(parsed):
            return parsed[idx]
        return m.group(0)

    content = re.sub(r"\$(\d{1,2})(?!\w)", replace_positional, content)

    # Replace named arguments ($name from frontmatter 'arguments' field)
    if argument_names:
        for i, name in enumerate(argument_names):
            if name and i < len(parsed):
                # Replace $name but not when part of a longer word
                # Use word boundary matching
                pattern = re.compile(r"\$" + re.escape(name) + r"(?!\w)")
                content = pattern.sub(parsed[i], content)

    return content


def substitute_variables(
    content: str,
    skill_dir: str = "",
    session_id: str = "",
) -> str:
    """Substitute environment variables in skill content.

    Replaces:
      ${CCOS_SKILL_DIR}   -> skill's directory path (forward slashes)
      ${CCOS_SESSION_ID}  -> current session ID
    """
    if not content:
        return content

    # Normalize to forward slashes for cross-platform consistency
    normalized_dir = skill_dir.replace("\\", "/")

    content = content.replace("${CCOS_SKILL_DIR}", normalized_dir)
    content = content.replace("${CCOS_SESSION_ID}", session_id)

    return content

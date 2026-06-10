"""Argument parsing and substitution for skills.

Supports:
  $ARGUMENTS          -> Full argument string
  $ARGUMENTS[0]       -> First argument (0-indexed)
  $ARGUMENTS[1]       -> Second argument, etc.
  $0, $1, $2          -> Shorthand for indexed args
  $name               -> Named argument (from frontmatter 'arguments' field)
  ${CCOS_SKILL_DIR}   -> Skill's directory path
  ${CCOS_SESSION_ID}  -> Current session ID

Mirrors cc's argumentSubstitution.ts substitution semantics: out-of-range
placeholders are replaced with the empty string (never left literal), the
substitution order is named -> $ARGUMENTS[N] -> $N -> $ARGUMENTS, and when the
content contains no placeholder at all the raw args are appended as an
"ARGUMENTS: ..." trailer.
"""

from __future__ import annotations

import re
import shlex

_NUMERIC_RE = re.compile(r"^\d+$")


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


def parse_argument_names(argument_names: str | list[str] | None) -> list[str]:
    """Parse argument names from the frontmatter 'arguments' field.

    Accepts either a whitespace-separated string or a list of strings, and
    filters out empty and numeric-only names (numeric names conflict with the
    $0/$1 positional shorthand). Mirrors cc's parseArgumentNames.
    """
    if not argument_names:
        return []

    def is_valid(name: str) -> bool:
        return (
            isinstance(name, str)
            and name.strip() != ""
            and not _NUMERIC_RE.match(name)
        )

    if isinstance(argument_names, list):
        return [str(n) for n in argument_names if is_valid(str(n))]
    if isinstance(argument_names, str):
        return [n for n in argument_names.split() if is_valid(n)]
    return []


def substitute_arguments(
    content: str,
    args_str: str,
    argument_names: list[str] | None = None,
    append_if_no_placeholder: bool = True,
) -> str:
    """Substitute argument placeholders in skill content.

    Replaces (in cc's order):
      $name            -> Named argument by position (from argument_names)
      $ARGUMENTS[N]    -> Nth argument (0-indexed)
      $N               -> Nth argument (0-indexed)
      $ARGUMENTS       -> full args string (replaced last)

    Out-of-range placeholders are replaced with the empty string. If the
    content contains no placeholder at all and ``append_if_no_placeholder`` is
    true with non-empty args, the raw args are appended as
    ``\\n\\nARGUMENTS: <args>``.
    """
    if not content:
        return content

    parsed = parse_arguments(args_str)
    original = content

    # 1. Named arguments ($name, by declared position). Match $name but not
    #    $name[...] or $nameXxx (negative lookahead for '[' and word chars).
    if argument_names:
        for i, name in enumerate(argument_names):
            if not name:
                continue
            pattern = re.compile(r"\$" + re.escape(name) + r"(?![\[\w])")
            value = parsed[i] if i < len(parsed) else ""
            content = pattern.sub(lambda _m, v=value: v, content)

    # 2. Indexed arguments ($ARGUMENTS[N])
    def replace_indexed(m: re.Match) -> str:
        idx = int(m.group(1))
        return parsed[idx] if idx < len(parsed) else ""

    content = re.sub(r"\$ARGUMENTS\[(\d+)\]", replace_indexed, content)

    # 3. Positional shorthand ($0, $1, ...)
    def replace_positional(m: re.Match) -> str:
        idx = int(m.group(1))
        return parsed[idx] if idx < len(parsed) else ""

    content = re.sub(r"\$(\d+)(?!\w)", replace_positional, content)

    # 4. Full argument string ($ARGUMENTS) -- replaced last, with the raw args.
    content = content.replace("$ARGUMENTS", args_str)

    # If nothing was substituted and we have args, append them as a trailer.
    if content == original and append_if_no_placeholder and args_str:
        content = content + f"\n\nARGUMENTS: {args_str}"

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

"""Environment context for system prompt injection."""

from __future__ import annotations

import os
import sys

from ccos.utils.platform_info import get_git_info, get_os_version, get_platform, get_shell


def _get_shell_info_line() -> str:
    """Build the Shell line (mirrors CC's getShellInfoLine)."""
    shell = get_shell()
    shell_name = os.path.basename(shell) if shell else "unknown"
    if get_platform() == "windows":
        return (
            f"Shell: {shell_name} (use Unix shell syntax, not Windows — "
            "e.g., /dev/null not NUL, forward slashes in paths)"
        )
    return f"Shell: {shell_name}"


def get_env_info(cwd: str, model: str, provider_name: str = "anthropic") -> str:
    """Build environment information block (like CC's computeSimpleEnvInfo).

    Items are rendered with CC's prependBullets convention: plain strings
    become top-level bullets (' - item'), while a string wrapped in a list
    becomes an indented bullet ('  - item').
    """
    git = get_git_info(cwd)
    is_git = bool(git.get("is_git_repo", False))

    # Each entry is either a str (top-level bullet) or a list[str] (indented).
    items: list[str | list[str]] = [
        f"Primary working directory: {cwd}",
        [f"Is a git repository: {str(is_git).lower()}"],
        f"Platform: {sys.platform}",
        _get_shell_info_line(),
        f"OS Version: {get_os_version()}",
    ]

    # Multi-provider: keep a Provider line, then mirror CC's modelDescription
    # sentence (generic — not Anthropic-specific).
    items.append(f"Provider: {provider_name}")
    items.append(f"You are powered by the model named {model}.")

    lines = ["# Environment", "You have been invoked in the following environment: "]
    for item in items:
        if isinstance(item, list):
            for sub in item:
                lines.append(f"  - {sub}")
        else:
            lines.append(f" - {item}")

    return "\n".join(lines)

"""Environment context for system prompt injection."""

from __future__ import annotations

import os
import sys
from datetime import date

from ccos.utils.platform_info import get_git_info, get_os_version, get_platform, get_shell


def get_env_info(cwd: str, model: str, provider_name: str = "anthropic") -> str:
    """Build environment information block (like CC's computeSimpleEnvInfo)."""
    lines = [
        "# Environment",
        "You have been invoked in the following environment:",
        f" - Primary working directory: {cwd}",
    ]

    # Git info
    git = get_git_info(cwd)
    lines.append(f"   - Is a git repository: {str(git.get('is_git_repo', False)).lower()}")
    if git.get("is_git_repo"):
        branch = git.get("branch", "")
        if branch:
            lines.append(f"   - Git branch: {branch}")

    # Platform
    platform = get_platform()
    lines.append(f" - Platform: {sys.platform}")
    shell = get_shell()
    if shell:
        shell_name = os.path.basename(shell)
        lines.append(f" - Shell: {shell_name}")
        if platform == "windows":
            lines.append("   (use Unix shell syntax, not Windows — e.g., forward slashes in paths)")

    lines.append(f" - OS Version: {get_os_version()}")
    lines.append(f" - Provider: {provider_name}")
    lines.append(f" - Model: {model}")
    lines.append(f" - Current date: {date.today().isoformat()}")
    lines.append(f" - Python: {sys.version.split()[0]}")

    return "\n".join(lines)

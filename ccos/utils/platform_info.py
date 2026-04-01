"""Platform detection utilities."""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys


def get_platform() -> str:
    """Return 'windows', 'macos', or 'linux'."""
    p = sys.platform
    if p == "win32":
        return "windows"
    if p == "darwin":
        return "macos"
    return "linux"


def get_os_version() -> str:
    return platform.platform()


def get_shell() -> str:
    shell = os.environ.get("SHELL", "")
    if not shell and get_platform() == "windows":
        shell = os.environ.get("COMSPEC", "cmd.exe")
    return shell


def has_ripgrep() -> bool:
    return shutil.which("rg") is not None


def _run_git(args: list[str], cwd: str) -> str:
    """Run a git command and return stdout as a string.

    Uses encoding='utf-8' explicitly — git outputs UTF-8 on all platforms,
    but Windows defaults to the system codepage (e.g., GBK) which breaks
    on non-ASCII commit messages.
    """
    try:
        r = subprocess.run(
            args, cwd=cwd, capture_output=True,
            timeout=5, encoding="utf-8", errors="replace",
        )
        if r.returncode == 0 and r.stdout:
            return r.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass
    return ""


def get_git_info(cwd: str) -> dict[str, str | bool]:
    """Gather basic git information for the working directory."""
    info: dict[str, str | bool] = {"is_git_repo": False}
    try:
        check = _run_git(["git", "rev-parse", "--is-inside-work-tree"], cwd)
        if check != "true":
            return info
        info["is_git_repo"] = True
        info["branch"] = _run_git(["git", "branch", "--show-current"], cwd)
        info["recent_commits"] = _run_git(["git", "log", "--oneline", "-5"], cwd)
        info["status"] = _run_git(["git", "status", "--short"], cwd)
    except Exception:
        pass
    return info

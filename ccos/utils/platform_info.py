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


def get_git_info(cwd: str) -> dict[str, str | bool]:
    """Gather basic git information for the working directory."""
    info: dict[str, str | bool] = {"is_git_repo": False}
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=cwd, capture_output=True, text=True, timeout=5,
        )
        if result.returncode != 0:
            return info
        info["is_git_repo"] = True

        # Branch
        r = subprocess.run(
            ["git", "branch", "--show-current"],
            cwd=cwd, capture_output=True, text=True, timeout=5,
        )
        info["branch"] = r.stdout.strip() if r.returncode == 0 else ""

        # Recent commits (last 5)
        r = subprocess.run(
            ["git", "log", "--oneline", "-5"],
            cwd=cwd, capture_output=True, text=True, timeout=5,
        )
        info["recent_commits"] = r.stdout.strip() if r.returncode == 0 else ""

        # Status
        r = subprocess.run(
            ["git", "status", "--short"],
            cwd=cwd, capture_output=True, text=True, timeout=5,
        )
        info["status"] = r.stdout.strip() if r.returncode == 0 else ""

    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return info

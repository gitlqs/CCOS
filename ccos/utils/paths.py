"""Path handling utilities."""

from __future__ import annotations

import os
from pathlib import Path


def expand_path(p: str, cwd: str | None = None) -> str:
    """Expand ``~`` and make relative paths absolute against *cwd*."""
    expanded = os.path.expanduser(p)
    if os.path.isabs(expanded):
        return os.path.normpath(expanded)
    base = cwd or os.getcwd()
    return os.path.normpath(os.path.join(base, expanded))


def to_relative(p: str, cwd: str | None = None) -> str:
    """Convert absolute path to relative (for display / token saving)."""
    base = cwd or os.getcwd()
    try:
        return os.path.relpath(p, base)
    except ValueError:
        # On Windows different drives can't be made relative
        return p


def ensure_parent(p: str) -> None:
    Path(p).parent.mkdir(parents=True, exist_ok=True)

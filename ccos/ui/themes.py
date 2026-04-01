"""Color themes matching Claude Code's exact palette."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

ThemeName = Literal["dark", "light", "dark-ansi", "light-ansi"]


@dataclass(frozen=True)
class ThemeColors:
    """Complete color palette for one theme."""

    # Brand
    claude: str  # Claude orange — header text, accents
    clawd_body: str  # Clawd mascot body
    clawd_background: str  # Clawd eye background

    # UI chrome
    bash_border: str  # Bash mode accent
    suggestion: str  # Autocomplete suggestion
    success: str
    error: str
    warning: str
    inactive: str  # Disabled / secondary text
    subtle: str  # Very faint text
    text: str  # Primary text

    # Permission mode colors
    mode_default: str  # Default (confirm) mode
    mode_auto: str  # Auto-accept mode
    mode_plan: str  # Plan mode


# ── Dark theme (RGB) ─────────────────────────────────────────────────
DARK = ThemeColors(
    claude="rgb(215,119,87)",
    clawd_body="rgb(215,119,87)",
    clawd_background="rgb(0,0,0)",
    bash_border="rgb(255,0,135)",
    suggestion="rgb(87,105,247)",
    success="rgb(44,187,93)",
    error="rgb(255,85,85)",
    warning="rgb(255,170,66)",
    inactive="rgb(102,102,102)",
    subtle="rgb(175,175,175)",
    text="rgb(255,255,255)",
    mode_default="rgb(215,119,87)",
    mode_auto="rgb(44,187,93)",
    mode_plan="rgb(87,105,247)",
)

# ── Light theme (RGB) ────────────────────────────────────────────────
LIGHT = ThemeColors(
    claude="rgb(215,119,87)",
    clawd_body="rgb(215,119,87)",
    clawd_background="rgb(0,0,0)",
    bash_border="rgb(255,0,135)",
    suggestion="rgb(87,105,247)",
    success="rgb(44,122,57)",
    error="rgb(171,43,63)",
    warning="rgb(150,108,30)",
    inactive="rgb(102,102,102)",
    subtle="rgb(175,175,175)",
    text="rgb(0,0,0)",
    mode_default="rgb(215,119,87)",
    mode_auto="rgb(44,122,57)",
    mode_plan="rgb(87,105,247)",
)

# ── ANSI fallbacks (16-color safe) ───────────────────────────────────
DARK_ANSI = ThemeColors(
    claude="bright_red",
    clawd_body="bright_red",
    clawd_background="black",
    bash_border="magenta",
    suggestion="blue",
    success="green",
    error="red",
    warning="yellow",
    inactive="bright_black",
    subtle="bright_black",
    text="white",
    mode_default="bright_red",
    mode_auto="green",
    mode_plan="blue",
)

LIGHT_ANSI = ThemeColors(
    claude="bright_red",
    clawd_body="bright_red",
    clawd_background="black",
    bash_border="magenta",
    suggestion="blue",
    success="green",
    error="red",
    warning="yellow",
    inactive="bright_black",
    subtle="bright_black",
    text="black",
    mode_default="bright_red",
    mode_auto="green",
    mode_plan="blue",
)

_THEMES: dict[ThemeName, ThemeColors] = {
    "dark": DARK,
    "light": LIGHT,
    "dark-ansi": DARK_ANSI,
    "light-ansi": LIGHT_ANSI,
}


def get_theme(name: ThemeName = "dark") -> ThemeColors:
    """Return the named theme, defaulting to dark."""
    return _THEMES.get(name, DARK)


def detect_theme() -> ThemeName:
    """Auto-detect whether terminal background is light or dark.

    Simple heuristic: check COLORFGBG env var (set by some terminals).
    Falls back to 'dark' since most modern terminals are dark.
    """
    import os

    colorfgbg = os.environ.get("COLORFGBG", "")
    if colorfgbg:
        parts = colorfgbg.split(";")
        if len(parts) >= 2:
            try:
                bg = int(parts[-1])
                # High value = light background
                if bg >= 8:
                    return "light"
            except ValueError:
                pass
    return "dark"

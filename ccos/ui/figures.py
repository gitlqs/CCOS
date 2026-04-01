"""Unicode symbols used throughout the UI — matches Claude Code's figures.ts."""

from __future__ import annotations

import sys

# Platform-aware circle glyph
BLACK_CIRCLE = "\u23fa" if sys.platform == "darwin" else "\u25cf"  # ⏺ / ●
BULLET_OPERATOR = "\u2219"  # ∙
TEARDROP_ASTERISK = "\u273b"  # ✻
UP_ARROW = "\u2191"  # ↑
DOWN_ARROW = "\u2193"  # ↓
LIGHTNING_BOLT = "\u21af"  # ↯  (fast mode)
EFFORT_LOW = "\u25cb"  # ○
EFFORT_MEDIUM = "\u25d0"  # ◐
EFFORT_HIGH = "\u25cf"  # ●
EFFORT_MAX = "\u25c9"  # ◉

# Media / trigger status
PLAY_ICON = "\u25b6"  # ▶
PAUSE_ICON = "\u23f8"  # ⏸

# MCP subscription
REFRESH_ARROW = "\u21bb"  # ↻
CHANNEL_ARROW = "\u2190"  # ←
INJECTED_ARROW = "\u2192"  # →
FORK_GLYPH = "\u2442"  # ⑂

# Review status (diamond states)
DIAMOND_OPEN = "\u25c7"  # ◇  running
DIAMOND_FILLED = "\u25c6"  # ◆  completed
REFERENCE_MARK = "\u203b"  # ※

# Issue flag
FLAG_ICON = "\u2691"  # ⚑

# Blockquote / horizontal rule
BLOCKQUOTE_BAR = "\u258e"  # ▎
HEAVY_HORIZONTAL = "\u2501"  # ━

# Prompt character — CC uses `figures.pointer` which renders as ❯
POINTER = "\u276f"  # ❯

# Bridge status
BRIDGE_SPINNER_FRAMES = [
    "\u00b7|\u00b7",   # ·|·
    "\u00b7/\u00b7",   # ·/·
    "\u00b7\u2014\u00b7",  # ·—·
    "\u00b7\\\u00b7",  # ·\·
]
BRIDGE_READY = "\u00b7\u2714\ufe0e\u00b7"  # ·✔·
BRIDGE_FAILED = "\u00d7"  # ×

# Block-drawing characters used in Clawd ASCII art
FULL_BLOCK = "\u2588"  # █
LIGHT_SHADE = "\u2591"  # ░
MEDIUM_SHADE = "\u2592"  # ▒
DARK_SHADE = "\u2593"  # ▓
RIGHT_HALF = "\u2590"  # ▐
LEFT_HALF = "\u258c"  # ▌
UPPER_LEFT = "\u259b"  # ▛
UPPER_RIGHT = "\u259c"  # ▜
LOWER_LEFT = "\u2599"  # ▙
LOWER_RIGHT = "\u259f"  # ▟
QUAD_UPPER_LEFT = "\u2598"  # ▘
QUAD_UPPER_RIGHT = "\u259d"  # ▝ (note: named in Unicode as "quadrant upper right")
QUAD_LOWER_LEFT = "\u2596"  # ▖
QUAD_LOWER_RIGHT = "\u2597"  # ▗

# Ellipsis used in welcome banner separator
ELLIPSIS = "\u2026"  # …

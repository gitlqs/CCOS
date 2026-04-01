"""Rich-based terminal output renderer — 1:1 Claude Code UI."""

from __future__ import annotations

import os
import re
from typing import Any

from rich.console import Console
from rich.markup import escape
from rich.markdown import Markdown
from rich.panel import Panel
from rich.style import Style
from rich.syntax import Syntax
from rich.text import Text
from rich.theme import Theme

from ccos import __version__
from ccos.providers.base import ToolCallContent
from ccos.ui.figures import (
    BLOCKQUOTE_BAR,
    ELLIPSIS,
    POINTER,
)
from ccos.ui.themes import ThemeColors, detect_theme, get_theme

# ── Rich theme (based on CC dark palette) ────────────────────────────

_THEME = Theme({
    "tool.name": "bold cyan",
    "tool.param": "dim",
    "tool.error": "bold red",
    "status": "dim",
    "cost": "dim green",
    "thinking": "dim italic magenta",
    "claude": "rgb(215,119,87)",
    "clawd_body": "rgb(215,119,87)",
    "dim": "dim",
})

# Tool display icons
_TOOL_ICONS = {
    "Bash": "$ ",
    "Read": "Read ",
    "Write": "Write ",
    "Edit": "Edit ",
    "Glob": "Glob ",
    "Grep": "Grep ",
    "Agent": "Agent ",
    "WebFetch": "Fetch ",
    "WebSearch": "Search ",
    "TodoWrite": "Todo ",
    "AskUserQuestion": "Ask ",
    "NotebookEdit": "Notebook ",
    "PowerShell": "PS> ",
    "EnterPlanMode": "Plan ",
    "ExitPlanMode": "Plan ",
    "TaskOutput": "Task ",
    "TaskStop": "Task ",
}


# ═══════════════════════════════════════════════════════════════════════
# Welcome banner — exact Claude Code dark-theme ASCII art (58 cols wide)
# ═══════════════════════════════════════════════════════════════════════

def _build_welcome_dark(version: str, colors: ThemeColors) -> Text:
    """Build the dark-theme welcome banner matching WelcomeV2.tsx."""
    # CC dark theme banner — each line is 58 chars wide
    # Colors: claude (orange) for header + clawd body, dim for shading, bold for * accents
    t = Text()
    c = Style(color=colors.claude)        # Claude orange
    d = Style(dim=True)                    # dim (shading)
    b = Style(bold=True)                   # bold * accents
    cb = Style(color=colors.clawd_body)    # clawd body
    cb_bg = Style(color=colors.clawd_body, bgcolor=colors.clawd_background)  # eyes

    # Line 1: Welcome to Claude Code v{version}
    t.append("Welcome to Claude Code", style=c)
    t.append(f" v{version} ", style=d)
    t.append("\n")

    # Line 2: ellipsis separator (58 × …)
    t.append(ELLIPSIS * 58 + "\n")

    # Line 3: empty
    t.append(" " * 58 + "\n")

    # Line 4:      *                                       █████▓▓░
    t.append("     ")
    t.append("*", style=b)
    t.append("                                       █████▓▓░     \n")

    # Line 5:                                  *         ███▓░     ░░
    t.append("                                 ")
    t.append("*", style=b)
    t.append("         ███▓░     ░░   \n")

    # Line 6:             ░░░░░░                        ███▓░
    t.append("            ░░░░░░                        ███▓░           \n")

    # Line 7:     ░░░   ░░░░░░░░░░                      ███▓░
    t.append("    ░░░   ░░░░░░░░░░                      ███▓░           \n")

    # Line 8:    ░░░░░░░░░░░░░░░░░░░    *                ██▓░░      ▓
    t.append("   ░░░░░░░░░░░░░░░░░░░    ")
    t.append("*", style=b)
    t.append("                ██▓░░      ▓   \n")

    # Line 9:                                              ░▓▓███▓▓░
    t.append("                                             ░▓▓███▓▓░    \n")

    # Line 10: *                                 ░░░░
    t.append(" ", style=d)
    t.append("*", style=d)
    t.append("                                 ░░░░                   \n", style=d)

    # Line 11:                                  ░░░░░░░░
    t.append("                                 ░░░░░░░░                 \n", style=d)

    # Line 12:                                ░░░░░░░░░░░░░░░░
    t.append("                               ░░░░░░░░░░░░░░░░           \n", style=d)

    # Line 13: clawd body row 1:       █████████
    t.append("      ")
    t.append(" █████████ ", style=cb)
    t.append("                                       ")
    t.append("*", style=d)
    t.append(" \n")

    # Line 14: clawd eyes row:       ██▄█████▄██
    t.append("      ")
    t.append("██▄█████▄██", style=cb_bg)
    t.append("                        ")
    t.append("*", style=b)
    t.append("                \n")

    # Line 15: clawd body row 2:       █████████
    t.append("      ")
    t.append(" █████████ ", style=cb)
    t.append("     ")
    t.append("*", style=b)
    t.append("                                   \n")

    # Line 16: footer with clawd feet
    t.append(ELLIPSIS * 7)
    t.append("█ █   █ █", style=cb)
    t.append(ELLIPSIS * 42 + "\n")

    return t


def _build_welcome_light(version: str, colors: ThemeColors) -> Text:
    """Build the light-theme welcome banner matching WelcomeV2.tsx."""
    t = Text()
    c = Style(color=colors.claude)
    d = Style(dim=True)
    cb = Style(color=colors.clawd_body)
    cb_bg = Style(color=colors.clawd_body, bgcolor=colors.clawd_background)

    # Header
    t.append("Welcome to Claude Code", style=c)
    t.append(f" v{version} ", style=d)
    t.append("\n")
    t.append(ELLIPSIS * 58 + "\n")
    t.append(" " * 58 + "\n")
    t.append(" " * 58 + "\n")
    t.append(" " * 58 + "\n")

    # Light theme uses only left-side shading
    t.append("            ░░░░░░                                        \n")
    t.append("    ░░░   ░░░░░░░░░░                                      \n")
    t.append("   ░░░░░░░░░░░░░░░░░░░                                    \n")
    t.append(" " * 58 + "\n")

    # Right-side gradient + clawd
    t.append("                           ░░░░                     ██    \n", style=d)
    t.append("                         ░░░░░░░░░░               ██▒▒██  \n", style=d)
    t.append("                                            ▒▒      ██   ▒\n")

    t.append("      ")
    t.append(" █████████ ", style=cb)
    t.append("                         ▒▒░░▒▒      ▒ ▒▒\n")

    t.append("      ")
    t.append("██▄█████▄██", style=cb_bg)
    t.append("                           ▒▒         ▒▒ \n")

    t.append("      ")
    t.append(" █████████ ", style=cb)
    t.append("                          ░          ▒   \n")

    # Footer
    t.append(ELLIPSIS * 7)
    t.append("█ █   █ █", style=cb)
    t.append(ELLIPSIS * 26)
    t.append("░")
    t.append(ELLIPSIS * 9)
    t.append("▒")
    t.append(ELLIPSIS * 4)
    t.append("\n")

    return t


class Renderer:
    """Render AI responses and tool interactions to the terminal."""

    def __init__(self, console: Console | None = None, theme_name: str | None = None):
        self.console = console or Console(theme=_THEME)
        self._streaming_text = ""
        self._thinking_active = False

        # Resolve theme
        resolved = theme_name or detect_theme()
        self.theme = get_theme(resolved)  # type: ignore[arg-type]
        self._theme_name = resolved

    def print_welcome(self, model: str, provider: str, cwd: str) -> None:
        """Print the CC-style welcome banner with Clawd ASCII art."""
        if self._theme_name in ("light", "light-ansi", "light-daltonized"):
            banner = _build_welcome_light(__version__, self.theme)
        else:
            banner = _build_welcome_dark(__version__, self.theme)

        self.console.print(banner)
        self.console.print(f"  [dim]Provider:[/dim] [cyan]{provider}[/cyan]  [dim]Model:[/dim] [cyan]{model}[/cyan]")
        self.console.print(f"  [dim]cwd:[/dim] {cwd}")
        self.console.print(f"  [dim]Type [cyan]/help[/cyan] for available commands[/dim]")
        self.console.print()

    def print_text_chunk(self, text: str) -> None:
        """Print a streaming text chunk (no newline, raw)."""
        if self._thinking_active:
            self._thinking_active = False
            self.console.print()
        self.console.print(text, end="", highlight=False)
        self._streaming_text += text

    def flush_streaming(self) -> None:
        """End streaming output."""
        if self._streaming_text:
            self.console.print()
            self._streaming_text = ""
        if self._thinking_active:
            self._thinking_active = False
            self.console.print()

    def print_markdown(self, text: str) -> None:
        """Render complete markdown text."""
        if text.strip():
            self.console.print(Markdown(text))

    def print_tool_call(self, tc: ToolCallContent) -> None:
        """Show a tool invocation — CC style."""
        icon = _TOOL_ICONS.get(tc.name, "")
        params = tc.input

        # Bash: show command in syntax-highlighted panel
        if tc.name == "Bash" and "command" in params:
            cmd = params["command"]
            desc = params.get("description", "")
            header = f"[tool.name]{icon}{tc.name}[/tool.name]"
            if desc:
                header += f"  [dim]{desc}[/dim]"
            self.console.print(Panel(
                Syntax(cmd, "bash", theme="monokai", word_wrap=True),
                title=header,
                border_style="dim cyan",
                padding=(0, 1),
            ))
            return

        # File-oriented tools: show path prominently
        if tc.name in ("Read", "Write", "Edit", "Glob", "Grep"):
            path = params.get("file_path") or params.get("path") or params.get("pattern") or ""
            detail_parts = []
            for k, v in params.items():
                if k in ("file_path", "path", "pattern"):
                    continue
                if isinstance(v, str) and len(v) > 80:
                    v = v[:77] + "..."
                detail_parts.append(f"{k}={v}")
            detail = "  ".join(detail_parts)
            header = f"[tool.name]{icon}{tc.name}[/tool.name] [bold]{path}[/bold]"
            if detail:
                self.console.print(Panel(
                    f"[tool.param]{detail}[/tool.param]",
                    title=header,
                    border_style="dim cyan",
                    padding=(0, 1),
                ))
            else:
                self.console.print(f"  {header}")
            return

        # Generic tool display
        params_str = ""
        for k, v in params.items():
            if isinstance(v, str) and len(v) > 120:
                v_display = v[:117] + "..."
            else:
                v_display = str(v)
            params_str += f"  {k}: {v_display}\n"

        self.console.print(Panel(
            f"[tool.name]{icon}{tc.name}[/tool.name]\n[tool.param]{params_str.rstrip()}[/tool.param]",
            title="[dim]Tool Call[/dim]",
            border_style="dim cyan",
            padding=(0, 1),
        ))

    def print_tool_result(self, tool_name: str, content: str, is_error: bool = False) -> None:
        """Show a tool result."""
        style = "red" if is_error else "green"
        icon = _TOOL_ICONS.get(tool_name, "")
        title = f"[dim]{icon}{tool_name} {'Error' if is_error else 'Result'}[/dim]"

        # Truncate long results for display
        max_display = 3000
        if len(content) > max_display:
            display = content[:max_display] + f"\n\n... ({len(content):,} total characters)"
        else:
            display = content

        # File content (cat -n format)
        if tool_name == "Read" and not is_error and _looks_like_file_content(display):
            self.console.print(Panel(
                Syntax(display, "text", theme="monokai", line_numbers=False),
                title=title,
                border_style=f"dim {style}",
                padding=(0, 1),
            ))
            return

        # Diff output from Edit
        if tool_name == "Edit" and not is_error:
            self.console.print(Panel(
                _format_edit_result(display),
                title=title,
                border_style=f"dim {style}",
                padding=(0, 1),
            ))
            return

        self.console.print(Panel(
            display,
            title=title,
            border_style=f"dim {style}",
            padding=(0, 1),
        ))

    def print_thinking(self, text: str) -> None:
        """Show thinking content."""
        self._thinking_active = True
        self.console.print(f"[thinking]{text}[/thinking]", end="")

    def print_error(self, message: str) -> None:
        self.console.print(f"[bold red]Error:[/bold red] {message}")

    def print_status(self, message: str) -> None:
        self.console.print(f"[status]{message}[/status]")

    def print_cost(self, summary: str) -> None:
        self.console.print(f"[cost]{summary}[/cost]")

    def print_permission_request(self, tool_name: str, params: dict[str, Any]) -> None:
        """Show a permission request panel."""
        icon = _TOOL_ICONS.get(tool_name, "")
        params_str = "\n".join(f"  {k}: {v}" for k, v in params.items())
        self.console.print(Panel(
            f"[tool.name]{icon}{tool_name}[/tool.name]\n[tool.param]{params_str}[/tool.param]",
            title="[yellow]Permission Required[/yellow]",
            border_style="yellow",
            padding=(0, 1),
        ))

    def print_footer_hint(self, mode: str = "default") -> None:
        """Print the footer shortcut hints like CC does."""
        t = Text()
        t.append("? ", style="dim")
        t.append("for shortcuts", style="dim")
        self.console.print(t)


# ── Helpers ──────────────────────────────────────────────────────────

def _looks_like_file_content(text: str) -> bool:
    """Check if text looks like cat -n output (line numbers)."""
    lines = text.split("\n", 5)
    if len(lines) < 2:
        return False
    return all(re.match(r'^\s*\d+\t', line) for line in lines[:3] if line.strip())


def _format_edit_result(text: str) -> Text:
    """Format an edit result with diff-like coloring."""
    result = Text()
    for line in text.split("\n"):
        if line.startswith("+") or line.startswith("Added") or "created" in line.lower():
            result.append(line + "\n", style="green")
        elif line.startswith("-") or line.startswith("Removed"):
            result.append(line + "\n", style="red")
        elif line.startswith("@"):
            result.append(line + "\n", style="cyan")
        else:
            result.append(line + "\n")
    return result

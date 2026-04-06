"""Rich-based terminal output renderer — 1:1 Claude Code UI."""

from __future__ import annotations

import os
import re
from typing import Any

from rich.console import Console
from rich.markup import escape
from rich.markdown import Markdown, Heading

# Override rich.markdown Heading to be left-aligned
Heading.LEVEL_ALIGN = {
    "h1": "left",
    "h2": "left",
    "h3": "left",
    "h4": "left",
    "h5": "left",
    "h6": "left",
}
from rich.padding import Padding
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
    "brand": "rgb(0,200,180)",
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
# Welcome banner — original CCOS circuit-robot design (ASCII safe)
# ═══════════════════════════════════════════════════════════════════════

def _build_welcome_dark(version: str, colors: ThemeColors) -> Text:
    t = Text()
    t.append(f"CCOS v{version} - Welcome\n")
    return t


def _build_welcome_light(version: str, colors: ThemeColors) -> Text:
    """Build the light-theme CCOS welcome banner."""
    return _build_welcome_dark(version, colors)


class Renderer:
    """Render AI responses and tool interactions to the terminal."""

    def __init__(self, console: Console | None = None, theme_name: str | None = None):
        self.console = console or Console(theme=_THEME)
        self._streaming_text = ""
        self._thinking_active = False
        # Tool display verbosity: "full" = show params + result, "header" = name only
        self.tool_display: str = "full"

        # Resolve theme
        resolved = theme_name or detect_theme()
        self.theme = get_theme(resolved)  # type: ignore[arg-type]
        self._theme_name = resolved

    def print_welcome(self, model: str, provider: str, cwd: str) -> None:
        """Print the CCOS welcome banner."""
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
            
        # Add basic indentation for streamed chunks like errors
        formatted = text
        if not self._streaming_text and formatted:
            # If it's the first chunk, start with indent after any leading newlines
            leading_newlines = len(formatted) - len(formatted.lstrip("\n"))
            formatted = "\n" * leading_newlines + "   " + formatted.lstrip("\n")
            
        # Replace remaining newlines with newline + indent (except the very last one if it ends the string)
        if formatted.endswith("\n"):
            formatted = formatted[:-1].replace("\n", "\n   ") + "\n"
        else:
            formatted = formatted.replace("\n", "\n   ")
            
        self.console.print(formatted, end="", highlight=False)
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
        self.flush_streaming()
        if text.strip():
            self.console.print(Padding(Markdown(text), (0, 0, 0, 3)))

    def _tool_summary(self, tc: ToolCallContent) -> str:
        """Build a one-line summary for compact (header-only) tool display."""
        params = tc.input
        if tc.name == "Bash":
            desc = params.get("description", "")
            cmd = params.get("command", "")
            return desc or (cmd[:60] + "..." if len(cmd) > 60 else cmd)
        if tc.name in ("Read", "Write", "Edit", "Glob", "Grep"):
            return params.get("file_path") or params.get("path") or params.get("pattern") or ""
        if tc.name == "Skill":
            return params.get("skill", "")
        if tc.name == "ToolSearch":
            return params.get("query", "")
        # Fallback: first string param value
        for v in params.values():
            if isinstance(v, str) and v:
                return v[:80]
        return ""

    def print_tool_call(self, tc: ToolCallContent) -> None:
        """Show a tool invocation — CC style."""
        icon = _TOOL_ICONS.get(tc.name, "")
        params = tc.input

        self.console.print()

        # ── Header-only mode: single line per tool call ──
        if self.tool_display == "header":
            summary = self._tool_summary(tc)
            line = f"[tool.name]╭─ {icon}{tc.name}[/tool.name]"
            if summary:
                line += f"  [dim]{escape(summary)}[/dim]"
            self.console.print(Padding(line, (0, 0, 0, 3)))
            return

        # ── Full mode ────────────────────────────────────

        # Bash: show command in syntax-highlighted panel
        if tc.name == "Bash" and "command" in params:
            cmd = params["command"]
            desc = params.get("description", "")
            header = f"[tool.name]╭─ {icon}{tc.name}[/tool.name]"
            if desc:
                header += f"  [dim]{desc}[/dim]"

            self.console.print(Padding(header, (0, 0, 0, 3)))
            self.console.print(Padding(Syntax(cmd, "bash", theme="monokai", word_wrap=True), (0, 0, 0, 6)))
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
            header = f"[tool.name]╭─ {icon}{tc.name}[/tool.name] [bold]{path}[/bold]"
            self.console.print(Padding(header, (0, 0, 0, 3)))
            if detail:
                self.console.print(Padding(f"[tool.param]{escape(detail)}[/tool.param]", (0, 0, 0, 6)))
            return

        # Generic tool display
        params_str = ""
        for k, v in params.items():
            if isinstance(v, str) and len(v) > 120:
                v_display = v[:117] + "..."
            else:
                v_display = str(v)
            params_str += f"{k}: {v_display}\n"

        self.console.print(Padding(f"[tool.name]╭─ {icon}{tc.name}[/tool.name]", (0, 0, 0, 3)))
        if params_str:
            self.console.print(Padding(f"[tool.param]{escape(params_str.rstrip())}[/tool.param]", (0, 0, 0, 6)))

    def print_tool_result(self, tool_name: str, content: str, is_error: bool = False) -> None:
        """Show a tool result."""
        # In header mode, show a brief status indicator instead of full output
        if self.tool_display == "header" and not is_error:
            # Show a one-line summary with char count
            n = len(content)
            self.console.print(Padding(f"[dim]ok ({n:,} chars)[/dim]", (0, 0, 0, 6)))
            return

        style = "red" if is_error else "dim"
        icon = _TOOL_ICONS.get(tool_name, "")
        
        # Truncate long results for display
        lines = content.splitlines()
        max_lines = 15 if not is_error else 50
        if len(lines) > max_lines:
            display = "\n".join(lines[:max_lines]) + f"\n\n... ({len(lines) - max_lines} more lines, {len(content):,} total chars)"
        else:
            display = content
            
        if not display.strip():
            display = "(no output)"

        # File content (cat -n format)
        if tool_name == "Read" and not is_error and _looks_like_file_content(display):
            self.console.print(Padding(Syntax(display, "text", theme="monokai", line_numbers=False), (0, 0, 0, 6)))
            return

        # Diff output from Edit
        if tool_name == "Edit" and not is_error:
            self.console.print(Padding(_format_edit_result(display), (0, 0, 0, 6)))
            return

        self.console.print(Padding(f"[{style}]{escape(display)}[/{style}]", (0, 0, 0, 6)))

    def print_thinking(self, text: str) -> None:
        """Show thinking content."""
        self._thinking_active = True
        self.console.print(f"[thinking]{text}[/thinking]", end="")

    def print_error(self, message: str) -> None:
        self.console.print(Padding(f"[bold red]Error:[/bold red] {escape(message)}", (0, 0, 0, 3)))

    def print_status(self, message: str) -> None:
        self.console.print(Padding(f"[status]{message}[/status]", (0, 0, 0, 3)))

    def print_cost(self, summary: str) -> None:
        self.console.print(Padding(f"[cost]{summary}[/cost]", (0, 0, 0, 3)))

    def print_permission_request(self, tool_name: str, params: dict[str, Any]) -> None:
        """Show a permission request panel."""
        icon = _TOOL_ICONS.get(tool_name, "")
        params_str = "\n".join(f"{k}: {v}" for k, v in params.items())

        self.console.print(Padding(f"\n[yellow]⚠️  Permission Required: [bold]{icon}{tool_name}[/bold][/yellow]", (0, 0, 0, 6)))
        if params_str:
            self.console.print(Padding(f"[tool.param]{escape(params_str)}[/tool.param]", (0, 0, 0, 9)))
        self.console.print()

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

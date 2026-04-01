"""Status line / footer bar — matches CC's StatusLine + PromptInputFooter."""

from __future__ import annotations

from rich.console import Console
from rich.text import Text

from ccos.engine.cost_tracker import CostTracker
from ccos.ui.figures import POINTER


class StatusBar:
    """Renders the bottom status bar showing model, cost, token usage, and hints."""

    def __init__(self, console: Console):
        self.console = console

    def render(
        self,
        *,
        model: str,
        provider: str,
        cost_tracker: CostTracker,
        cwd: str = "",
        permission_mode: str = "default",
        context_pct: float | None = None,
    ) -> None:
        """Print a single-line status bar like CC's StatusLine."""
        t = Text()

        # Model + provider
        t.append(f"{model}", style="bold")
        t.append(f" ({provider})", style="dim")
        t.append("  ", style="dim")

        # Cost
        cost = cost_tracker.estimate_cost()
        if cost > 0:
            t.append(f"${cost:.4f}", style="dim green")
            t.append("  ", style="dim")

        # Token counts
        inp = cost_tracker.total_input_tokens
        out = cost_tracker.total_output_tokens
        if inp > 0 or out > 0:
            t.append(f"{_fmt_tokens(inp)} in / {_fmt_tokens(out)} out", style="dim")
            t.append("  ", style="dim")

        # Context window usage
        if context_pct is not None:
            bar = _context_bar(context_pct)
            t.append(bar)
            t.append("  ", style="dim")

        # Turns
        if cost_tracker.turn_count > 0:
            t.append(f"{cost_tracker.turn_count} turns", style="dim")

        self.console.print(t)

    def render_footer_hints(self, permission_mode: str = "default") -> None:
        """Print the footer shortcut hints (below prompt)."""
        t = Text()

        # Mode indicator
        mode_sym = _mode_symbol(permission_mode)
        t.append(mode_sym)
        t.append("  ", style="dim")

        # Shortcuts hint
        t.append("? ", style="dim")
        t.append("for shortcuts", style="dim")

        self.console.print(t)


def _fmt_tokens(n: int) -> str:
    """Format token count compactly: 1.2k, 45.3k, 1.2M."""
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}k"
    return str(n)


def _context_bar(pct: float) -> Text:
    """Render a tiny context-window usage bar [████░░░░] 45%."""
    bar_width = 8
    filled = int(pct * bar_width)
    empty = bar_width - filled

    t = Text()
    t.append("[", style="dim")

    # Color based on usage
    if pct >= 0.9:
        fill_style = "bold red"
    elif pct >= 0.75:
        fill_style = "yellow"
    else:
        fill_style = "green"

    t.append("█" * filled, style=fill_style)
    t.append("░" * empty, style="dim")
    t.append("]", style="dim")
    t.append(f" {pct * 100:.0f}%", style="dim")
    return t


def _mode_symbol(mode: str) -> Text:
    """Return the permission mode symbol + color like CC's PermissionMode."""
    t = Text()
    if mode == "auto" or mode == "trust_all":
        t.append("●", style="bold green")  # auto-accept
    elif mode == "plan":
        t.append("●", style="bold blue")  # plan mode
    else:
        t.append("●", style="rgb(215,119,87)")  # default = Claude orange
    return t

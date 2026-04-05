"""Permission prompt UI — ask user to approve/deny tool execution."""

from __future__ import annotations

from typing import Any

from rich.console import Console
from rich.padding import Padding
from rich.text import Text

from ccos.tools.base import Tool

console = Console()


def ask_permission(tool: Tool, params: dict[str, Any]) -> str:
    """Show a permission dialog and return user choice.

    Returns one of: 'yes', 'no', 'always', 'deny_always'
    """
    # Build description of what the tool wants to do
    desc_parts: list[str] = [f"[bold]{tool.name}[/bold]"]

    if tool.name == "Bash":
        cmd = params.get("command", "")
        desc_parts.append(f"Command: [cyan]{cmd}[/cyan]")
        if params.get("description"):
            desc_parts.append(f"Description: {params['description']}")
    elif tool.name in ("Write", "Edit"):
        path = params.get("file_path", "")
        desc_parts.append(f"File: [cyan]{path}[/cyan]")
    elif tool.name == "Read":
        path = params.get("file_path", "")
        desc_parts.append(f"File: [cyan]{path}[/cyan]")
    else:
        # Generic display of params
        for k, v in params.items():
            if isinstance(v, str) and len(v) < 200:
                desc_parts.append(f"{k}: [cyan]{v}[/cyan]")

    console.print(Padding(f"\n[yellow]⚠️  Permission Required: [bold]{tool.name}[/bold][/yellow]", (0, 0, 0, 3)))
    if len(desc_parts) > 1:
        console.print(Padding("\n".join(desc_parts[1:]), (0, 0, 0, 6)))
    console.print()

    console.print(
        "   [green]Y[/green]es  |  "
        "[red]N[/red]o  |  "
        "[blue]A[/blue]lways allow  |  "
        "[magenta]D[/magenta]eny always"
    )

    while True:
        try:
            choice = console.input("   [yellow]> [/yellow]").strip().lower()
        except (EOFError, KeyboardInterrupt):
            return "no"

        if choice in ("y", "yes", ""):
            return "yes"
        if choice in ("n", "no"):
            return "no"
        if choice in ("a", "always"):
            return "always"
        if choice in ("d", "deny"):
            return "deny_always"

        console.print("   [dim]Please enter Y, N, A, or D[/dim]")

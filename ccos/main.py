"""CLI entry point using Click."""

from __future__ import annotations

import os
import sys

# Fix Windows asyncio + httpx "Event loop is closed" error.
# The ProactorEventLoop (default on Windows) raises RuntimeError when
# httpx's async connections try to close after asyncio.run() finishes.
if sys.platform == "win32":
    import asyncio
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

import click

from ccos import __version__


@click.command(context_settings={"help_option_names": ["-h", "--help"]})
@click.version_option(__version__, "-v", "--version", prog_name="ccos")
@click.argument("prompt", nargs=-1, required=False)
@click.option(
    "-m", "--model",
    default=None,
    help="Model to use (e.g. claude-sonnet-4-6, gpt-4o, llama3.1)",
)
@click.option(
    "-p", "--provider",
    default=None,
    help="LLM provider (anthropic, openai, ollama, grok, or any configured name)",
)
@click.option(
    "--cwd",
    default=None,
    help="Working directory (default: current directory)",
)
@click.option(
    "--dangerously-skip-permissions",
    is_flag=True,
    default=False,
    help="Skip all permission checks (use with caution)",
)
@click.option(
    "--resume", "-r",
    default=None,
    help="Resume a previous session by ID",
)
def cli(
    prompt: tuple[str, ...],
    model: str | None,
    provider: str | None,
    cwd: str | None,
    dangerously_skip_permissions: bool,
    resume: str | None,
) -> None:
    """CCOS - Production-grade agentic coding CLI.

    Run without arguments for interactive mode, or pass a prompt for single-shot mode.

    Examples:

        ccos                                  # Interactive REPL
        ccos "explain this codebase"          # Single query
        ccos -p openai -m gpt-4o "hello"      # Use OpenAI
        ccos -p ollama -m llama3.1 "hello"    # Use Ollama
    """
    from ccos.app import App

    try:
        app = App(
            provider_name=provider,
            model=model,
            cwd=cwd,
            trust_all=dangerously_skip_permissions,
            resume_session_id=resume,
        )
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    if prompt:
        # Single-shot mode
        app.run_single(" ".join(prompt))
    else:
        # Interactive mode
        try:
            app.run_interactive()
        except (SystemExit, KeyboardInterrupt):
            pass


if __name__ == "__main__":
    cli()

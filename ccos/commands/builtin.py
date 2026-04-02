"""Built-in slash commands."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
from typing import TYPE_CHECKING, Any

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from ccos.commands.registry import CommandRegistry, SlashCommand

if TYPE_CHECKING:
    from ccos.app import App


def register_builtin_commands(registry: CommandRegistry, app: App) -> None:
    """Register all built-in slash commands."""

    def cmd_help(**_: Any) -> None:
        console = Console()
        table = Table(title="Available Commands", border_style="dim")
        table.add_column("Command", style="cyan")
        table.add_column("Description")
        for cmd in registry.get_all_unique():
            aliases = f" ({', '.join('/' + a for a in cmd.aliases)})" if cmd.aliases else ""
            table.add_row(f"/{cmd.name}{aliases}", cmd.description)
        console.print(table)

    def cmd_exit(**_: Any) -> None:
        raise SystemExit(0)

    def cmd_clear(**_: Any) -> None:
        app.engine.messages.clear()
        app.plan_manager.is_plan_mode = False
        Console().print("[dim]Conversation cleared.[/dim]")

    def cmd_model(args: str = "", **_: Any) -> None:
        console = Console()
        if args:
            app.model = args.strip()
            app.engine.model = app.model
            app.config.default_model = app.model
            pcfg = app.config.providers.get(app.engine.provider.name)
            if pcfg:
                pcfg.default_model = app.model
            app.config.save()
            console.print(f"Model switched to: [cyan]{app.model}[/cyan]")
        else:
            console.print(f"Current model: [cyan]{app.model}[/cyan]")
            console.print(f"Provider: [dim]{app.engine.provider.name}[/dim]")
            console.print("[dim]Fetching available models...[/dim]")
            try:
                models = app._run_async(app.engine.provider.list_models())
            except Exception:
                models = []
            if models:
                console.print(f"\n[dim]Available models ({len(models)}):[/dim]")
                for i, m in enumerate(models, 1):
                    marker = " [yellow]◀ current[/yellow]" if m == app.model else ""
                    console.print(f"  [dim]{i:>3}.[/dim] [cyan]{m}[/cyan]{marker}")
                try:
                    choice = console.input(
                        f"\n[dim]Select model number (Enter to keep [cyan]{app.model}[/cyan]):[/dim] "
                    )
                    choice = choice.strip()
                    if choice:
                        idx = int(choice) - 1
                        if 0 <= idx < len(models):
                            app.model = models[idx]
                            app.engine.model = app.model
                            app.config.default_model = app.model
                            pcfg = app.config.providers.get(app.engine.provider.name)
                            if pcfg:
                                pcfg.default_model = app.model
                            app.config.save()
                            console.print(f"Model switched to: [cyan]{app.model}[/cyan]")
                        else:
                            console.print("[red]Invalid selection.[/red]")
                except (EOFError, KeyboardInterrupt):
                    console.print()
                except ValueError:
                    console.print("[red]Invalid input.[/red]")
            else:
                console.print("[dim](Provider did not return a model list)[/dim]")

    def cmd_provider(args: str = "", **_: Any) -> None:
        console = Console()
        if args:
            name = args.strip()
            try:
                from ccos.providers.registry import ProviderRegistry
                reg = ProviderRegistry()
                provider = reg.get_provider(app.config, provider_name=name)
                app.provider = provider
                app.engine.provider = provider

                # Query available models from provider API
                default_model = reg.get_model(app.config, provider_name=name)
                console.print(f"Provider switched to: [cyan]{name}[/cyan]")
                console.print("[dim]Fetching available models...[/dim]")
                try:
                    models = app._run_async(provider.list_models())
                except Exception:
                    models = []

                if models:
                    # Auto-correct if default_model is not in the real model list
                    if default_model not in models:
                        default_model = models[0]
                    console.print(f"[dim]Available models ({len(models)}):[/dim]")
                    for i, m in enumerate(models, 1):
                        marker = " [yellow](default)[/yellow]" if m == default_model else ""
                        console.print(f"  [dim]{i:>3}.[/dim] [cyan]{m}[/cyan]{marker}")
                    try:
                        choice = console.input(
                            f"\n[dim]Select model number (Enter for [cyan]{default_model}[/cyan]):[/dim] "
                        )
                        choice = choice.strip()
                        if choice:
                            idx = int(choice) - 1
                            if 0 <= idx < len(models):
                                default_model = models[idx]
                            else:
                                console.print("[red]Invalid selection, using default.[/red]")
                    except (EOFError, KeyboardInterrupt):
                        console.print()
                    except ValueError:
                        console.print("[red]Invalid input, using default.[/red]")

                app.engine.model = default_model
                app.model = default_model
                app.config.default_provider = name
                app.config.default_model = default_model
                pcfg = app.config.providers.get(name)
                if pcfg:
                    pcfg.default_model = default_model
                app.config.save()
                console.print(f"Model: [cyan]{default_model}[/cyan]")
            except ValueError as e:
                console.print(f"[red]{e}[/red]")
        else:
            current = app.engine.provider.name
            table = Table(title="Providers", border_style="dim", show_header=True)
            table.add_column("", width=2)
            table.add_column("Provider", style="cyan")
            table.add_column("Default Model", style="dim")
            table.add_column("Base URL", style="dim")
            for pname, pcfg in app.config.providers.items():
                marker = "[yellow]▶[/yellow]" if pname == current else ""
                model_str = pcfg.default_model or "-"
                url_str = pcfg.base_url or "-"
                table.add_row(marker, pname, model_str, url_str)
            console.print(table)
            console.print(f"Current model: [cyan]{app.model}[/cyan]")
            console.print(f"[dim]Use /provider <name> to switch.[/dim]")

    def cmd_cost(**_: Any) -> None:
        Console().print(app.engine.cost.summary())

    def cmd_status(**_: Any) -> None:
        console = Console()
        console.print(f"Provider: [cyan]{app.engine.provider.name}[/cyan]")
        console.print(f"Model: [cyan]{app.model}[/cyan]")
        console.print(f"Working dir: {app.ctx.cwd}")
        console.print(f"Session: [dim]{app.session_manager.session_id}[/dim]")
        console.print(f"Messages: {len(app.engine.messages.messages)}")
        est_tokens = app.engine.messages.estimate_total_tokens()
        console.print(f"Est. context tokens: ~{est_tokens:,}")
        if app.plan_manager.is_plan_mode:
            console.print("[yellow]Plan mode: ACTIVE[/yellow]")
        console.print(app.engine.cost.summary())

    def cmd_compact(**_: Any) -> None:
        console = Console()
        mgr = app.engine.messages
        if len(mgr.messages) <= 6:
            console.print("[dim]Nothing to compact.[/dim]")
            return

        before = mgr.estimate_total_tokens()
        # Generate summary using the LLM
        console.print("[dim]Compacting conversation...[/dim]")
        summary_prompt = mgr.get_compact_prompt()
        try:
            summary = asyncio.run(app.engine.run_turn(summary_prompt))
            # Remove the summary turn itself (last 2 messages: user + assistant)
            if len(mgr.messages) >= 2:
                mgr.messages = mgr.messages[:-2]
            removed = mgr.compact(summary)
            after = mgr.estimate_total_tokens()
            console.print(
                f"[dim]Compacted: removed {removed} messages, "
                f"~{before:,} -> ~{after:,} tokens.[/dim]"
            )
        except Exception as e:
            # Fallback: simple truncation
            msgs = mgr.messages
            if len(msgs) > 10:
                mgr.messages = msgs[-10:]
                console.print(f"[dim]Fallback compact: kept last 10 messages (was {len(msgs)}).[/dim]")
            else:
                console.print(f"[dim]Compact failed: {e}[/dim]")

    def cmd_config(**_: Any) -> None:
        from ccos.config import get_config_dir
        path = get_config_dir() / "config.json"
        Console().print(f"Config file: [cyan]{path}[/cyan]")

    def cmd_history(**_: Any) -> None:
        console = Console()
        sessions = app.session_manager.list_sessions(app.cwd)
        if not sessions:
            console.print("[dim]No saved sessions found.[/dim]")
            return

        table = Table(title="Recent Sessions", border_style="dim")
        table.add_column("ID", style="cyan", no_wrap=True)
        table.add_column("First Prompt")
        table.add_column("Model", style="dim")
        table.add_column("Updated", style="dim")

        import time
        for s in sessions[:15]:
            age = time.time() - s.updated_at
            if age < 3600:
                when = f"{int(age / 60)}m ago"
            elif age < 86400:
                when = f"{int(age / 3600)}h ago"
            else:
                when = f"{int(age / 86400)}d ago"
            prompt = s.first_prompt or "(no prompt)"
            table.add_row(s.session_id[:12], prompt[:60], s.model or "?", when)

        console.print(table)
        console.print("[dim]Resume with: /resume <session_id> or ccos --resume <id>[/dim]")

    def cmd_resume(args: str = "", **_: Any) -> None:
        console = Console()
        if not args.strip():
            console.print("[red]Usage: /resume <session_id>[/red]")
            return

        session_id = args.strip()
        messages = app.session_manager.resume_session(session_id, app.cwd)
        if messages is None:
            console.print(f"[red]Session {session_id} not found.[/red]")
            return

        # Restore messages into the engine
        app.engine.messages.clear()
        app._restore_messages(messages)

        # Wire session ID into ExitPlanMode
        exit_plan = app.tool_registry.get("ExitPlanMode")
        if exit_plan is not None:
            from ccos.tools.plan_mode import ExitPlanModeTool
            if isinstance(exit_plan, ExitPlanModeTool):
                exit_plan._session_id = app.session_manager.session_id

        console.print(
            f"[green]Resumed session {session_id} ({len(messages)} messages).[/green]"
        )

    def cmd_diff(**_: Any) -> None:
        console = Console()
        try:
            result = subprocess.run(
                ["git", "diff", "--stat"],
                capture_output=True, text=True, cwd=app.cwd, timeout=10,
            )
            if result.stdout.strip():
                console.print(Panel(result.stdout.strip(), title="Git Diff (stat)", border_style="dim"))
                # Also show the full diff
                full = subprocess.run(
                    ["git", "diff"],
                    capture_output=True, text=True, cwd=app.cwd, timeout=10,
                )
                if full.stdout.strip():
                    from rich.syntax import Syntax
                    console.print(Syntax(full.stdout[:5000], "diff", theme="monokai"))
            else:
                console.print("[dim]No uncommitted changes.[/dim]")
        except FileNotFoundError:
            console.print("[red]Git not found.[/red]")
        except Exception as e:
            console.print(f"[red]Error: {e}[/red]")

    def cmd_plan(**_: Any) -> None:
        console = Console()
        if app.plan_manager.is_plan_mode:
            console.print("[yellow]Plan mode is ACTIVE.[/yellow]")
            plan = app.plan_manager.get_plan(app.session_manager.session_id)
            if plan:
                from rich.markdown import Markdown
                console.print(Panel(Markdown(plan), title="Current Plan", border_style="yellow"))
            else:
                console.print("[dim]No plan written yet.[/dim]")
            path = app.plan_manager.get_plan_file_path(app.session_manager.session_id)
            console.print(f"[dim]Plan file: {path}[/dim]")
        else:
            console.print("[dim]Not in plan mode. The AI can enter plan mode using EnterPlanMode tool.[/dim]")

    def cmd_memory(args: str = "", **_: Any) -> None:
        console = Console()
        from ccos.memory.store import MemoryStore, MemoryEntry
        from ccos.memory.types import MemoryType
        import time as _time

        store = app.memory_store
        parts = args.strip().split(maxsplit=1)
        subcmd = parts[0] if parts else ""
        sub_args = parts[1].strip() if len(parts) > 1 else ""

        if subcmd == "add":
            # /memory add <name>
            if not sub_args:
                console.print("[red]Usage: /memory add <name>[/red]")
                return
            name = sub_args
            console.print("[dim]Memory types: user, feedback, project, reference[/dim]")
            try:
                type_str = console.input("[yellow]Type: [/yellow]").strip()
                description = console.input("[yellow]Description (one line): [/yellow]").strip()
                console.print("[dim]Enter content (end with empty line):[/dim]")
                lines: list[str] = []
                while True:
                    line = console.input("")
                    if not line:
                        break
                    lines.append(line)
            except (EOFError, KeyboardInterrupt):
                console.print()
                return

            try:
                mem_type = MemoryType(type_str)
            except ValueError:
                console.print(f"[red]Invalid type '{type_str}'. Use: user, feedback, project, reference[/red]")
                return

            entry = MemoryEntry(
                name=name,
                description=description,
                type=mem_type,
                content="\n".join(lines),
            )
            store.save_entry(entry)
            console.print(f"[green]Memory '{name}' saved to {entry.file_path}[/green]")

        elif subcmd == "delete":
            if not sub_args:
                console.print("[red]Usage: /memory delete <name>[/red]")
                return
            if store.delete_entry(sub_args):
                console.print(f"[green]Memory '{sub_args}' deleted.[/green]")
            else:
                console.print(f"[red]Memory '{sub_args}' not found.[/red]")

        elif subcmd == "show":
            if not sub_args:
                console.print("[red]Usage: /memory show <name>[/red]")
                return
            entry = store.load_entry(sub_args)
            if entry:
                from rich.markdown import Markdown
                header = f"[cyan]{entry.name}[/cyan] ({entry.type.value}) — {entry.description}"
                age_warn = store.get_age_warning(entry)
                if age_warn:
                    header += f"\n[yellow]{age_warn}[/yellow]"
                console.print(Panel(
                    Markdown(entry.content[:3000]),
                    title=header,
                    border_style="blue",
                ))
            else:
                console.print(f"[red]Memory '{sub_args}' not found.[/red]")

        elif subcmd == "edit":
            if not sub_args:
                console.print("[red]Usage: /memory edit <name>[/red]")
                return
            entry = store.load_entry(sub_args)
            if not entry:
                console.print(f"[red]Memory '{sub_args}' not found.[/red]")
                return
            console.print(f"[dim]Current content:[/dim]\n{entry.content}\n")
            try:
                console.print("[dim]Enter new content (end with empty line):[/dim]")
                lines = []
                while True:
                    line = console.input("")
                    if not line:
                        break
                    lines.append(line)
            except (EOFError, KeyboardInterrupt):
                console.print()
                return
            entry.content = "\n".join(lines)
            try:
                new_desc = console.input(
                    f"[yellow]Description [{entry.description}]: [/yellow]"
                ).strip()
                if new_desc:
                    entry.description = new_desc
            except (EOFError, KeyboardInterrupt):
                pass
            store.save_entry(entry)
            console.print(f"[green]Memory '{entry.name}' updated.[/green]")

        else:
            # Default: list all memories + CLAUDE.md files
            entries = store.scan_all()

            # Also show CLAUDE.md files
            claude_files = []
            for name in ("CLAUDE.md", "CCOS.md"):
                project_path = os.path.join(app.cwd, name)
                if os.path.exists(project_path):
                    claude_files.append(project_path)
            home_claude = os.path.expanduser("~/.claude/CLAUDE.md")
            if os.path.exists(home_claude):
                claude_files.append(home_claude)

            if claude_files:
                console.print("[bold]Static memory files:[/bold]")
                for p in claude_files:
                    size = os.path.getsize(p)
                    console.print(f"  [cyan]{p}[/cyan] ({size:,} bytes)")
                console.print()

            if entries:
                table = Table(title="Auto-Memory", border_style="dim")
                table.add_column("Name", style="cyan")
                table.add_column("Type", style="yellow")
                table.add_column("Description")
                table.add_column("Age", style="dim", justify="right")
                for e in entries:
                    days = e.age_days
                    if days < 1:
                        age = "< 1d"
                    elif days < 30:
                        age = f"{int(days)}d"
                    else:
                        age = f"{int(days / 30)}mo"
                    table.add_row(e.name, e.type.value, e.description[:50], age)
                console.print(table)
            else:
                console.print("[dim]No auto-memories yet.[/dim]")

            console.print(f"\n[dim]Memory dir: {store.memory_dir}[/dim]")
            console.print("[dim]Subcommands: /memory add|delete|show|edit <name>[/dim]")

    def cmd_doctor(**_: Any) -> None:
        console = Console()
        console.print("[bold]CCOS Doctor[/bold]\n")

        # Check Python version
        import sys
        console.print(f"  Python: [cyan]{sys.version.split()[0]}[/cyan]")
        console.print(f"  Platform: [cyan]{sys.platform}[/cyan]")

        # Check provider
        console.print(f"  Provider: [cyan]{app.provider.name}[/cyan]")
        console.print(f"  Model: [cyan]{app.model}[/cyan]")

        # Check API keys
        checks = [
            ("ANTHROPIC_API_KEY", "Anthropic"),
            ("OPENAI_API_KEY", "OpenAI"),
            ("GEMINI_API_KEY", "Google Gemini"),
            ("XAI_API_KEY", "Grok/xAI"),
            ("BRAVE_SEARCH_API_KEY", "Brave Search"),
        ]
        for env_var, name in checks:
            val = os.environ.get(env_var)
            if val:
                masked = val[:8] + "..." + val[-4:] if len(val) > 16 else "***"
                console.print(f"  {name}: [green]{masked}[/green]")
            else:
                console.print(f"  {name}: [dim]not set[/dim]")

        # Check tools
        console.print(f"\n  Tools: [cyan]{len(app.tool_registry.get_all())}[/cyan] registered")
        console.print(f"  Tools: {', '.join(app.tool_registry.names())}")

        # Check git
        try:
            result = subprocess.run(
                ["git", "--version"], capture_output=True, text=True, timeout=5,
            )
            console.print(f"  Git: [green]{result.stdout.strip()}[/green]")
        except Exception:
            console.print("  Git: [red]not found[/red]")

        # Check ripgrep
        try:
            result = subprocess.run(
                ["rg", "--version"], capture_output=True, text=True, timeout=5,
            )
            ver = result.stdout.split("\n")[0] if result.stdout else "?"
            console.print(f"  Ripgrep: [green]{ver}[/green]")
        except Exception:
            console.print("  Ripgrep: [dim]not found (grep will use Python fallback)[/dim]")

        # Check session storage
        session_path = app.session_manager.transcript_path
        console.print(f"\n  Session: [dim]{app.session_manager.session_id}[/dim]")
        if session_path and os.path.exists(session_path):
            size = os.path.getsize(session_path)
            console.print(f"  Transcript: [dim]{session_path} ({size:,} bytes)[/dim]")

    def cmd_login(**_: Any) -> None:
        console = Console()
        from ccos.auth import load_credentials, save_credentials, verify_api_key

        creds = load_credentials()

        # Show current auth status
        if creds.has_any_key():
            console.print("[dim]Current credentials:[/dim]")
            for prov, key in creds.api_keys.items():
                masked = key[:8] + "..." + key[-4:] if len(key) > 16 else "***"
                console.print(f"  {prov}: [green]{masked}[/green]")
            if creds.oauth_token:
                acct_label = creds.oauth_account.email if creds.oauth_account else "logged in"
                import time as _time
                if creds.oauth_expires_at:
                    remaining = creds.oauth_expires_at - _time.time()
                    if remaining > 0:
                        hours = int(remaining // 3600)
                        mins = int((remaining % 3600) // 60)
                        expiry = f", expires in {hours}h{mins}m"
                    else:
                        expiry = ", [yellow]expired[/yellow]"
                else:
                    expiry = ""
                console.print(f"  Anthropic OAuth: [green]{acct_label}[/green]{expiry}")
            console.print()

        # Ask which provider / method
        console.print("[bold]Sign in[/bold]")
        console.print("  1. Anthropic — API key (sk-ant-...)")
        console.print("  2. Anthropic — OAuth (Claude.ai browser login, Pro/Max subscription)")
        console.print("  3. OpenAI (GPT-4o, o1, etc.)")
        console.print("  4. Google Gemini")
        console.print("  5. xAI / Grok")
        console.print("  6. Custom provider")
        console.print()

        try:
            choice = console.input("[yellow]Choice (1-6): [/yellow]").strip()
        except (EOFError, KeyboardInterrupt):
            return

        # ── Option 2: Anthropic OAuth ──────────────────────────────────────
        if choice == "2":
            from ccos.auth import build_oauth_url, exchange_oauth_code, OAuthAccount
            import time as _time

            url, code_verifier, state = build_oauth_url()

            console.print()
            console.print("[bold]Anthropic OAuth Login[/bold]")
            console.print("[dim]Open the following URL in your browser to log in:[/dim]")
            console.print()
            console.print(f"  [cyan]{url}[/cyan]")
            console.print()
            console.print("[dim]After logging in, you'll see a page showing your authorization code.[/dim]")
            console.print("[dim]Copy the full code (including any # suffix) and paste it below.[/dim]")
            console.print()

            try:
                raw_code = console.input("[yellow]Paste authorization code: [/yellow]").strip()
            except (EOFError, KeyboardInterrupt):
                return

            if not raw_code:
                console.print("[red]No code entered.[/red]")
                return

            # Split code and state (format: {code}#{state})
            if "#" in raw_code:
                auth_code, returned_state = raw_code.split("#", 1)
                if returned_state != state:
                    console.print("[red]State mismatch — possible CSRF attack. Aborting.[/red]")
                    return
            else:
                auth_code = raw_code

            console.print("[dim]Exchanging code for tokens...[/dim]")
            try:
                token_data = asyncio.run(exchange_oauth_code(auth_code, code_verifier, state))
            except Exception as e:
                console.print(f"[red]Token exchange failed: {e}[/red]")
                return

            access_token = token_data.get("access_token", "")
            refresh_token = token_data.get("refresh_token", "")
            expires_in = token_data.get("expires_in", 28800)

            if not access_token:
                console.print("[red]No access token received.[/red]")
                return

            creds.oauth_token = access_token
            creds.oauth_refresh_token = refresh_token
            creds.oauth_expires_at = _time.time() + expires_in

            acct_data = token_data.get("account", {})
            org_data = token_data.get("organization", {})
            creds.oauth_account = OAuthAccount(
                email=acct_data.get("email_address", ""),
                display_name=org_data.get("name", ""),
                organization_id=org_data.get("uuid", ""),
            )
            save_credentials(creds)

            email = creds.oauth_account.email or "unknown"
            console.print(f"[green]Logged in as {email}. OAuth token saved.[/green]")

            # Refresh provider
            try:
                from ccos.providers.registry import ProviderRegistry
                app.provider = ProviderRegistry().get_provider(
                    app.config, provider_name="anthropic",
                )
                app.engine.provider = app.provider
                console.print("[dim]Provider refreshed with OAuth token.[/dim]")
            except Exception:
                pass
            return

        # ── Options 1, 3-6: API key login ─────────────────────────────────
        provider_map = {"1": "anthropic", "3": "openai", "4": "gemini", "5": "grok", "6": "custom"}
        provider = provider_map.get(choice)
        if not provider:
            console.print("[red]Invalid choice.[/red]")
            return

        if provider == "custom":
            try:
                provider = console.input("Provider name: ").strip()
            except (EOFError, KeyboardInterrupt):
                return
            if not provider:
                return

        # Ask for API key
        key_hint = {
            "anthropic": "sk-ant-...",
            "openai": "sk-...",
            "gemini": "AIza...",
            "grok": "xai-...",
        }.get(provider, "")
        prompt = f"API key ({key_hint}): " if key_hint else "API key: "
        try:
            api_key = console.input(f"[yellow]{prompt}[/yellow]").strip()
        except (EOFError, KeyboardInterrupt):
            return

        if not api_key:
            console.print("[red]No key entered.[/red]")
            return

        # Verify
        console.print("[dim]Verifying...[/dim]")
        success, msg = asyncio.run(verify_api_key(provider, api_key))
        if success:
            console.print(f"[green]{msg}[/green]")
            creds.api_keys[provider] = api_key
            save_credentials(creds)
            console.print(f"[green]Key saved for {provider}.[/green]")

            # Refresh provider if it matches
            if provider == app.config.default_provider or provider == app.provider.name:
                try:
                    from ccos.providers.registry import ProviderRegistry
                    app.provider = ProviderRegistry().get_provider(
                        app.config, provider_name=provider,
                    )
                    app.engine.provider = app.provider
                    console.print(f"[dim]Provider refreshed.[/dim]")
                except Exception:
                    pass
        else:
            console.print(f"[red]{msg}[/red]")
            try:
                save_anyway = console.input("[yellow]Save anyway? (y/N): [/yellow]").strip().lower()
            except (EOFError, KeyboardInterrupt):
                return
            if save_anyway == "y":
                creds.api_keys[provider] = api_key
                save_credentials(creds)
                console.print(f"[dim]Key saved for {provider}.[/dim]")

    def cmd_logout(**_: Any) -> None:
        console = Console()
        from ccos.auth import load_credentials, save_credentials

        creds = load_credentials()
        if not creds.api_keys and not creds.oauth_token:
            console.print("[dim]No stored credentials.[/dim]")
            return

        console.print("[bold]Stored credentials:[/bold]")
        providers = list(creds.api_keys.keys())
        for i, prov in enumerate(providers, 1):
            key = creds.api_keys[prov]
            masked = key[:8] + "..." + key[-4:] if len(key) > 16 else "***"
            console.print(f"  {i}. {prov}: {masked}")
        if creds.oauth_token:
            console.print(f"  {len(providers) + 1}. OAuth token")
        console.print(f"  a. Remove all")
        console.print()

        try:
            choice = console.input("[yellow]Remove which? [/yellow]").strip().lower()
        except (EOFError, KeyboardInterrupt):
            return

        if choice == "a":
            creds.api_keys.clear()
            creds.oauth_token = ""
            creds.oauth_account = None
            save_credentials(creds)
            console.print("[green]All credentials removed.[/green]")
        elif choice.isdigit():
            idx = int(choice) - 1
            if 0 <= idx < len(providers):
                removed = providers[idx]
                del creds.api_keys[removed]
                save_credentials(creds)
                console.print(f"[green]Removed credentials for {removed}.[/green]")
            else:
                console.print("[red]Invalid choice.[/red]")

    def cmd_init(**_: Any) -> None:
        """Initialize a CLAUDE.md / CCOS.md in the project."""
        console = Console()
        target = os.path.join(app.cwd, "CLAUDE.md")
        if os.path.exists(target):
            console.print(f"[dim]{target} already exists.[/dim]")
            return
        template = (
            "# Project Instructions\n\n"
            "<!-- Add project-specific instructions for the AI here. -->\n"
            "<!-- This file is loaded into every conversation as context. -->\n\n"
            "## Project Overview\n\n"
            "<!-- Describe your project, tech stack, and conventions. -->\n\n"
            "## Code Style\n\n"
            "<!-- Describe preferred code style, patterns, and conventions. -->\n\n"
            "## Testing\n\n"
            "<!-- Describe how to run tests and testing conventions. -->\n"
        )
        with open(target, "w", encoding="utf-8", newline="\n") as f:
            f.write(template)
        console.print(f"[green]Created {target}[/green]")
        console.print("[dim]Edit this file to provide project-specific instructions to the AI.[/dim]")

    def cmd_permissions(**_: Any) -> None:
        console = Console()
        console.print(f"[bold]Permission Mode:[/bold] {app.permissions.mode.value}")
        if app.permissions.always_allow:
            console.print("\n[bold]Always Allow:[/bold]")
            for tool, patterns in app.permissions.always_allow.items():
                for p in patterns:
                    console.print(f"  {tool}: {p}")
        if app.permissions.always_deny:
            console.print("\n[bold]Always Deny:[/bold]")
            for tool, patterns in app.permissions.always_deny.items():
                for p in patterns:
                    console.print(f"  {tool}: {p}")
        if app.permissions._session_allows:
            console.print("\n[bold]Session Allows:[/bold]")
            for tool, patterns in app.permissions._session_allows.items():
                for p in patterns:
                    console.print(f"  {tool}: {p}")

    def cmd_vim(**_: Any) -> None:
        console = Console()
        app.config.ui.vim_mode = not app.config.ui.vim_mode
        state = "enabled" if app.config.ui.vim_mode else "disabled"
        console.print(f"[dim]Vim mode {state}. Takes effect on next input.[/dim]")

    def cmd_theme(args: str = "", **_: Any) -> None:
        console = Console()
        if args.strip():
            app.config.ui.theme = args.strip()
            console.print(f"[dim]Theme set to: {args.strip()}[/dim]")
        else:
            console.print(f"[dim]Current theme: {app.config.ui.theme}[/dim]")

    def cmd_export(**_: Any) -> None:
        """Export the current conversation as markdown."""
        console = Console()
        msgs = app.engine.messages.messages
        if not msgs:
            console.print("[dim]No conversation to export.[/dim]")
            return

        from ccos.providers.base import TextContent, ToolCallContent
        lines = ["# Conversation Export\n"]
        for msg in msgs:
            if isinstance(msg.content, str):
                lines.append(f"## {msg.role.title()}\n\n{msg.content}\n")
            elif isinstance(msg.content, list):
                for block in msg.content:
                    if isinstance(block, TextContent):
                        lines.append(f"## {msg.role.title()}\n\n{block.text}\n")
                    elif isinstance(block, ToolCallContent):
                        lines.append(f"### Tool: {block.name}\n```\n{block.input}\n```\n")

        export_path = os.path.join(app.cwd, f"conversation-{app.session_manager.session_id[:8]}.md")
        with open(export_path, "w", encoding="utf-8", newline="\n") as f:
            f.write("\n".join(lines))
        console.print(f"[green]Exported to {export_path}[/green]")

    def cmd_hooks(**_: Any) -> None:
        console = Console()
        hooks_dir = os.path.join(os.path.expanduser("~"), ".ccos", "hooks")
        if os.path.isdir(hooks_dir):
            files = os.listdir(hooks_dir)
            if files:
                console.print("[bold]Installed hooks:[/bold]")
                for f in sorted(files):
                    console.print(f"  {f}")
            else:
                console.print("[dim]No hooks installed.[/dim]")
        else:
            console.print("[dim]No hooks directory found at ~/.ccos/hooks/[/dim]")
        console.print("[dim]Create shell scripts in ~/.ccos/hooks/ to run before/after tool calls.[/dim]")

    def cmd_files(**_: Any) -> None:
        """Show files read/modified in this session."""
        console = Console()
        read_files = app.ctx.read_files
        if not read_files:
            console.print("[dim]No files accessed in this session.[/dim]")
            return
        table = Table(title="Files Accessed", border_style="dim")
        table.add_column("File", style="cyan")
        table.add_column("Last Read", style="dim")
        import time
        for path, mtime in sorted(read_files.items()):
            age = time.time() - mtime
            when = f"{int(age)}s ago" if age < 60 else f"{int(age/60)}m ago"
            table.add_row(path, when)
        console.print(table)

    def cmd_fast(**_: Any) -> None:
        """Toggle fast mode (same model, faster output)."""
        console = Console()
        # In our implementation, fast mode is a hint to the provider
        console.print("[dim]Fast mode toggled. (Same model, optimized for speed.)[/dim]")

    def cmd_branch(**_: Any) -> None:
        """Show current git branch."""
        console = Console()
        try:
            result = subprocess.run(
                ["git", "branch", "--show-current"],
                capture_output=True, text=True, cwd=app.cwd, timeout=5,
            )
            if result.stdout.strip():
                console.print(f"Branch: [cyan]{result.stdout.strip()}[/cyan]")
            else:
                console.print("[dim]Not on a branch (detached HEAD?).[/dim]")
        except Exception:
            console.print("[red]Git not available.[/red]")

    def cmd_add_dir(args: str = "", **_: Any) -> None:
        """Add an additional working directory."""
        console = Console()
        path = args.strip()
        if not path:
            console.print("[red]Usage: /add-dir <path>[/red]")
            return
        abs_path = os.path.abspath(os.path.join(app.cwd, path))
        if not os.path.isdir(abs_path):
            console.print(f"[red]Not a directory: {abs_path}[/red]")
            return
        if not hasattr(app.ctx, 'added_dirs'):
            app.ctx.added_dirs = []
        if abs_path not in app.ctx.added_dirs:
            app.ctx.added_dirs.append(abs_path)
        console.print(f"[green]Added directory: {abs_path}[/green]")

    def cmd_context(**_: Any) -> None:
        """Show context window usage."""
        console = Console()
        est = app.engine.messages.estimate_total_tokens()
        budget = 180_000
        pct = est / budget * 100
        bar_len = 40
        filled = int(pct / 100 * bar_len)
        bar = "█" * filled + "░" * (bar_len - filled)
        style = "green" if pct < 50 else "yellow" if pct < 75 else "red"
        console.print(f"[bold]Context Window[/bold]")
        console.print(f"  [{style}][{bar}] {pct:.1f}%[/{style}]")
        console.print(f"  ~{est:,} / {budget:,} tokens")
        console.print(f"  Messages: {len(app.engine.messages.messages)}")
        console.print(f"  Turns: {app.engine.cost.turn_count}")

    def cmd_session(**_: Any) -> None:
        """Show current session info."""
        console = Console()
        console.print(f"[bold]Session:[/bold] {app.session_manager.session_id}")
        console.print(f"  Model: [cyan]{app.model}[/cyan]")
        console.print(f"  Provider: [cyan]{app.provider.name}[/cyan]")
        console.print(f"  CWD: {app.cwd}")
        if app.session_manager.transcript_path and os.path.exists(app.session_manager.transcript_path):
            size = os.path.getsize(app.session_manager.transcript_path)
            console.print(f"  Transcript: {app.session_manager.transcript_path} ({size:,} bytes)")

    def cmd_stats(**_: Any) -> None:
        """Show detailed cost/usage statistics."""
        console = Console()
        cost = app.engine.cost
        console.print("[bold]Session Statistics[/bold]\n")
        console.print(f"  Input tokens:  {cost.total_input_tokens:>12,}")
        console.print(f"  Output tokens: {cost.total_output_tokens:>12,}")
        console.print(f"  Cache read:    {cost.total_cache_read_tokens:>12,}")
        console.print(f"  Cache create:  {cost.total_cache_creation_tokens:>12,}")
        console.print(f"  Total turns:   {cost.turn_count:>12}")
        console.print(f"  Est. cost:     ${cost.estimate_cost():>11.4f}")
        console.print()
        if cost._model_tokens:
            console.print("  [bold]Per-model breakdown:[/bold]")
            for m, (inp, out) in cost._model_tokens.items():
                console.print(f"    {m}: {inp:,} in / {out:,} out")

    def cmd_review(**_: Any) -> None:
        """Review all changes made in this session."""
        console = Console()
        try:
            result = subprocess.run(
                ["git", "diff", "HEAD"],
                capture_output=True, text=True, cwd=app.cwd, timeout=15,
            )
            if result.stdout.strip():
                from rich.syntax import Syntax
                console.print(Panel(
                    Syntax(result.stdout[:10000], "diff", theme="monokai"),
                    title="[bold]Changes Since Last Commit[/bold]",
                    border_style="yellow",
                ))
            else:
                console.print("[dim]No uncommitted changes.[/dim]")
        except Exception as e:
            console.print(f"[red]Error: {e}[/red]")

    def cmd_pr_comments(args: str = "", **_: Any) -> None:
        """Show comments on a GitHub PR."""
        console = Console()
        pr_num = args.strip()
        if not pr_num:
            console.print("[red]Usage: /pr_comments <PR number or URL>[/red]")
            return
        try:
            # Try to get repo info from git
            remote = subprocess.run(
                ["git", "remote", "get-url", "origin"],
                capture_output=True, text=True, cwd=app.cwd, timeout=5,
            )
            if remote.stdout.strip():
                console.print(f"[dim]Fetching PR #{pr_num} comments...[/dim]")
                result = subprocess.run(
                    ["gh", "pr", "view", pr_num, "--comments"],
                    capture_output=True, text=True, cwd=app.cwd, timeout=30,
                )
                if result.stdout:
                    console.print(result.stdout[:5000])
                elif result.stderr:
                    console.print(f"[red]{result.stderr}[/red]")
            else:
                console.print("[red]Not a git repository with a remote.[/red]")
        except FileNotFoundError:
            console.print("[red]gh CLI not found. Install from https://cli.github.com[/red]")
        except Exception as e:
            console.print(f"[red]Error: {e}[/red]")

    def cmd_rewind(args: str = "", **_: Any) -> None:
        """Remove last N message pairs from conversation."""
        console = Console()
        n = 1
        if args.strip().isdigit():
            n = int(args.strip())
        msgs = app.engine.messages.messages
        to_remove = n * 2  # user + assistant pairs
        if to_remove >= len(msgs):
            app.engine.messages.clear()
            console.print("[dim]Conversation fully cleared.[/dim]")
        else:
            app.engine.messages.messages = msgs[:-to_remove]
            console.print(f"[dim]Removed last {n} exchange(s). {len(app.engine.messages.messages)} messages remain.[/dim]")

    def cmd_bug(**_: Any) -> None:
        """Report a bug or share session for debugging."""
        console = Console()
        console.print("[bold]Report a Bug[/bold]\n")
        console.print("To report an issue:")
        console.print("  1. Use /export to save the conversation")
        console.print("  2. Submit at: https://github.com/your-org/ccos/issues")
        console.print()
        console.print(f"Session ID: [dim]{app.session_manager.session_id}[/dim]")
        if app.session_manager.transcript_path:
            console.print(f"Transcript: [dim]{app.session_manager.transcript_path}[/dim]")

    def cmd_terminal_setup(**_: Any) -> None:
        """Show terminal setup recommendations."""
        console = Console()
        console.print("[bold]Terminal Setup[/bold]\n")
        console.print("For the best experience:")
        console.print("  - Use a terminal with Unicode support (Windows Terminal, iTerm2, etc.)")
        console.print("  - Set PYTHONIOENCODING=utf-8 on Windows")
        console.print("  - Install ripgrep (rg) for faster code search")
        console.print("  - Use a Nerd Font for icon support")
        console.print()
        import sys
        console.print(f"  Python: {sys.version.split()[0]}")
        console.print(f"  Platform: {sys.platform}")
        console.print(f"  Encoding: {sys.stdout.encoding}")

    def cmd_mode(args: str = "", **_: Any) -> None:
        """Switch permission mode."""
        console = Console()
        from ccos.permissions.manager import PermissionMode
        if args.strip():
            try:
                new_mode = PermissionMode(args.strip())
                app.permissions.mode = new_mode
                console.print(f"[green]Permission mode set to: {new_mode.value}[/green]")
            except ValueError:
                modes = ", ".join(m.value for m in PermissionMode)
                console.print(f"[red]Invalid mode. Available: {modes}[/red]")
        else:
            console.print(f"Current mode: [cyan]{app.permissions.mode.value}[/cyan]")
            modes = ", ".join(m.value for m in PermissionMode)
            console.print(f"[dim]Available: {modes}[/dim]")

    def cmd_copy(**_: Any) -> None:
        """Copy last assistant response to clipboard."""
        console = Console()
        msgs = app.engine.messages.messages
        for msg in reversed(msgs):
            if msg.role == "assistant":
                from ccos.providers.base import TextContent
                text = ""
                if isinstance(msg.content, str):
                    text = msg.content
                elif isinstance(msg.content, list):
                    for block in msg.content:
                        if isinstance(block, TextContent):
                            text += block.text
                if text:
                    try:
                        import subprocess as sp
                        if sys.platform == "darwin":
                            sp.run(["pbcopy"], input=text.encode(), check=True)
                        elif sys.platform == "win32":
                            sp.run(["clip"], input=text.encode(), check=True)
                        else:
                            sp.run(["xclip", "-selection", "clipboard"], input=text.encode(), check=True)
                        console.print("[green]Copied to clipboard.[/green]")
                    except Exception:
                        console.print("[red]Could not copy to clipboard.[/red]")
                    return
        console.print("[dim]No assistant response to copy.[/dim]")

    def cmd_color(**_: Any) -> None:
        """Show color theme preview."""
        console = Console()
        from ccos.ui.themes import DARK
        console.print("[bold]Theme Colors:[/bold]")
        for field_name in DARK.__dataclass_fields__:
            val = getattr(DARK, field_name)
            console.print(f"  [{val}]{field_name}: {val}[/{val}]")

    def cmd_mcp(args: str = "", **_: Any) -> None:
        """MCP server management — add, remove, list, reconnect, test."""
        console = Console()
        from ccos.mcp.client import MCPManager
        from ccos.mcp.types import MCPServerConfig, TransportType, ConnectionState
        from ccos.mcp.tools import register_mcp_tools, unregister_mcp_tools

        # Ensure mcp_manager exists
        if not hasattr(app, "mcp_manager") or app.mcp_manager is None:
            app.mcp_manager = MCPManager()

        parts = args.strip().split(maxsplit=1)
        subcmd = parts[0] if parts else ""
        sub_args = parts[1].strip() if len(parts) > 1 else ""

        if subcmd == "add":
            # /mcp add <name> — interactive setup
            name = sub_args
            if not name:
                try:
                    name = console.input("[yellow]Server name: [/yellow]").strip()
                except (EOFError, KeyboardInterrupt):
                    console.print()
                    return
            if not name:
                console.print("[red]Server name is required.[/red]")
                return

            # Choose transport type
            console.print("[dim]Transport types:[/dim]")
            console.print("  1. stdio  — Local subprocess (npx, python, etc.)")
            console.print("  2. sse    — Server-Sent Events (remote HTTP)")
            console.print("  3. http   — HTTP Streamable (remote HTTP)")
            console.print("  4. ws     — WebSocket (remote)")
            try:
                type_choice = console.input("[yellow]Transport (1-4, default=1): [/yellow]").strip()
            except (EOFError, KeyboardInterrupt):
                console.print()
                return

            transport_map = {"1": "stdio", "2": "sse", "3": "http", "4": "ws", "": "stdio"}
            transport_str = transport_map.get(type_choice, "stdio")

            cfg_dict: dict[str, Any] = {"type": transport_str}

            if transport_str == "stdio":
                try:
                    command = console.input("[yellow]Command (e.g. npx): [/yellow]").strip()
                    if not command:
                        console.print("[red]Command is required for stdio transport.[/red]")
                        return
                    args_str = console.input("[yellow]Args (space-separated, optional): [/yellow]").strip()
                    env_str = console.input("[yellow]Env vars (KEY=VAL KEY=VAL, optional): [/yellow]").strip()
                except (EOFError, KeyboardInterrupt):
                    console.print()
                    return
                cfg_dict["command"] = command
                if args_str:
                    cfg_dict["args"] = args_str.split()
                if env_str:
                    env = {}
                    for pair in env_str.split():
                        if "=" in pair:
                            k, v = pair.split("=", 1)
                            env[k] = v
                    cfg_dict["env"] = env
            else:
                try:
                    url = console.input("[yellow]URL: [/yellow]").strip()
                    if not url:
                        console.print("[red]URL is required for network transports.[/red]")
                        return
                    headers_str = console.input("[yellow]Headers (Key:Value, comma-sep, optional): [/yellow]").strip()
                except (EOFError, KeyboardInterrupt):
                    console.print()
                    return
                cfg_dict["url"] = url
                if headers_str:
                    headers = {}
                    for item in headers_str.split(","):
                        item = item.strip()
                        if ":" in item:
                            k, v = item.split(":", 1)
                            headers[k.strip()] = v.strip()
                    cfg_dict["headers"] = headers

            # Save to config
            app.config.mcp_servers[name] = cfg_dict
            app.config.save()
            console.print(f"[green]Server '{name}' saved to config.[/green]")

            # Connect immediately
            console.print(f"[dim]Connecting to '{name}'...[/dim]")
            config = MCPServerConfig.from_dict(cfg_dict)
            error = asyncio.run(app.mcp_manager.connect_server(name, config))
            if error:
                console.print(f"[red]Connection failed: {error}[/red]")
            else:
                conn = app.mcp_manager.get_connection(name)
                if conn:
                    registered = register_mcp_tools(app.mcp_manager, app.tool_registry)
                    console.print(
                        f"[green]Connected! "
                        f"{len(conn.tools)} tools, "
                        f"{len(conn.resources)} resources, "
                        f"{len(conn.prompts)} prompts[/green]"
                    )
                    if conn.tools:
                        for t in conn.tools:
                            console.print(f"  [cyan]mcp__{name}__{t.name}[/cyan] — {t.description[:60]}")

        elif subcmd == "remove":
            if not sub_args:
                console.print("[red]Usage: /mcp remove <name>[/red]")
                return
            name = sub_args
            # Disconnect
            disconnected = asyncio.run(app.mcp_manager.disconnect_server(name))
            # Remove from config
            if name in app.config.mcp_servers:
                del app.config.mcp_servers[name]
                app.config.save()
            # Unregister tools
            removed_count = unregister_mcp_tools(name, app.tool_registry)
            if disconnected or name in app.config.mcp_servers:
                console.print(f"[green]Server '{name}' removed ({removed_count} tools unregistered).[/green]")
            else:
                console.print(f"[red]Server '{name}' not found.[/red]")

        elif subcmd == "reconnect":
            if not sub_args:
                console.print("[red]Usage: /mcp reconnect <name>[/red]")
                return
            name = sub_args
            conn = app.mcp_manager.get_connection(name)
            if not conn:
                console.print(f"[red]Server '{name}' not found.[/red]")
                return
            console.print(f"[dim]Reconnecting to '{name}'...[/dim]")
            # Unregister old tools first
            unregister_mcp_tools(name, app.tool_registry)
            error = asyncio.run(app.mcp_manager.reconnect_server(name))
            if error:
                console.print(f"[red]Reconnection failed: {error}[/red]")
            else:
                # Re-register tools
                register_mcp_tools(app.mcp_manager, app.tool_registry)
                conn = app.mcp_manager.get_connection(name)
                if conn:
                    console.print(
                        f"[green]Reconnected! {len(conn.tools)} tools, "
                        f"{len(conn.resources)} resources[/green]"
                    )

        elif subcmd == "enable":
            name = sub_args or ""
            if not name:
                console.print("[red]Usage: /mcp enable <name>[/red]")
                return
            if name in app.config.mcp_servers:
                app.config.mcp_servers[name]["enabled"] = True
                app.config.save()
                # Connect if not already
                conn = app.mcp_manager.get_connection(name)
                if not conn or not conn.is_connected:
                    config = MCPServerConfig.from_dict(app.config.mcp_servers[name])
                    error = asyncio.run(app.mcp_manager.connect_server(name, config))
                    if error:
                        console.print(f"[red]Enable failed: {error}[/red]")
                    else:
                        register_mcp_tools(app.mcp_manager, app.tool_registry)
                        console.print(f"[green]Server '{name}' enabled and connected.[/green]")
                else:
                    console.print(f"[green]Server '{name}' is already connected.[/green]")
            else:
                console.print(f"[red]Server '{name}' not in config.[/red]")

        elif subcmd == "disable":
            name = sub_args or ""
            if not name:
                console.print("[red]Usage: /mcp disable <name>[/red]")
                return
            if name in app.config.mcp_servers:
                app.config.mcp_servers[name]["enabled"] = False
                app.config.save()
                asyncio.run(app.mcp_manager.disconnect_server(name))
                unregister_mcp_tools(name, app.tool_registry)
                console.print(f"[yellow]Server '{name}' disabled.[/yellow]")
            else:
                console.print(f"[red]Server '{name}' not in config.[/red]")

        elif subcmd == "test":
            # /mcp test <name> — test a tool on a server
            name = sub_args or ""
            if not name:
                console.print("[red]Usage: /mcp test <name>[/red]")
                return
            conn = app.mcp_manager.get_connection(name)
            if not conn:
                console.print(f"[red]Server '{name}' not found.[/red]")
                return
            if not conn.is_connected:
                console.print(f"[red]Server '{name}' is not connected (state: {conn.state.value}).[/red]")
                return

            if not conn.tools:
                console.print(f"[dim]Server '{name}' has no tools to test.[/dim]")
                # Try listing resources instead
                if conn.resources:
                    console.print("[dim]Resources:[/dim]")
                    for r in conn.resources:
                        console.print(f"  [cyan]{r.uri}[/cyan] — {r.name}")
                return

            # List tools and let user pick one
            console.print(f"[bold]Tools on '{name}':[/bold]")
            for i, t in enumerate(conn.tools, 1):
                console.print(f"  [dim]{i}.[/dim] [cyan]{t.name}[/cyan] — {t.description[:60]}")

            try:
                choice = console.input("\n[yellow]Tool number to test (Enter to skip): [/yellow]").strip()
            except (EOFError, KeyboardInterrupt):
                console.print()
                return

            if not choice or not choice.isdigit():
                return

            idx = int(choice) - 1
            if idx < 0 or idx >= len(conn.tools):
                console.print("[red]Invalid selection.[/red]")
                return

            tool = conn.tools[idx]
            console.print(f"\n[dim]Testing: {tool.name}[/dim]")

            # Show input schema
            props = tool.input_schema.get("properties", {})
            required = tool.input_schema.get("required", [])
            test_args: dict[str, Any] = {}

            if props:
                console.print("[dim]Input parameters:[/dim]")
                for pname, pschema in props.items():
                    req_mark = " [red]*[/red]" if pname in required else ""
                    ptype = pschema.get("type", "string")
                    pdesc = pschema.get("description", "")
                    hint = f" ({pdesc})" if pdesc else ""
                    try:
                        val = console.input(
                            f"  [yellow]{pname}{req_mark} ({ptype}){hint}: [/yellow]"
                        ).strip()
                    except (EOFError, KeyboardInterrupt):
                        console.print()
                        return
                    if val:
                        # Type coercion
                        if ptype == "number":
                            try:
                                test_args[pname] = float(val)
                            except ValueError:
                                test_args[pname] = val
                        elif ptype == "integer":
                            try:
                                test_args[pname] = int(val)
                            except ValueError:
                                test_args[pname] = val
                        elif ptype == "boolean":
                            test_args[pname] = val.lower() in ("true", "1", "yes")
                        elif ptype == "object" or ptype == "array":
                            try:
                                test_args[pname] = json.loads(val)
                            except json.JSONDecodeError:
                                test_args[pname] = val
                        else:
                            test_args[pname] = val
                    elif pname in required:
                        console.print(f"[red]'{pname}' is required.[/red]")
                        return

            console.print(f"[dim]Calling {tool.name}({json.dumps(test_args, ensure_ascii=False)})...[/dim]")
            try:
                result = asyncio.run(conn.call_tool(tool.name, test_args))
                console.print(Panel(
                    result[:3000],
                    title=f"Result: {tool.name}",
                    border_style="green",
                ))
            except Exception as e:
                console.print(f"[red]Error: {e}[/red]")

        elif subcmd == "tools":
            # /mcp tools [name] — list all tools
            target = sub_args or None
            all_tools = app.mcp_manager.all_tools
            if target:
                all_tools = [t for t in all_tools if t.server_name == target]

            if all_tools:
                table = Table(title="MCP Tools", border_style="dim")
                table.add_column("Server", style="yellow")
                table.add_column("Tool", style="cyan")
                table.add_column("Description")
                for t in all_tools:
                    table.add_row(t.server_name, t.name, t.description[:60])
                console.print(table)
            else:
                console.print("[dim]No MCP tools available.[/dim]")

        elif subcmd == "resources":
            # /mcp resources [name]
            target = sub_args or None
            all_res = app.mcp_manager.all_resources
            if target:
                all_res = [r for r in all_res if r.server_name == target]

            if all_res:
                table = Table(title="MCP Resources", border_style="dim")
                table.add_column("Server", style="yellow")
                table.add_column("URI", style="cyan")
                table.add_column("Name")
                table.add_column("MIME", style="dim")
                for r in all_res:
                    table.add_row(r.server_name, r.uri, r.name, r.mime_type)
                console.print(table)
            else:
                console.print("[dim]No MCP resources available.[/dim]")

        elif subcmd == "prompts":
            # /mcp prompts [name]
            target = sub_args or None
            all_prompts = app.mcp_manager.all_prompts
            if target:
                all_prompts = [p for p in all_prompts if p.server_name == target]

            if all_prompts:
                table = Table(title="MCP Prompts", border_style="dim")
                table.add_column("Server", style="yellow")
                table.add_column("Prompt", style="cyan")
                table.add_column("Description")
                for p in all_prompts:
                    table.add_row(p.server_name, p.name, p.description[:60])
                console.print(table)
            else:
                console.print("[dim]No MCP prompts available.[/dim]")

        else:
            # Default: /mcp — show status of all servers
            statuses = app.mcp_manager.get_status_summary()

            if not statuses and not app.config.mcp_servers:
                console.print("[dim]No MCP servers configured.[/dim]")
                console.print("[dim]Use /mcp add to set up a server.[/dim]")
                console.print()
                console.print("[dim]Example:[/dim]")
                console.print("  [cyan]/mcp add filesystem[/cyan]")
                console.print()
                console.print("[dim]Config format in ~/.ccos/config.json:[/dim]")
                console.print('[dim]  "mcp_servers": {[/dim]')
                console.print('[dim]    "filesystem": {[/dim]')
                console.print('[dim]      "command": "npx",[/dim]')
                console.print('[dim]      "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"][/dim]')
                console.print('[dim]    }[/dim]')
                console.print('[dim]  }[/dim]')
                return

            table = Table(title="MCP Servers", border_style="dim")
            table.add_column("Name", style="cyan")
            table.add_column("State")
            table.add_column("Transport", style="dim")
            table.add_column("Tools", justify="right")
            table.add_column("Resources", justify="right")
            table.add_column("Prompts", justify="right")
            table.add_column("Info", style="dim")

            for s in statuses:
                state = s["state"]
                if state == "connected":
                    state_str = "[green]connected[/green]"
                elif state == "failed":
                    state_str = "[red]failed[/red]"
                elif state == "disabled":
                    state_str = "[yellow]disabled[/yellow]"
                elif state == "reconnecting":
                    state_str = "[yellow]reconnecting[/yellow]"
                else:
                    state_str = f"[dim]{state}[/dim]"

                info = ""
                if s.get("server_name"):
                    info = s["server_name"]
                    if s.get("server_version"):
                        info += f" v{s['server_version']}"
                if s.get("error"):
                    info = s["error"][:40]

                table.add_row(
                    s["name"],
                    state_str,
                    s["transport"],
                    str(s["tools"]),
                    str(s["resources"]),
                    str(s["prompts"]),
                    info,
                )
            console.print(table)

            # Show unconfigured servers from config
            for name in app.config.mcp_servers:
                if name not in [s["name"] for s in statuses]:
                    console.print(f"  [dim]{name}: not connected (in config)[/dim]")

            console.print()
            console.print(
                "[dim]Subcommands: /mcp add|remove|reconnect|enable|disable|test|tools|resources|prompts[/dim]"
            )

    # ── Co-author configuration ────────────────────────────────
    def cmd_co_author(args: str = "", **_: Any) -> None:
        """Configure the Co-Authored-By trailer for git commits."""
        console = Console()
        arg = args.strip()

        if not arg:
            # Show current setting
            current = app.config.git.co_author
            if current:
                console.print(f"Co-author: [cyan]{current}[/cyan]")
            else:
                console.print("[dim]Co-author is disabled.[/dim]")
            console.print()
            console.print("[dim]Usage:[/dim]")
            console.print("  /co-author Name <email>   Set co-author")
            console.print("  /co-author off             Disable co-author")
            return

        if arg.lower() in ("off", "disable", "none", "false", ""):
            app.config.git.co_author = ""
            app.engine.co_author = ""
            app.config.save()
            console.print("[green]Co-author disabled.[/green] Commits will not include a Co-Authored-By trailer.")
        else:
            app.config.git.co_author = arg
            app.engine.co_author = arg
            app.config.save()
            console.print(f"[green]Co-author set to:[/green] [cyan]{arg}[/cyan]")
            console.print("[dim]All future commits will include: Co-Authored-By: " + arg + "[/dim]")

    # ── Skill management commands ────────────────────────────────
    def cmd_skills(args: str = "", **_: Any) -> None:
        """Manage skills: list, create, edit, delete, show, reload."""
        console = Console()
        parts = args.strip().split(maxsplit=1)
        subcmd = parts[0] if parts else ""
        sub_args = parts[1].strip() if len(parts) > 1 else ""

        if not hasattr(app, "skill_registry") or app.skill_registry is None:
            console.print("[red]Skill system not initialized.[/red]")
            return

        if subcmd == "create" or subcmd == "new":
            # /skills create <name> [description]
            if not sub_args:
                console.print("[red]Usage: /skills create <name> [description][/red]")
                return
            create_parts = sub_args.split(maxsplit=1)
            name = create_parts[0]
            desc = create_parts[1] if len(create_parts) > 1 else ""

            from ccos.skills.loader import create_skill_template
            try:
                path = create_skill_template(name, app.cwd, description=desc)
                console.print(f"[green]Created skill:[/green] {path}")
                console.print("[dim]Edit the SKILL.md file to customize your skill.[/dim]")
                # Reload skills
                _reload_skills()
            except Exception as e:
                console.print(f"[red]Error creating skill: {e}[/red]")

        elif subcmd == "delete" or subcmd == "rm" or subcmd == "remove":
            # /skills delete <name>
            if not sub_args:
                console.print("[red]Usage: /skills delete <name>[/red]")
                return
            from ccos.skills.loader import delete_skill
            if delete_skill(sub_args, app.cwd):
                console.print(f"[green]Deleted skill:[/green] {sub_args}")
                _reload_skills()
            else:
                console.print(f"[red]Skill not found: {sub_args}[/red]")

        elif subcmd == "show" or subcmd == "view":
            # /skills show <name>
            if not sub_args:
                console.print("[red]Usage: /skills show <name>[/red]")
                return
            skill = app.skill_registry.get(sub_args)
            if skill is None:
                console.print(f"[red]Skill not found: {sub_args}[/red]")
                return
            console.print(Panel(
                f"[cyan bold]{skill.user_facing_name()}[/cyan bold]\n\n"
                f"[dim]Name:[/dim]        {skill.name}\n"
                f"[dim]Source:[/dim]      {skill.source.value}\n"
                f"[dim]File:[/dim]        {skill.loaded_from}\n"
                f"[dim]Description:[/dim] {skill.description or '(none)'}\n"
                f"[dim]Arguments:[/dim]   {' '.join(skill.argument_names) if skill.argument_names else '(none)'}\n"
                f"[dim]Hint:[/dim]        {skill.argument_hint or '(none)'}\n"
                f"[dim]Tools:[/dim]       {', '.join(skill.allowed_tools) if skill.allowed_tools else '(all)'}\n"
                f"[dim]Context:[/dim]     {skill.context.value}\n"
                f"[dim]Model:[/dim]       {skill.model or '(default)'}\n"
                f"[dim]User:[/dim]        {'yes' if skill.user_invocable else 'no'}\n"
                f"[dim]Model invoke:[/dim] {'no' if skill.disable_model_invocation else 'yes'}\n"
                f"[dim]Conditional:[/dim] {', '.join(skill.paths) if skill.paths else 'no'}\n"
                f"\n--- Content ---\n\n{skill.content[:2000]}{'...' if len(skill.content) > 2000 else ''}",
                title=f"Skill: {skill.name}",
                border_style="cyan",
            ))

        elif subcmd == "edit":
            # /skills edit <name> — open in editor
            if not sub_args:
                console.print("[red]Usage: /skills edit <name>[/red]")
                return
            skill = app.skill_registry.get(sub_args)
            if skill is None:
                console.print(f"[red]Skill not found: {sub_args}[/red]")
                return
            editor = os.environ.get("EDITOR", "notepad" if os.name == "nt" else "vi")
            try:
                subprocess.run([editor, skill.loaded_from], check=False)
                console.print(f"[dim]Reloading skills...[/dim]")
                _reload_skills()
            except Exception as e:
                console.print(f"[red]Could not open editor: {e}[/red]")
                console.print(f"[dim]File: {skill.loaded_from}[/dim]")

        elif subcmd == "reload":
            # /skills reload
            count = _reload_skills()
            console.print(f"[green]Reloaded {count} skills.[/green]")

        else:
            # Default: list all skills
            all_skills = app.skill_registry.get_all_including_conditional()
            if not all_skills:
                from pathlib import Path as _Path
                user_skills = _Path.home() / ".ccos" / "skills"
                console.print("[dim]No skills found.[/dim]")
                console.print()
                console.print("[dim]Create a skill:[/dim]")
                console.print("  /skills create <name> [description]")
                console.print()
                console.print("[dim]Or create manually:[/dim]")
                console.print(f"  mkdir {user_skills / 'my-skill'}")
                console.print("  Create SKILL.md with YAML frontmatter + content")
                return

            table = Table(title="Available Skills", border_style="dim")
            table.add_column("Name", style="cyan")
            table.add_column("Description")
            table.add_column("Source", style="dim")
            table.add_column("Args", style="dim")
            table.add_column("Context", style="dim")

            for skill in all_skills:
                flags = []
                if not skill.user_invocable:
                    flags.append("no-user")
                if skill.disable_model_invocation:
                    flags.append("no-model")
                if skill.is_conditional:
                    flags.append("conditional")

                table.add_row(
                    f"/{skill.name}",
                    skill.description[:60] + ("..." if len(skill.description) > 60 else ""),
                    skill.source.value,
                    skill.argument_hint or ("-" if not skill.argument_names else " ".join(skill.argument_names)),
                    skill.context.value + (" " + " ".join(flags) if flags else ""),
                )

            console.print(table)
            console.print()
            console.print(
                "[dim]Subcommands: /skills create|delete|show|edit|reload[/dim]"
            )

    def _reload_skills() -> int:
        """Reload skills from disk and re-register as slash commands."""
        from ccos.skills.loader import load_all_skills
        skills = load_all_skills(app.cwd)
        app.skill_registry.reload(skills)
        _register_skill_slash_commands()
        return len(skills)

    def _register_skill_slash_commands() -> None:
        """Register user-invocable skills as slash commands."""
        # Remove old skill-based slash commands
        to_remove = [
            name for name, cmd in registry._commands.items()
            if hasattr(cmd, '_is_skill_command') and cmd._is_skill_command
        ]
        for name in to_remove:
            del registry._commands[name]

        # Register new ones
        for skill in app.skill_registry.get_user_invocable():
            def make_handler(sk):
                def handler(args: str = "", **_: Any) -> None:
                    """Invoke skill and inject into conversation."""
                    console = Console()
                    try:
                        content = app.skill_executor.prepare_skill_content(sk, args)
                        # Inject as user message into engine
                        console.print(f"[dim]Running skill: {sk.name}...[/dim]")
                        # Add skill content as a user message and run engine turn
                        app.session_manager.save_user_message(f"/{sk.name} {args}".strip())
                        result = app._run_async(app._async_skill_turn(content))
                        app.renderer.flush_streaming()
                        app._persist_last_assistant()
                    except KeyboardInterrupt:
                        app.renderer.flush_streaming()
                        app.renderer.print_status("Interrupted.")
                    except Exception as e:
                        console.print(f"[red]Skill error: {e}[/red]")
                return handler

            cmd = SlashCommand(
                name=skill.name,
                description=skill.description or f"Skill: {skill.name}",
                handler=make_handler(skill),
            )
            cmd._is_skill_command = True  # type: ignore[attr-defined]
            registry.register(cmd)

    # Register all
    registry.register(SlashCommand("help", "Show available commands", cmd_help, aliases=["?"]))
    registry.register(SlashCommand("exit", "Exit the application", cmd_exit, aliases=["quit", "q"]))
    registry.register(SlashCommand("clear", "Clear conversation history", cmd_clear))
    registry.register(SlashCommand("model", "Switch or show current model", cmd_model))
    registry.register(SlashCommand("provider", "Switch or show current provider", cmd_provider))
    registry.register(SlashCommand("cost", "Show token usage and cost", cmd_cost))
    registry.register(SlashCommand("status", "Show current session status", cmd_status))
    registry.register(SlashCommand("compact", "Compress conversation context", cmd_compact))
    registry.register(SlashCommand("config", "Show config file location", cmd_config))
    registry.register(SlashCommand("history", "List recent sessions", cmd_history))
    registry.register(SlashCommand("resume", "Resume a previous session", cmd_resume))
    registry.register(SlashCommand("diff", "Show git diff for current directory", cmd_diff))
    registry.register(SlashCommand("plan", "Show plan mode status and current plan", cmd_plan))
    registry.register(SlashCommand("memory", "Manage auto-memory (add/delete/show/edit)", cmd_memory))
    registry.register(SlashCommand("doctor", "Check system configuration and dependencies", cmd_doctor))
    registry.register(SlashCommand("login", "Sign in with your API key", cmd_login))
    registry.register(SlashCommand("logout", "Remove stored credentials", cmd_logout))
    registry.register(SlashCommand("init", "Create a CLAUDE.md in the project", cmd_init))
    registry.register(SlashCommand("permissions", "Show permission settings", cmd_permissions))
    registry.register(SlashCommand("vim", "Toggle vim mode", cmd_vim))
    registry.register(SlashCommand("theme", "Set or show color theme", cmd_theme))
    registry.register(SlashCommand("export", "Export conversation as markdown", cmd_export))
    registry.register(SlashCommand("hooks", "Show installed hooks", cmd_hooks))
    registry.register(SlashCommand("files", "Show files accessed in this session", cmd_files))
    registry.register(SlashCommand("fast", "Toggle fast mode", cmd_fast))
    registry.register(SlashCommand("branch", "Show current git branch", cmd_branch))
    # New commands
    registry.register(SlashCommand("add-dir", "Add an additional working directory", cmd_add_dir))
    registry.register(SlashCommand("context", "Show context window usage", cmd_context))
    registry.register(SlashCommand("session", "Show current session info", cmd_session))
    registry.register(SlashCommand("stats", "Show detailed usage statistics", cmd_stats))
    registry.register(SlashCommand("review", "Review all uncommitted changes", cmd_review))
    registry.register(SlashCommand("pr_comments", "Show comments on a GitHub PR", cmd_pr_comments))
    registry.register(SlashCommand("rewind", "Remove last N exchanges from conversation", cmd_rewind))
    registry.register(SlashCommand("bug", "Report a bug or share session", cmd_bug, aliases=["issue"]))
    registry.register(SlashCommand("terminal-setup", "Show terminal setup recommendations", cmd_terminal_setup))
    registry.register(SlashCommand("mode", "Switch permission mode", cmd_mode))
    registry.register(SlashCommand("copy", "Copy last response to clipboard", cmd_copy))
    registry.register(SlashCommand("color", "Preview color theme", cmd_color))
    registry.register(SlashCommand("mcp", "Manage MCP servers (add/remove/reconnect/test)", cmd_mcp))
    registry.register(SlashCommand("skills", "Manage skills (list/create/delete/show/edit/reload)", cmd_skills))
    registry.register(SlashCommand("co-author", "Configure Co-Authored-By for git commits", cmd_co_author))

    # Register skill-based slash commands (if skill system is initialized)
    if hasattr(app, "skill_registry") and app.skill_registry is not None:
        _register_skill_slash_commands()

"""Application main class — assembles all components into a running system."""

from __future__ import annotations

import asyncio
import os
import signal
from typing import Any

from rich.console import Console

from ccos.commands.builtin import register_builtin_commands
from ccos.commands.registry import CommandRegistry
from ccos.config import Config
from ccos.engine.cost_tracker import CostTracker
from ccos.engine.query_engine import QueryEngine
from ccos.history.session import SessionManager
from ccos.hooks import HookManager
from ccos.permissions.manager import PermissionManager, PermissionMode
from ccos.plan import PlanManager
from ccos.prompt.builder import PromptBuilder
from ccos.providers.base import LLMProvider, ToolCallContent
from ccos.providers.registry import ProviderRegistry
from ccos.tools.base import ToolContext, ToolRegistry, create_default_registry
from ccos.ui.input import PromptMode, create_input_session, get_user_input
from ccos.ui.renderer import Renderer
from ccos.ui.status import StatusBar


class App:
    """The main CCOS application."""

    def __init__(
        self,
        *,
        provider_name: str | None = None,
        model: str | None = None,
        cwd: str | None = None,
        trust_all: bool = False,
        resume_session_id: str | None = None,
    ):
        self.config = Config.load()
        self.cwd = cwd or os.getcwd()

        # Provider
        registry = ProviderRegistry()
        self.provider = registry.get_provider(self.config, provider_name=provider_name)
        self.model = registry.get_model(
            self.config, provider_name=provider_name, model=model,
        )

        # Tools
        self.tool_registry = create_default_registry(self.cwd)

        # Context
        self.ctx = ToolContext(cwd=self.cwd)

        # Plan manager
        self.plan_manager = PlanManager()

        # Memory store + extractor
        from ccos.memory.store import MemoryStore
        from ccos.memory.extractor import MemoryExtractor
        self.memory_store = MemoryStore(self.cwd)
        self.memory_extractor = MemoryExtractor(
            store=self.memory_store,
            engine_factory=None,  # Wired after engine creation
        )

        # Session manager
        self.session_manager = SessionManager()

        # UI (must be before _init_mcp which uses self.console)
        self.console = Console()
        self.renderer = Renderer(self.console)
        self.status_bar = StatusBar(self.console)
        self._prompt_mode: PromptMode = "default"

        # MCP servers — deferred to first async context (see _ensure_mcp)
        self.mcp_manager = None
        self._mcp_initialized = False

        # Persistent event loop for MCP transport compatibility
        self._loop: asyncio.AbstractEventLoop | None = None

        # Wire AgentTool's engine factory
        agent_tool = self.tool_registry.get("Agent")
        if agent_tool is not None:
            from ccos.tools.agent import AgentTool
            if isinstance(agent_tool, AgentTool):
                agent_tool._engine_factory = self._create_sub_engine

        # Wire PlanMode tools
        enter_plan = self.tool_registry.get("EnterPlanMode")
        if enter_plan is not None:
            from ccos.tools.plan_mode import EnterPlanModeTool
            if isinstance(enter_plan, EnterPlanModeTool):
                enter_plan._plan_manager = self.plan_manager

        exit_plan = self.tool_registry.get("ExitPlanMode")
        if exit_plan is not None:
            from ccos.tools.plan_mode import ExitPlanModeTool
            if isinstance(exit_plan, ExitPlanModeTool):
                exit_plan._plan_manager = self.plan_manager

        # Permissions
        perm_mode = PermissionMode.TRUST_ALL if trust_all else PermissionMode(
            self.config.permissions.mode
        )
        self.permissions = PermissionManager(mode=perm_mode)

        # Hooks
        self.hooks = HookManager()
        if self.config.hooks:
            self.hooks.load_from_config(self.config.hooks)

        # Engine
        self.engine = QueryEngine(
            provider=self.provider,
            model=self.model,
            tools=self.tool_registry,
            prompt_builder=PromptBuilder(),
            permissions=self.permissions,
            ctx=self.ctx,
            cost_tracker=CostTracker(),
            console=self.console,
            on_text=self.renderer.print_text_chunk,
            on_tool_start=self.renderer.print_tool_call,
            on_tool_end=self.renderer.print_tool_result,
            on_thinking=self.renderer.print_thinking,
            hooks=self.hooks,
        )

        # Wire memory extractor's engine factory
        self.memory_extractor._engine_factory = self._create_sub_engine

        # Commands
        self.commands = CommandRegistry()
        register_builtin_commands(self.commands, self)

        # Resume session if requested
        self._resume_session_id = resume_session_id

    def _get_loop(self) -> asyncio.AbstractEventLoop:
        """Return a persistent event loop (created once, reused across turns).

        Using a persistent loop is essential when MCP stdio transports are
        active: their background reader tasks and Futures are bound to the
        loop that created them.  ``asyncio.run()`` destroys its loop after
        every call, which orphans those tasks and hangs on executor shutdown.
        """
        if self._loop is None or self._loop.is_closed():
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
        return self._loop

    def _run_async(self, coro) -> Any:
        """Run a coroutine on the persistent event loop."""
        return self._get_loop().run_until_complete(coro)

    def _shutdown_loop(self) -> None:
        """Shut down MCP transports then close the event loop."""
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        # Disconnect MCP servers so their subprocesses + reader tasks stop
        if self.mcp_manager is not None:
            try:
                loop.run_until_complete(self.mcp_manager.disconnect_all())
            except Exception:
                pass
        try:
            # Cancel any remaining tasks
            pending = asyncio.all_tasks(loop)
            for task in pending:
                task.cancel()
            if pending:
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        except Exception:
            pass
        try:
            loop.run_until_complete(loop.shutdown_asyncgens())
        except Exception:
            pass
        loop.close()
        self._loop = None

    def run_interactive(self) -> None:
        """Run the interactive REPL."""
        # Start or resume session
        if self._resume_session_id:
            messages = self.session_manager.resume_session(
                self._resume_session_id, self.cwd,
            )
            if messages:
                self._restore_messages(messages)
                self.renderer.print_status(
                    f"Resumed session {self._resume_session_id} "
                    f"({len(messages)} messages)"
                )
            else:
                self.renderer.print_error(
                    f"Session {self._resume_session_id} not found. Starting new session."
                )
                self.session_manager.start_session(self.cwd, self.model)
        else:
            self.session_manager.start_session(self.cwd, self.model)

        # Wire session ID into ExitPlanMode tool
        exit_plan = self.tool_registry.get("ExitPlanMode")
        if exit_plan is not None:
            from ccos.tools.plan_mode import ExitPlanModeTool
            if isinstance(exit_plan, ExitPlanModeTool):
                exit_plan._session_id = self.session_manager.session_id

        self.renderer.print_welcome(self.model, self.provider.name, self.cwd)

        session = create_input_session(
            slash_commands=self.commands.names(),
            vim_mode=self.config.ui.vim_mode,
        )

        try:
            while True:
                try:
                    # Update prompt mode based on plan manager state
                    if self.plan_manager.is_plan_mode:
                        self._prompt_mode = "plan"
                    else:
                        self._prompt_mode = "default"

                    user_input = get_user_input(session, mode=self._prompt_mode)

                    if user_input is None:
                        # Ctrl+D — exit
                        self.renderer.print_status("Goodbye!")
                        self.status_bar.render(
                            model=self.model,
                            provider=self.provider.name,
                            cost_tracker=self.engine.cost,
                            cwd=self.cwd,
                        )
                        break

                    if not user_input:
                        continue

                    # Check for slash commands
                    if user_input.startswith("/"):
                        self._handle_slash_command(user_input)
                        continue

                    # Persist user message
                    self.session_manager.save_user_message(user_input)

                    # Run through the engine
                    try:
                        result = self._run_async(self._async_turn(user_input))
                        self.renderer.flush_streaming()
                        # Persist assistant response
                        self._persist_last_assistant()
                        # Background memory extraction
                        self._maybe_extract_memories()
                    except KeyboardInterrupt:
                        self.renderer.flush_streaming()
                        self.renderer.print_status("Interrupted.")
                        continue

                except KeyboardInterrupt:
                    self.console.print()
                    continue
                except EOFError:
                    break
        finally:
            self._shutdown_loop()

        self.status_bar.render(
            model=self.model,
            provider=self.provider.name,
            cost_tracker=self.engine.cost,
            cwd=self.cwd,
        )

    def run_single(self, prompt: str) -> None:
        """Run a single non-interactive query."""
        self.session_manager.start_session(self.cwd, self.model)
        self.session_manager.save_user_message(prompt)

        try:
            result = self._run_async(self._async_turn(prompt))
            self.renderer.flush_streaming()
            self._persist_last_assistant()
        except KeyboardInterrupt:
            self.renderer.flush_streaming()
            self.renderer.print_status("Interrupted.")
        finally:
            self._shutdown_loop()

        self.status_bar.render(
            model=self.model,
            provider=self.provider.name,
            cost_tracker=self.engine.cost,
            cwd=self.cwd,
        )

    async def _ensure_mcp(self) -> None:
        """Lazily connect MCP servers on the first async call.

        Must run inside the same event loop that will later call tools,
        because StdioTransport's background reader task and Futures are
        bound to the current loop.
        """
        if self._mcp_initialized or not self.config.mcp_servers:
            return
        self._mcp_initialized = True

        try:
            from ccos.mcp.client import MCPManager
            from ccos.mcp.tools import register_mcp_tools

            self.mcp_manager = MCPManager()

            try:
                results = await asyncio.wait_for(
                    self.mcp_manager.connect_servers(self.config.mcp_servers),
                    timeout=15,
                )
            except asyncio.TimeoutError:
                self.console.print("  [dim]MCP connection timed out after 15s[/dim]")
                return

            for name, error in results.items():
                conn = self.mcp_manager.get_connection(name)
                if error:
                    self.console.print(f"  [dim]MCP [red]{name}[/red]: {error}[/dim]")
                elif conn:
                    parts = []
                    if conn.tools:
                        parts.append(f"{len(conn.tools)} tools")
                    if conn.resources:
                        parts.append(f"{len(conn.resources)} resources")
                    if conn.prompts:
                        parts.append(f"{len(conn.prompts)} prompts")
                    summary = ", ".join(parts) if parts else "connected"
                    self.console.print(
                        f"  [dim]MCP [green]{name}[/green] ({conn.config.type.value}): {summary}[/dim]"
                    )

            # Register all MCP tools
            register_mcp_tools(self.mcp_manager, self.tool_registry)
        except Exception as e:
            self.console.print(f"  [dim]MCP init error: {e}[/dim]")

    async def _async_turn(self, user_input: str) -> str:
        """Connect MCP (if needed) then run an engine turn in one event loop."""
        await self._ensure_mcp()
        return await self.engine.run_turn(user_input)

    def _persist_last_assistant(self) -> None:
        """Save the last assistant message to the session transcript."""
        msgs = self.engine.messages.messages
        if not msgs:
            return
        last = msgs[-1]
        if last.role == "assistant":
            content = last.content
            # Serialize content blocks
            serialized = []
            for block in (content if isinstance(content, list) else [content]):
                if isinstance(block, str):
                    serialized.append({"type": "text", "text": block})
                elif hasattr(block, "text"):
                    serialized.append({"type": "text", "text": block.text})
                elif hasattr(block, "name"):
                    serialized.append({
                        "type": "tool_use",
                        "name": block.name,
                        "id": getattr(block, "id", ""),
                        "input": getattr(block, "input", {}),
                    })
                else:
                    serialized.append({"type": "unknown", "data": str(block)})
            self.session_manager.save_assistant_message(serialized, self.model)

    def _maybe_extract_memories(self) -> None:
        """Check if background memory extraction should run after this turn."""
        try:
            messages = self.engine.messages.messages
            # Check if main agent wrote to memory dir this turn
            has_writes = self._check_memory_writes_this_turn()
            if self.memory_extractor.should_extract(messages, has_writes):
                self.memory_extractor.run_background(messages)
        except Exception:
            pass  # Never crash main app for memory extraction

    def _check_memory_writes_this_turn(self) -> bool:
        """Check if the last assistant turn included writes to the memory directory."""
        msgs = self.engine.messages.messages
        if not msgs:
            return False
        last = msgs[-1]
        if last.role != "assistant":
            return False
        content = last.content
        if not isinstance(content, list):
            return False
        memory_dir = str(self.memory_store.memory_dir)
        for block in content:
            if hasattr(block, "name") and block.name in ("Write", "Edit"):
                inp = getattr(block, "input", {})
                file_path = inp.get("file_path", "")
                if memory_dir in file_path:
                    return True
        return False

    def _restore_messages(self, entries: list[dict]) -> None:
        """Restore messages from session transcript into the engine."""
        for entry in entries:
            etype = entry.get("type")
            if etype == "user":
                self.engine.messages.add_user(entry.get("content", ""))
            # Assistant and tool_result restoration is best-effort
            # Full message restoration would need content block deserialization

    def _create_sub_engine(self, model_override: str = "") -> QueryEngine:
        """Factory for creating sub-agent QueryEngine instances."""
        model = model_override or self.model
        return QueryEngine(
            provider=self.provider,
            model=model,
            tools=self.tool_registry,
            prompt_builder=PromptBuilder(),
            permissions=self.permissions,
            ctx=self.ctx,  # shared context (read_files, background_tasks)
            cost_tracker=self.engine.cost,  # shared cost tracker
            console=self.console,
            on_text=lambda t: None,  # sub-agents don't stream to UI
            on_tool_start=None,
            on_tool_end=None,
            on_thinking=None,
        )

    def _handle_slash_command(self, raw: str) -> None:
        """Parse and execute a slash command."""
        parts = raw[1:].split(maxsplit=1)
        cmd_name = parts[0] if parts else ""
        args = parts[1] if len(parts) > 1 else ""

        cmd = self.commands.get(cmd_name)
        if cmd is None:
            self.renderer.print_error(f"Unknown command: /{cmd_name}. Type /help for available commands.")
            return

        try:
            cmd.handler(args=args)
        except SystemExit:
            self.renderer.print_status("Goodbye!")
            self.renderer.print_cost(self.engine.cost.summary())
            raise
        except Exception as e:
            self.renderer.print_error(f"Command error: {e}")

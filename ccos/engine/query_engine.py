"""Core agentic loop — send messages to LLM, execute tools, iterate."""

from __future__ import annotations

import asyncio
import time
from typing import Any, AsyncIterator, Callable

from rich.console import Console

from ccos.engine.cost_tracker import CostTracker
from ccos.engine.message_manager import MessageManager
from ccos.engine.tool_executor import execute_tool_calls
from ccos.hooks import HookManager
from ccos.permissions.manager import PermissionManager
from ccos.prompt.builder import PromptBuilder
from ccos.providers.base import (
    ChunkType,
    LLMProvider,
    LLMResponse,
    StreamChunk,
    TextContent,
    ThinkingConfig,
    ThinkingContent,
    ToolCallContent,
    ToolSchema,
)
from ccos.tools.base import ToolContext, ToolRegistry

_MAX_TOOL_TURNS = 50  # Safety limit to prevent infinite loops
_MAX_RETRIES = 3      # Max API retries on transient errors
_RETRY_DELAYS = [1, 2, 4]  # Exponential backoff seconds

# Error types that are retryable
_RETRYABLE_ERRORS = (
    "overloaded",
    "rate_limit",
    "529",
    "500",
    "502",
    "503",
    "timeout",
    "connection",
    "APIConnectionError",
    "InternalServerError",
    "RateLimitError",
    "APIStatusError",
)


class QueryEngine:
    """The core agentic conversation loop."""

    def __init__(
        self,
        *,
        provider: LLMProvider,
        model: str,
        tools: ToolRegistry,
        prompt_builder: PromptBuilder,
        permissions: PermissionManager,
        ctx: ToolContext,
        cost_tracker: CostTracker | None = None,
        console: Console | None = None,
        thinking: ThinkingConfig | None = None,
        on_text: Callable[[str], None] | None = None,
        on_tool_start: Callable[[ToolCallContent], None] | None = None,
        on_tool_end: Callable[[str, str, bool], None] | None = None,
        on_thinking: Callable[[str], None] | None = None,
        hooks: HookManager | None = None,
        skill_registry: Any = None,
        co_author: str = "",
    ):
        self.provider = provider
        self.model = model
        self.tools = tools
        self.prompt_builder = prompt_builder
        self.permissions = permissions
        self.ctx = ctx
        self.cost = cost_tracker or CostTracker()
        self.messages = MessageManager()
        self.console = console or Console()
        self.thinking = thinking
        self.hooks = hooks
        self.skill_registry = skill_registry
        self.co_author = co_author
        # Callbacks for UI
        self._on_text = on_text
        self._on_tool_start = on_tool_start
        self._on_tool_end = on_tool_end
        self._on_thinking = on_thinking

    async def run_turn(self, user_input: str) -> str:
        """Process a single user turn through the full agentic loop.

        Returns the final text response.
        """
        self.messages.add_user(user_input)
        final_text = ""
        turn = 0

        while turn < _MAX_TOOL_TURNS:
            turn += 1

            # Check if compaction is needed
            if self.messages.needs_compaction():
                self._auto_compact()

            # Build system prompt
            model_skills = None
            if self.skill_registry is not None:
                try:
                    model_skills = self.skill_registry.get_model_invocable()
                except Exception:
                    pass
            system = self.prompt_builder.build(
                tools=self.tools.get_all(),
                model=self.model,
                cwd=self.ctx.cwd,
                provider_name=self.provider.name,
                co_author=self.co_author,
                skills=model_skills,
            )

            # Get tool schemas
            tool_schemas = [
                ToolSchema(
                    name=t.name,
                    description=t.description,
                    input_schema=t.input_schema,
                )
                for t in self.tools.get_all()
            ]

            # Stream response from LLM (with retry)
            response = await self._stream_with_retry(
                system=system,
                tool_schemas=tool_schemas,
            )

            # Record cost
            self.cost.record(
                model=self.model,
                input_tokens=response.input_tokens,
                output_tokens=response.output_tokens,
                cache_read_tokens=response.cache_read_tokens,
                cache_creation_tokens=response.cache_creation_tokens,
            )

            # Add to history
            self.messages.add_assistant_response(response)

            # Get text from response
            final_text = response.get_text()

            # Check for tool calls
            tool_calls = response.get_tool_calls()
            if not tool_calls:
                break  # end_turn — no more tools to run

            # Notify UI about tool calls
            for tc in tool_calls:
                if self._on_tool_start:
                    self._on_tool_start(tc)

            # Execute tools
            results = await execute_tool_calls(
                tool_calls=tool_calls,
                registry=self.tools,
                ctx=self.ctx,
                permissions=self.permissions,
                hooks=self.hooks,
            )

            # Notify UI about results
            for tc, result in zip(tool_calls, results):
                if self._on_tool_end:
                    content = result.content if isinstance(result.content, str) else str(result.content)
                    self._on_tool_end(tc.name, content, result.is_error)

            # Add results and continue loop
            self.messages.add_tool_results(results)

        return final_text

    async def _stream_with_retry(
        self,
        system: str,
        tool_schemas: list[ToolSchema],
    ) -> LLMResponse:
        """Stream a response with retry logic for transient errors."""
        last_error: Exception | None = None

        for attempt in range(_MAX_RETRIES):
            try:
                response = await self._stream_response(
                    system=system,
                    tool_schemas=tool_schemas,
                )
                # Check if we got an error response that's retryable
                if response.stop_reason == "error":
                    text = response.get_text()
                    if any(err in text.lower() for err in ("overloaded", "rate_limit", "529")):
                        if attempt < _MAX_RETRIES - 1:
                            delay = _RETRY_DELAYS[min(attempt, len(_RETRY_DELAYS) - 1)]
                            if self._on_text:
                                self._on_text(f"\n[Retrying in {delay}s...]\n")
                            await asyncio.sleep(delay)
                            continue
                return response

            except Exception as e:
                last_error = e
                error_str = str(e).lower()
                is_retryable = any(err.lower() in error_str for err in _RETRYABLE_ERRORS)

                if is_retryable and attempt < _MAX_RETRIES - 1:
                    delay = _RETRY_DELAYS[min(attempt, len(_RETRY_DELAYS) - 1)]
                    if self._on_text:
                        self._on_text(f"\n[API error: {e}. Retrying in {delay}s...]\n")
                    await asyncio.sleep(delay)
                    continue
                else:
                    # Non-retryable or final attempt
                    response = LLMResponse()
                    response.content.append(TextContent(text=f"\n\n[API Error: {e}]"))
                    response.stop_reason = "error"
                    return response

        # Should not reach here, but just in case
        response = LLMResponse()
        response.content.append(TextContent(text=f"\n\n[API Error after {_MAX_RETRIES} retries: {last_error}]"))
        response.stop_reason = "error"
        return response

    async def _stream_response(
        self,
        system: str,
        tool_schemas: list[ToolSchema],
    ) -> LLMResponse:
        """Stream a response, calling callbacks for text/tool chunks."""
        response = LLMResponse()
        current_text = ""
        current_tool: ToolCallContent | None = None

        stream = self.provider.stream(
            messages=self.messages.to_api_format(),
            system=system,
            tools=tool_schemas if tool_schemas else None,
            model=self.model,
            max_tokens=16384,
            thinking=self.thinking,
        )

        async for chunk in stream:
            if chunk.type == ChunkType.TEXT:
                if chunk.text:
                    current_text += chunk.text
                    if self._on_text:
                        self._on_text(chunk.text)

            elif chunk.type == ChunkType.THINKING:
                if chunk.text and self._on_thinking:
                    self._on_thinking(chunk.text)

            elif chunk.type == ChunkType.TOOL_CALL_START:
                current_tool = chunk.tool_call

            elif chunk.type == ChunkType.TOOL_CALL_END:
                if chunk.tool_call:
                    response.content.append(chunk.tool_call)

            elif chunk.type == ChunkType.DONE:
                response.stop_reason = chunk.stop_reason or "end_turn"
                response.input_tokens = chunk.input_tokens
                response.output_tokens = chunk.output_tokens
                response.cache_read_tokens = chunk.cache_read_tokens
                response.cache_creation_tokens = chunk.cache_creation_tokens

            elif chunk.type == ChunkType.ERROR:
                current_text += f"\n\n[Error: {chunk.text}]"

        # Add accumulated text as content block
        if current_text:
            response.content.insert(0, TextContent(text=current_text))

        return response

    def _auto_compact(self) -> None:
        """Automatically compact messages when approaching context limit."""
        # Simple compaction: remove old turns, keep recent
        msgs = self.messages.messages
        if len(msgs) <= 8:
            return

        # Keep last 6 messages
        old_count = len(msgs) - 6
        summary_parts = []
        for msg in msgs[:old_count]:
            if isinstance(msg.content, str):
                summary_parts.append(msg.content[:200])
            elif isinstance(msg.content, list):
                for block in msg.content:
                    if isinstance(block, TextContent):
                        summary_parts.append(block.text[:200])

        summary = "[Auto-compacted earlier conversation]\n" + "\n".join(summary_parts[:10])
        self.messages.compact(summary)

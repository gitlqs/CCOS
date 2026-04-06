"""Core agentic loop — send messages to LLM, execute tools, iterate."""

from __future__ import annotations

import asyncio
import logging
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
    Message,
    StreamChunk,
    TextContent,
    ThinkingConfig,
    ThinkingContent,
    ToolCallContent,
    ToolSchema,
)
from ccos.tools.base import ToolContext, ToolRegistry

log = logging.getLogger(__name__)

_MAX_TOOL_TURNS = 50  # Safety limit to prevent infinite loops
_MAX_RETRIES = 3      # Max API retries on transient errors
_RETRY_DELAYS = [1, 2, 4]  # Exponential backoff seconds

# Output token budgets.
# For reasoning models (o-series, GPT-5), max_completion_tokens includes
# reasoning tokens, so we need a much higher budget to leave room for
# actual output + tool calls after internal reasoning.
_DEFAULT_MAX_TOKENS = 16_384           # Non-reasoning models (GPT-4o etc.)
_REASONING_MAX_TOKENS = 65_536         # Reasoning models (o-series, GPT-5)
_RESPONSES_MAX_TOKENS = 65_536         # Responses API models (Codex)

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
        flush_streaming: Callable[[], None] | None = None,
        hooks: HookManager | None = None,
        skill_registry: Any = None,
        co_author: str = "",
        on_text_complete: Callable[[str], None] | None = None,
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
        self._flush_streaming = flush_streaming
        self._on_text_complete = on_text_complete

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
            # Collect deferred tool names from ToolSearch
            deferred_names: list[str] = []
            from ccos.tools.tool_search import ToolSearchTool
            _ts = self.tools.get("ToolSearch")
            if isinstance(_ts, ToolSearchTool):
                deferred_names = _ts.deferred_names

            system = self.prompt_builder.build(
                tools=self.tools.get_all(),
                model=self.model,
                cwd=self.ctx.cwd,
                provider_name=self.provider.name,
                co_author=self.co_author,
                skills=model_skills,
                deferred_tool_names=deferred_names,
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

            # Print complete markdown text for this turn
            if final_text.strip() and self._on_text_complete:
                self._on_text_complete(final_text)

            # Check for tool calls
            tool_calls = response.get_tool_calls()
            content_types = [type(c).__name__ for c in response.content]

            log.debug(
                "turn=%d model=%s stop=%s in=%d out=%d tool_calls=%d text_len=%d content=%s",
                turn, self.model, response.stop_reason,
                response.input_tokens, response.output_tokens,
                len(tool_calls), len(final_text), content_types,
            )

            if not tool_calls:
                # If truncated (finish_reason=length), warn the user
                if response.stop_reason == "length":
                    trunc_msg = "\n[Response truncated — output token limit reached. Continuing...]\n"
                    if self._on_text:
                        self._on_text(trunc_msg)
                    final_text += trunc_msg
                    # Don't break — add to history and let the model continue
                    # so it can re-attempt the tool call
                    self.messages.messages[-1] = Message(
                        role="assistant", content=[TextContent(text=final_text)]
                    )
                    self.messages.add_user(
                        "Your previous response was truncated due to the output token limit. "
                        "Please continue from where you left off. If you were about to call "
                        "a tool, go ahead and call it now."
                    )
                    continue
                break  # end_turn — no more tools to run

            # Flush streaming text before showing tool call UI
            if hasattr(self, '_flush_streaming') and self._flush_streaming:
                self._flush_streaming()

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
                    error_msg = f"\n\n[API Error: {e}]"
                    if self._on_text:
                        self._on_text(error_msg)
                    response = LLMResponse()
                    response.content.append(TextContent(text=error_msg))
                    response.stop_reason = "error"
                    return response

        # Should not reach here, but just in case
        error_msg = f"\n\n[API Error after {_MAX_RETRIES} retries: {last_error}]"
        if self._on_text:
            self._on_text(error_msg)
        response = LLMResponse()
        response.content.append(TextContent(text=error_msg))
        response.stop_reason = "error"
        return response

    def _get_max_tokens(self) -> int:
        """Pick the output token budget based on the current model."""
        from ccos.providers.openai_compat import _is_reasoning_model, _is_responses_model
        model = self.model
        try:
            if _is_responses_model(model):
                return _RESPONSES_MAX_TOKENS
            if _is_reasoning_model(model):
                return _REASONING_MAX_TOKENS
        except Exception:
            pass
        return _DEFAULT_MAX_TOKENS

    async def _stream_response(
        self,
        system: str,
        tool_schemas: list[ToolSchema],
    ) -> LLMResponse:
        """Stream a response, calling callbacks for text/tool chunks."""
        response = LLMResponse()
        current_text = ""
        current_tool: ToolCallContent | None = None

        max_tokens = self._get_max_tokens()

        stream = self.provider.stream(
            messages=self.messages.to_api_format(),
            system=system,
            tools=tool_schemas if tool_schemas else None,
            model=self.model,
            max_tokens=max_tokens,
            thinking=self.thinking,
        )

        try:
            async for chunk in stream:
                if chunk.type == ChunkType.TEXT:
                    if chunk.text:
                        current_text += chunk.text

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
                    error_text = f"\n\n[API Error: {chunk.text}]"
                    current_text += error_text
                    if self._on_text:
                        self._on_text(error_text)
        finally:
            # Ensure the async generator is properly closed to avoid
            # "Task was destroyed but it is pending!" warnings.
            # This happens when the generator's underlying HTTP stream
            # hasn't been fully consumed (e.g., after permission deny).
            if hasattr(stream, 'aclose'):
                try:
                    await stream.aclose()
                except Exception:
                    pass

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

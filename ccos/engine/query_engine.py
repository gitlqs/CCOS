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

# cc imposes no main-loop turn cap (query.ts only checks maxTurns when it is
# explicitly provided); the subagent default is 200 (forkSubagent.ts). We keep a
# very high internal safety ceiling to guard against a runaway loop, but normal
# sessions are effectively uncapped.
_SUBAGENT_MAX_TURNS = 200  # cc forkSubagent default
_SAFETY_MAX_TURNS = 1000   # Internal ceiling, far above cc's old 50
_MAX_RETRIES = 3      # Max API retries on transient errors
_RETRY_DELAYS = [1, 2, 4]  # Exponential backoff seconds

# Output-token truncation recovery. cc retries up to
# MAX_OUTPUT_TOKENS_RECOVERY_LIMIT (3) times before surfacing the error.
_MAX_OUTPUT_TOKENS_RECOVERY_LIMIT = 3
# cc's exact recovery message (query.ts), injected as a hidden meta user turn.
_OUTPUT_TOKEN_RECOVERY_MESSAGE = (
    "Output token limit hit. Resume directly — no apology, no recap of what you "
    "were doing. Pick up mid-thought if that is where the cut happened. Break "
    "remaining work into smaller pieces."
)

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
        mcp_instructions_provider: Callable[[], str | None] | None = None,
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
        # Provider for the live "# MCP Server Instructions" prompt section
        # (evaluated each turn so newly-connected servers are picked up).
        self._mcp_instructions_provider = mcp_instructions_provider
        # Callbacks for UI
        self._on_text = on_text
        self._on_tool_start = on_tool_start
        self._on_tool_end = on_tool_end
        self._on_thinking = on_thinking
        self._flush_streaming = flush_streaming
        self._on_text_complete = on_text_complete

    def _parse_user_input_for_images(self, text: str) -> list[Any] | str:
        import base64
        import mimetypes
        import os
        import re
        from ccos.providers.base import ImageContent

        # Match absolute paths that end with common image extensions
        pattern = r'(?P<path>(?:[a-zA-Z]:[\\/]|/)[^\s\'"<>|]+\.(?:png|jpe?g|gif|webp|bmp))'
        matches = list(re.finditer(pattern, text, re.IGNORECASE))
        if not matches:
            return text

        blocks = []
        last_idx = 0
        for match in matches:
            path = match.group("path")
            if os.path.exists(path) and os.path.isfile(path):
                if match.start() > last_idx:
                    blocks.append(TextContent(text=text[last_idx:match.start()]))
                try:
                    with open(path, "rb") as f:
                        data = base64.b64encode(f.read()).decode("utf-8")
                    mime = mimetypes.guess_type(path)[0] or "image/png"
                    # Include the path text for context
                    blocks.append(TextContent(text=f"[Attached Image: {os.path.basename(path)}]"))
                    blocks.append(ImageContent(
                        source_type="base64",
                        media_type=mime,
                        data=data
                    ))
                except Exception:
                    blocks.append(TextContent(text=text[match.start():match.end()]))
                last_idx = match.end()
            else:
                blocks.append(TextContent(text=text[last_idx:match.end()]))
                last_idx = match.end()

        if last_idx < len(text):
            blocks.append(TextContent(text=text[last_idx:]))

        return blocks if len(blocks) > 0 else text

    async def run_turn(self, user_input: str, max_turns: int | None = None) -> str:
        """Process a single user turn through the full agentic loop.

        Args:
            user_input: The user's message.
            max_turns: Optional cap on the number of model turns. ``None`` (the
                default for the main loop) means no cap, matching cc's REPL path
                which passes ``undefined``. Subagents pass 200.

        Returns the final text response.
        """
        parsed_input = self._parse_user_input_for_images(user_input)
        self.messages.add_user(parsed_input)
        final_text = ""
        turn = 0
        # Per-turn counter mirroring cc's MAX_OUTPUT_TOKENS_RECOVERY_LIMIT.
        output_token_recovery_count = 0

        while turn < _SAFETY_MAX_TURNS:
            turn += 1

            # Honor an explicit cap (cc only enforces maxTurns when provided).
            if max_turns is not None and turn > max_turns:
                limit_msg = f"Reached maximum number of turns ({max_turns})"
                if self._on_text:
                    self._on_text(f"\n[{limit_msg}]\n")
                final_text += f"\n[{limit_msg}]\n"
                break

            # Hard blocking limit: if the context is too large to send safely,
            # surface a clean prompt-too-long error instead of issuing an
            # oversized request that the provider would reject with a 400.
            if self.messages.is_at_blocking_limit(self.model):
                await self._auto_compact()
                if self.messages.is_at_blocking_limit(self.model):
                    block_msg = (
                        "\n[Prompt is too long — the conversation exceeds the "
                        "model's context window even after compaction. Start a "
                        "new conversation or remove some context to continue.]\n"
                    )
                    if self._on_text:
                        self._on_text(block_msg)
                    final_text += block_msg
                    break

            # Check if compaction is needed (model-aware threshold)
            if self.messages.needs_compaction(self.model):
                await self._auto_compact()

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

            mcp_instructions = None
            if self._mcp_instructions_provider is not None:
                try:
                    mcp_instructions = self._mcp_instructions_provider()
                except Exception:
                    mcp_instructions = None

            system = self.prompt_builder.build(
                tools=self.tools.get_all(),
                model=self.model,
                cwd=self.ctx.cwd,
                provider_name=self.provider.name,
                co_author=self.co_author,
                skills=model_skills,
                deferred_tool_names=deferred_names,
                mcp_instructions=mcp_instructions,
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
                # Output-token truncation. The primary Anthropic provider emits
                # "max_tokens"; OpenAI-compatible providers map to "length". Be
                # provider-agnostic and key on both.
                if response.stop_reason in ("length", "max_tokens"):
                    if output_token_recovery_count < _MAX_OUTPUT_TOKENS_RECOVERY_LIMIT:
                        output_token_recovery_count += 1
                        if self._on_text:
                            self._on_text(
                                "\n[Response truncated — output token limit "
                                "reached. Continuing...]\n"
                            )
                        # Inject cc's exact recovery prompt as a hidden meta
                        # user turn (isMeta-equivalent: not added to final_text)
                        # and let the model resume mid-thought.
                        self.messages.add_user(_OUTPUT_TOKEN_RECOVERY_MESSAGE)
                        continue
                    # Recovery exhausted — surface the error and stop.
                    exhausted = (
                        "\n[Output token limit hit repeatedly; recovery "
                        f"exhausted after {_MAX_OUTPUT_TOKENS_RECOVERY_LIMIT} "
                        "attempts.]\n"
                    )
                    if self._on_text:
                        self._on_text(exhausted)
                    final_text += exhausted
                    break
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
        # Accumulate thinking text + signature across the stream so the block
        # round-trips into assistant history (required for interleaved-thinking
        # + tool-use trajectories on Anthropic models — see the rules of
        # thinking in cc/src/query.ts).
        current_thinking = ""
        current_thinking_signature = ""

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
                    if chunk.text:
                        current_thinking += chunk.text
                        if self._on_thinking:
                            self._on_thinking(chunk.text)
                    # The signature arrives separately (on a delta/stop) once the
                    # provider plumbs it through StreamChunk; capture it whenever
                    # present so the preserved block is valid for replay.
                    sig = getattr(chunk, "signature", "") or ""
                    if sig:
                        current_thinking_signature = sig

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

        # Preserve the thinking block at the front of the assistant content so
        # it is stored by add_assistant_response and replayed on subsequent
        # iterations of the same trajectory. The Anthropic API rejects a
        # thinking block without a valid signature, so only round-trip it once a
        # signature is available; otherwise it has already been streamed to the
        # UI and we simply omit it from history (matching today's behavior until
        # the provider plumbs the signature through StreamChunk).
        if current_thinking and current_thinking_signature:
            response.content.insert(
                0,
                ThinkingContent(
                    thinking=current_thinking,
                    signature=current_thinking_signature,
                ),
            )

        return response

    async def _auto_compact(self) -> None:
        """Compact the conversation via a real LLM summarization.

        Mirrors cc's compactConversation: ask the model to summarize the
        conversation so far, then replace the older turns with that
        model-generated summary (rather than a char-slice placeholder).
        Falls back to a simple text-slice summary only if the summarization
        call fails, so compaction still makes progress.
        """
        msgs = self.messages.messages
        if len(msgs) <= 8:
            return

        summary: str | None = None
        try:
            summary = await self._summarize_for_compaction()
        except Exception as e:  # noqa: BLE001 — never let compaction crash a turn
            log.debug("LLM compaction summarization failed: %s", e)

        if not summary or not summary.strip():
            summary = self._fallback_compaction_summary(msgs)

        self.messages.compact(summary)

    async def _summarize_for_compaction(self) -> str:
        """Run a forked, tool-free LLM call to summarize the conversation."""
        prompt = self.messages.get_compact_prompt()
        request = [Message(role="user", content=prompt)]

        stream = self.provider.stream(
            messages=request,
            system="You are a helpful assistant that summarizes conversations.",
            tools=None,
            model=self.model,
            max_tokens=self._get_max_tokens(),
            thinking=None,
        )

        parts: list[str] = []
        try:
            async for chunk in stream:
                if chunk.type == ChunkType.TEXT and chunk.text:
                    parts.append(chunk.text)
        finally:
            if hasattr(stream, "aclose"):
                try:
                    await stream.aclose()
                except Exception:
                    pass

        return "".join(parts).strip()

    @staticmethod
    def _fallback_compaction_summary(msgs: list[Message]) -> str:
        """Char-slice fallback used only when LLM summarization is unavailable."""
        old_count = max(len(msgs) - 6, 0)
        summary_parts: list[str] = []
        for msg in msgs[:old_count]:
            if isinstance(msg.content, str):
                summary_parts.append(msg.content[:200])
            elif isinstance(msg.content, list):
                for block in msg.content:
                    if isinstance(block, TextContent):
                        summary_parts.append(block.text[:200])
        return "[Auto-compacted earlier conversation]\n" + "\n".join(summary_parts[:10])

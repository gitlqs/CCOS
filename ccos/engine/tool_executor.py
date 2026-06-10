"""Tool execution dispatcher — handles parallel/serial execution and permissions."""

from __future__ import annotations

import asyncio
import os
from typing import Any

from ccos.hooks import HookManager
from ccos.permissions.manager import PermissionManager
from ccos.permissions.prompts import ask_permission
from ccos.providers.base import TextContent, ToolCallContent, ToolResultContent
from ccos.tools.base import PermissionDecision, Tool, ToolContext, ToolOutput, ToolRegistry

# ── Canonical tool_result messages (verbatim from cc/src/utils/messages.ts) ──
# These exact strings steer the model's behavior after a denial, so they must
# match cc word-for-word.
CANCEL_MESSAGE = (
    "The user doesn't want to take this action right now. STOP what you are "
    "doing and wait for the user to tell you how to proceed."
)
REJECT_MESSAGE = (
    "The user doesn't want to proceed with this tool use. The tool use was "
    "rejected (eg. if it was a file edit, the new_string was NOT written to the "
    "file). STOP what you are doing and wait for the user to tell you how to "
    "proceed."
)
REJECT_MESSAGE_WITH_REASON_PREFIX = (
    "The user doesn't want to proceed with this tool use. The tool use was "
    "rejected (eg. if it was a file edit, the new_string was NOT written to the "
    "file). To tell you how to proceed, the user said:\n"
)
SUBAGENT_REJECT_MESSAGE = (
    "Permission for this tool use was denied. The tool use was rejected (eg. if "
    "it was a file edit, the new_string was NOT written to the file). Try a "
    "different approach or report the limitation to complete your task."
)
DENIAL_WORKAROUND_GUIDANCE = (
    "IMPORTANT: You *may* attempt to accomplish this action using other tools "
    "that might naturally be used to accomplish this goal, e.g. using head "
    "instead of cat. But you *should not* attempt to work around this denial in "
    "malicious ways, e.g. do not use your ability to run tests to execute "
    "non-test actions. You should only try to work around this restriction in "
    "reasonable ways that do not attempt to bypass the intent behind this "
    "denial. If you believe this capability is essential to complete the user's "
    "request, STOP and explain to the user what you were trying to do and why "
    "you need this permission. Let the user decide how to proceed."
)


def AUTO_REJECT_MESSAGE(tool_name: str) -> str:
    """Policy/rule denial message (cc: AUTO_REJECT_MESSAGE)."""
    return f"Permission to use {tool_name} has been denied. {DENIAL_WORKAROUND_GUIDANCE}"


# Max number of read-only tools to run concurrently (cc default
# CLAUDE_CODE_MAX_TOOL_USE_CONCURRENCY = 10).
def _max_tool_use_concurrency() -> int:
    raw = os.environ.get("CLAUDE_CODE_MAX_TOOL_USE_CONCURRENCY", "")
    try:
        parsed = int(raw)
        if parsed > 0:
            return parsed
    except (TypeError, ValueError):
        pass
    return 10


def _is_concurrency_safe(tool: Tool, params: dict[str, Any]) -> bool:
    """Determine whether a tool call may run concurrently with its neighbours.

    Mirrors cc's partitionToolCalls guard: validate the input first, then call
    the tool's concurrency-safe predicate, defaulting to NOT-safe on any
    validation failure or exception. ``is_concurrency_safe`` is a distinct
    concept from read-only; tools may opt out of parallelism even when
    read-only. Falls back to ``is_read_only`` when a tool defines no explicit
    ``is_concurrency_safe`` method.
    """
    # Conservative input validation: a missing required key or a non-dict input
    # means we cannot trust the predicate, so treat as not-safe (serial).
    if not isinstance(params, dict):
        return False
    try:
        predicate = getattr(tool, "is_concurrency_safe", None)
        if callable(predicate):
            return bool(predicate(params))
        return bool(tool.is_read_only(params))
    except Exception:
        # If the predicate raises (e.g. malformed input), be conservative.
        return False


async def execute_tool_calls(
    tool_calls: list[ToolCallContent],
    registry: ToolRegistry,
    ctx: ToolContext,
    permissions: PermissionManager,
    hooks: HookManager | None = None,
) -> list[ToolResultContent]:
    """Execute a batch of tool calls, preserving the model's emitted order.

    Mirrors cc's toolOrchestration.runTools: partition the calls into
    consecutive batches that are EITHER a single non-concurrency-safe tool OR a
    run of consecutive concurrency-safe tools. Each batch runs (concurrent
    inside the batch, capped at the max concurrency), but the batches run in
    sequence, so a write between two reads forces three batches rather than a
    global reorder. Results are returned 1:1 with ``tool_calls``.
    """
    # Resolve each call's tool once and detect unknown tools up front.
    resolved: list[tuple[ToolCallContent, Tool | None]] = []
    for tc in tool_calls:
        resolved.append((tc, registry.get(tc.name)))

    # Build batches of (is_concurrency_safe, [tool_calls]) preserving order.
    batches: list[tuple[bool, list[ToolCallContent]]] = []
    for tc, tool in resolved:
        # Unknown tools, and any non-concurrency-safe tool, get their own serial
        # batch (matching cc's conservative fallback on validation failure).
        safe = tool is not None and _is_concurrency_safe(tool, tc.input)
        if safe and batches and batches[-1][0]:
            batches[-1][1].append(tc)
        else:
            batches.append((safe, [tc]))

    results: list[ToolResultContent] = []
    max_concurrency = _max_tool_use_concurrency()

    for is_safe, batch in batches:
        if is_safe and len(batch) > 1:
            # Run the concurrency-safe run together, capped at max_concurrency.
            sem = asyncio.Semaphore(max_concurrency)

            async def _run(tc: ToolCallContent) -> ToolResultContent:
                async with sem:
                    return await _execute_single(tc, registry, ctx, permissions, hooks)

            batch_results = await asyncio.gather(*[_run(tc) for tc in batch])
            results.extend(batch_results)
        else:
            # Single tool (concurrency-safe-singleton or non-safe) runs alone.
            for tc in batch:
                results.append(
                    await _execute_single(tc, registry, ctx, permissions, hooks)
                )

    return results


async def _execute_single(
    tc: ToolCallContent,
    registry: ToolRegistry,
    ctx: ToolContext,
    permissions: PermissionManager,
    hooks: HookManager | None = None,
) -> ToolResultContent:
    """Execute a single tool call with permission checking and hooks."""
    tool = registry.get(tc.name)
    if tool is None:
        return ToolResultContent(
            tool_use_id=tc.id,
            content=f"Error: Unknown tool '{tc.name}'",
            is_error=True,
        )

    # ── PreToolUse hook ──────────────────────────────────────────
    if hooks and hooks.has_event("PreToolUse"):
        hr = hooks.run_hooks(
            "PreToolUse",
            tool_name=tc.name,
            tool_input=tc.input,
        )
        if not hr.continue_execution:
            return ToolResultContent(
                tool_use_id=tc.id,
                content=f"Blocked by hook: {hr.reason or hr.error}",
                is_error=True,
            )
        # Allow hook to modify tool input
        if hr.updated_input is not None:
            tc = ToolCallContent(
                id=tc.id, name=tc.name, input=hr.updated_input,
            )

    # Permission check
    perm = permissions.check(tool, tc.input, ctx)

    if perm.decision == PermissionDecision.DENY:
        # Policy/rule denial — use cc's AUTO_REJECT_MESSAGE wording so the model
        # gets the canonical workaround guidance rather than a paraphrase.
        return ToolResultContent(
            tool_use_id=tc.id,
            content=AUTO_REJECT_MESSAGE(tool.name),
            is_error=True,
        )

    if perm.decision == PermissionDecision.ASK:
        choice = ask_permission(tool, tc.input)
        if choice == "no":
            return ToolResultContent(
                tool_use_id=tc.id,
                content=REJECT_MESSAGE,
                is_error=True,
            )
        elif choice == "always":
            # Remember for this session, scoped to tool + relevant param
            _pattern = _extract_allow_pattern(tc)
            permissions.add_session_allow(tool.name, _pattern)
        elif choice == "deny_always":
            permissions.add_always_deny(tool.name, "*")
            return ToolResultContent(
                tool_use_id=tc.id,
                content=REJECT_MESSAGE,
                is_error=True,
            )

    # Execute with error handling
    try:
        output = await tool.execute(tc.input, ctx)
    except KeyboardInterrupt:
        return ToolResultContent(
            tool_use_id=tc.id,
            content="Tool execution interrupted by user.",
            is_error=True,
        )
    except Exception as e:
        return ToolResultContent(
            tool_use_id=tc.id,
            content=f"Tool execution error: {type(e).__name__}: {e}",
            is_error=True,
        )

    result = ToolResultContent(
        tool_use_id=tc.id,
        content=output.content,
        is_error=output.is_error,
    )

    # ── PostToolUse hook ─────────────────────────────────────────
    if hooks and hooks.has_event("PostToolUse"):
        hooks.run_hooks(
            "PostToolUse",
            tool_name=tc.name,
            tool_input=tc.input,
            tool_response=output.content,
        )

    return result


def _extract_allow_pattern(tc: ToolCallContent) -> str:
    """Extract a meaningful pattern from a tool call for session-allow rules."""
    params = tc.input
    # For file tools, use the directory
    if tc.name in ("Write", "Edit", "Read"):
        path = params.get("file_path", "")
        if path:
            import os
            dir_path = os.path.dirname(path)
            return f"{dir_path}/*" if dir_path else "*"
    # For Bash, use the command prefix
    if tc.name == "Bash":
        cmd = params.get("command", "").strip()
        # Use first word as pattern
        first_word = cmd.split()[0] if cmd.split() else "*"
        return first_word
    return "*"

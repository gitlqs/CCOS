"""Tool execution dispatcher — handles parallel/serial execution and permissions."""

from __future__ import annotations

import asyncio
from typing import Any

from ccos.hooks import HookManager
from ccos.permissions.manager import PermissionManager
from ccos.permissions.prompts import ask_permission
from ccos.providers.base import TextContent, ToolCallContent, ToolResultContent
from ccos.tools.base import PermissionDecision, Tool, ToolContext, ToolOutput, ToolRegistry


async def execute_tool_calls(
    tool_calls: list[ToolCallContent],
    registry: ToolRegistry,
    ctx: ToolContext,
    permissions: PermissionManager,
    hooks: HookManager | None = None,
) -> list[ToolResultContent]:
    """Execute a batch of tool calls, respecting concurrency and permissions.

    Read-only tools run in parallel; write tools run sequentially.
    """
    results: list[ToolResultContent] = []

    # Separate into read-only (parallelizable) and write (serial)
    read_only: list[ToolCallContent] = []
    write_ops: list[ToolCallContent] = []

    for tc in tool_calls:
        tool = registry.get(tc.name)
        if tool is None:
            results.append(ToolResultContent(
                tool_use_id=tc.id,
                content=f"Error: Unknown tool '{tc.name}'",
                is_error=True,
            ))
            continue
        if tool.is_read_only(tc.input):
            read_only.append(tc)
        else:
            write_ops.append(tc)

    # Execute read-only tools in parallel
    if read_only:
        tasks = [
            _execute_single(tc, registry, ctx, permissions, hooks)
            for tc in read_only
        ]
        parallel_results = await asyncio.gather(*tasks)
        results.extend(parallel_results)

    # Execute write tools sequentially
    for tc in write_ops:
        result = await _execute_single(tc, registry, ctx, permissions, hooks)
        results.append(result)

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
        return ToolResultContent(
            tool_use_id=tc.id,
            content=f"Permission denied: {perm.reason}",
            is_error=True,
        )

    if perm.decision == PermissionDecision.ASK:
        choice = ask_permission(tool, tc.input)
        if choice == "no":
            return ToolResultContent(
                tool_use_id=tc.id,
                content="The user denied this tool execution.",
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
                content="The user denied this tool execution.",
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

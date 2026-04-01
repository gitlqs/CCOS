"""Hook system — shell commands that run in response to tool/session events.

Mirrors CC's hook system. Hooks are configured in settings (user/project):
  {
    "hooks": {
      "PreToolUse": [
        { "matcher": "Bash", "hooks": [{ "type": "command", "command": "..." }] }
      ]
    }
  }

Supported events: PreToolUse, PostToolUse, UserPromptSubmit, SessionStart,
SessionEnd, Stop, Notification, etc.

Hook input is JSON on stdin. Output is JSON on stdout.
Exit code 0 = success, 2 = blocking error, other = non-blocking warning.
"""

from __future__ import annotations

import asyncio
import fnmatch
import json
import os
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Any, Literal

HookEvent = Literal[
    "PreToolUse",
    "PostToolUse",
    "PostToolUseFailure",
    "UserPromptSubmit",
    "SessionStart",
    "SessionEnd",
    "Stop",
    "Notification",
    "PreCompact",
    "PostCompact",
    "CwdChanged",
    "FileChanged",
]


@dataclass
class HookConfig:
    """A single hook definition."""
    type: str = "command"  # command | prompt | http
    command: str = ""
    shell: str = "bash"
    timeout: int = 600  # seconds
    status_message: str = ""
    once: bool = False
    if_filter: str = ""  # permission rule filter, e.g. "Bash(git *)"


@dataclass
class HookGroup:
    """A group of hooks sharing a matcher pattern."""
    matcher: str = ""  # empty = match all, or "Bash|Write|Edit"
    hooks: list[HookConfig] = field(default_factory=list)


@dataclass
class HookResult:
    """Result from a hook execution."""
    success: bool = True
    continue_execution: bool = True
    output: str = ""
    error: str = ""
    decision: str = ""  # "approve" | "block" | ""
    reason: str = ""
    system_message: str = ""
    updated_input: dict[str, Any] | None = None


class HookManager:
    """Load, match, and execute hooks for tool/session events."""

    def __init__(self) -> None:
        self._hooks: dict[str, list[HookGroup]] = {}
        self._session_id: str = ""
        self._cwd: str = ""
        self._transcript_path: str = ""

    def load_from_config(self, hooks_config: dict[str, Any]) -> None:
        """Load hooks from parsed config dict.

        Expected format:
        {
          "PreToolUse": [
            { "matcher": "Bash", "hooks": [{ "type": "command", "command": "..." }] }
          ]
        }
        """
        self._hooks.clear()
        for event_name, groups in hooks_config.items():
            if not isinstance(groups, list):
                continue
            parsed_groups: list[HookGroup] = []
            for g in groups:
                if not isinstance(g, dict):
                    continue
                group = HookGroup(matcher=g.get("matcher", ""))
                for h in g.get("hooks", []):
                    if not isinstance(h, dict):
                        continue
                    hc = HookConfig(
                        type=h.get("type", "command"),
                        command=h.get("command", ""),
                        shell=h.get("shell", "bash"),
                        timeout=h.get("timeout", 600),
                        status_message=h.get("statusMessage", ""),
                        once=h.get("once", False),
                        if_filter=h.get("if", ""),
                    )
                    group.hooks.append(hc)
                if group.hooks:
                    parsed_groups.append(group)
            if parsed_groups:
                self._hooks[event_name] = parsed_groups

    def set_session_info(
        self,
        session_id: str,
        cwd: str,
        transcript_path: str = "",
    ) -> None:
        self._session_id = session_id
        self._cwd = cwd
        self._transcript_path = transcript_path

    @property
    def has_hooks(self) -> bool:
        return bool(self._hooks)

    def has_event(self, event: str) -> bool:
        return event in self._hooks

    def run_hooks(
        self,
        event: HookEvent,
        *,
        tool_name: str = "",
        tool_input: dict[str, Any] | None = None,
        tool_response: str = "",
        prompt: str = "",
        extra: dict[str, Any] | None = None,
    ) -> HookResult:
        """Run all matching hooks for an event synchronously.

        Returns the combined result. If any hook blocks (exit code 2),
        the overall result is blocking.
        """
        groups = self._hooks.get(event, [])
        if not groups:
            return HookResult()

        combined = HookResult()

        for group in groups:
            if not _matches(group.matcher, tool_name):
                continue

            for hook in group.hooks:
                # Check if_filter
                if hook.if_filter and not _matches_if_filter(
                    hook.if_filter, tool_name, tool_input or {}
                ):
                    continue

                if hook.type != "command":
                    # Only command hooks are supported for now
                    continue

                result = _execute_command_hook(
                    hook,
                    event=event,
                    session_id=self._session_id,
                    cwd=self._cwd,
                    transcript_path=self._transcript_path,
                    tool_name=tool_name,
                    tool_input=tool_input,
                    tool_response=tool_response,
                    prompt=prompt,
                    extra=extra,
                )

                if not result.continue_execution:
                    combined.continue_execution = False
                    combined.error = result.error
                    combined.reason = result.reason
                    combined.decision = result.decision or "block"
                    return combined

                if result.output:
                    combined.output += result.output + "\n"
                if result.system_message:
                    combined.system_message = result.system_message
                if result.updated_input is not None:
                    combined.updated_input = result.updated_input

                # Handle 'once' hooks — remove after first execution
                if hook.once:
                    group.hooks.remove(hook)

        return combined

    async def run_hooks_async(
        self,
        event: HookEvent,
        **kwargs: Any,
    ) -> HookResult:
        """Async wrapper around run_hooks."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None,
            lambda: self.run_hooks(event, **kwargs),
        )


def _matches(matcher: str, tool_name: str) -> bool:
    """Check if a tool name matches the hook group's matcher pattern."""
    if not matcher:
        return True  # empty = match all
    patterns = [p.strip() for p in matcher.split("|")]
    return tool_name in patterns


def _matches_if_filter(
    if_filter: str,
    tool_name: str,
    tool_input: dict[str, Any],
) -> bool:
    """Check if the if_filter matches the tool call.

    Format: "Bash(git *)" or "Write(*.py)"
    """
    if not if_filter:
        return True

    # Parse "ToolName(pattern)"
    if "(" in if_filter and if_filter.endswith(")"):
        filter_tool = if_filter[: if_filter.index("(")]
        pattern = if_filter[if_filter.index("(") + 1 : -1]

        if filter_tool and filter_tool != tool_name:
            return False

        # Match pattern against the primary parameter
        primary = ""
        if tool_name == "Bash":
            primary = tool_input.get("command", "")
        elif tool_name in ("Read", "Write", "Edit"):
            primary = tool_input.get("file_path", "")
        elif tool_name == "Glob":
            primary = tool_input.get("pattern", "")
        elif tool_name == "Grep":
            primary = tool_input.get("pattern", "")
        else:
            primary = str(tool_input)

        return fnmatch.fnmatch(primary, pattern)

    # Simple tool name match
    return if_filter == tool_name


def _execute_command_hook(
    hook: HookConfig,
    *,
    event: str,
    session_id: str,
    cwd: str,
    transcript_path: str,
    tool_name: str,
    tool_input: dict[str, Any] | None,
    tool_response: str,
    prompt: str,
    extra: dict[str, Any] | None,
) -> HookResult:
    """Execute a single command hook via subprocess."""
    # Build the JSON input
    hook_input: dict[str, Any] = {
        "session_id": session_id,
        "cwd": cwd,
        "transcript_path": transcript_path,
        "hook_event_name": event,
    }

    if tool_name:
        hook_input["tool_name"] = tool_name
    if tool_input is not None:
        hook_input["tool_input"] = tool_input
    if tool_response:
        hook_input["tool_response"] = tool_response
    if prompt:
        hook_input["prompt"] = prompt
    if extra:
        hook_input.update(extra)

    input_json = json.dumps(hook_input, ensure_ascii=False) + "\n"

    try:
        # Choose shell
        if sys.platform == "win32" and hook.shell == "powershell":
            cmd = ["pwsh", "-NoProfile", "-NonInteractive", "-Command", hook.command]
            result = subprocess.run(
                cmd,
                input=input_json,
                capture_output=True,
                text=True,
                timeout=hook.timeout,
                cwd=cwd,
            )
        else:
            result = subprocess.run(
                hook.command,
                input=input_json,
                capture_output=True,
                text=True,
                timeout=hook.timeout,
                cwd=cwd,
                shell=True,
            )

        hr = HookResult()

        if result.returncode == 0:
            hr.success = True
            hr.output = result.stdout.strip()
            # Try to parse JSON output
            _parse_hook_output(hr, result.stdout)
        elif result.returncode == 2:
            # Blocking error
            hr.success = False
            hr.continue_execution = False
            hr.error = result.stderr.strip() or result.stdout.strip()
            hr.decision = "block"
            hr.reason = hr.error
        else:
            # Non-blocking warning
            hr.success = False
            hr.error = result.stderr.strip()

        return hr

    except subprocess.TimeoutExpired:
        return HookResult(
            success=False,
            error=f"Hook timed out after {hook.timeout}s: {hook.command}",
        )
    except Exception as e:
        return HookResult(success=False, error=f"Hook execution error: {e}")


def _parse_hook_output(hr: HookResult, stdout: str) -> None:
    """Parse JSON output from a hook's stdout."""
    stdout = stdout.strip()
    if not stdout:
        return

    try:
        data = json.loads(stdout)
        if not isinstance(data, dict):
            return

        if "continue" in data:
            hr.continue_execution = bool(data["continue"])
        if "decision" in data:
            hr.decision = data["decision"]
        if "reason" in data:
            hr.reason = data["reason"]
        if "systemMessage" in data:
            hr.system_message = data["systemMessage"]

        # hookSpecificOutput
        specific = data.get("hookSpecificOutput", {})
        if isinstance(specific, dict):
            if "updatedInput" in specific:
                hr.updated_input = specific["updatedInput"]
            if "permissionDecision" in specific:
                hr.decision = specific["permissionDecision"]
            if "permissionDecisionReason" in specific:
                hr.reason = specific["permissionDecisionReason"]

    except (json.JSONDecodeError, TypeError):
        pass  # Non-JSON output is fine

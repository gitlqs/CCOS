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
import re
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
    "SubagentStop",
    "Notification",
    "PreCompact",
    "PostCompact",
    "TeammateIdle",
    "TaskCreated",
    "TaskCompleted",
    "CwdChanged",
    "FileChanged",
]

# Default unset-timeout fallback in seconds (mirror cc
# TOOL_HOOK_EXECUTION_TIMEOUT_MS = 10 minutes).
TOOL_HOOK_EXECUTION_TIMEOUT_S = 600

# Per-event mapping from a hook event to the field on the hook input that the
# matcher should be tested against (mirror cc getMatchingHooks). Events not
# listed here match against the tool name (PreToolUse/PostToolUse/...).
_MATCH_QUERY_FIELD: dict[str, str] = {
    "PreToolUse": "tool_name",
    "PostToolUse": "tool_name",
    "PostToolUseFailure": "tool_name",
    "SessionStart": "source",
    "PreCompact": "trigger",
    "PostCompact": "trigger",
    "Notification": "notification_type",
    "SessionEnd": "reason",
    "SubagentStop": "agent_type",
    # FileChanged matches against basename(file_path) — handled specially.
    "FileChanged": "file_path",
}

# Events for which the permission-rule `if` filter applies (mirror cc
# prepareIfConditionMatcher).
_IF_FILTER_EVENTS = {
    "PreToolUse",
    "PostToolUse",
    "PostToolUseFailure",
    "PermissionRequest",
}


@dataclass
class HookConfig:
    """A single hook definition.

    Mirrors cc's discriminated hook schema. ``shell`` is optional (None means
    the platform default shell); only ``powershell`` switches to pwsh. ``timeout``
    is in seconds, falling back to TOOL_HOOK_EXECUTION_TIMEOUT_S (10 min) when
    unset. The prompt/http/agent fields round-trip in settings even though
    execution is command-only for now.
    """
    type: str = "command"  # command | prompt | http | agent
    command: str = ""
    # Shell interpreter. 'bash' uses your $SHELL (bash/zsh/sh); 'powershell'
    # uses pwsh. Defaults to bash.
    shell: str | None = None
    timeout: int | None = None  # seconds; None = TOOL_HOOK_EXECUTION_TIMEOUT_S
    status_message: str = ""
    once: bool = False
    if_filter: str = ""  # permission rule filter, e.g. "Bash(git *)"
    # If true, hook runs in background without blocking.
    is_async: bool = False
    # If true, hook runs in background and wakes the model on exit code 2.
    async_rewake: bool = False
    # prompt/agent hook fields.
    prompt: str = ""
    model: str = ""
    # http hook fields.
    url: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    allowed_env_vars: list[str] = field(default_factory=list)

    @property
    def effective_timeout(self) -> int:
        return self.timeout if self.timeout is not None else TOOL_HOOK_EXECUTION_TIMEOUT_S


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
    # Hide stdout from the transcript.
    suppress_output: bool = False
    # Message shown when continue is false.
    stop_reason: str = ""
    # additionalContext injected as a system-reminder (UserPromptSubmit /
    # PostToolUse / SessionStart / PreToolUse).
    additional_context: str = ""
    # SessionStart hookSpecificOutput.
    initial_user_message: str = ""
    watch_paths: list[str] = field(default_factory=list)


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
                    timeout = h.get("timeout")
                    hc = HookConfig(
                        type=h.get("type", "command"),
                        command=h.get("command", ""),
                        # Optional; None = platform default shell.
                        shell=h.get("shell"),
                        timeout=int(timeout) if timeout is not None else None,
                        status_message=h.get("statusMessage", ""),
                        once=h.get("once", False),
                        if_filter=h.get("if", ""),
                        is_async=bool(h.get("async", False)),
                        async_rewake=bool(h.get("asyncRewake", False)),
                        prompt=h.get("prompt", ""),
                        model=h.get("model", ""),
                        url=h.get("url", ""),
                        headers=h.get("headers", {}) or {},
                        allowed_env_vars=list(h.get("allowedEnvVars", []) or []),
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

        # Per-event query the matcher is tested against (mirror cc
        # getMatchingHooks). Tool events match tool_name; others map to a
        # field on the hook input.
        match_query = _match_query_for_event(
            event, tool_name=tool_name, tool_input=tool_input or {}, extra=extra or {}
        )

        for group in groups:
            if not _matches(group.matcher, match_query):
                continue

            for hook in group.hooks:
                # Check if_filter (only for tool/permission events).
                if (
                    hook.if_filter
                    and event in _IF_FILTER_EVENTS
                    and not _matches_if_filter(
                        hook.if_filter, tool_name, tool_input or {}
                    )
                ):
                    continue

                if hook.type != "command":
                    # Only command hooks are executed for now (prompt/http/agent
                    # round-trip in settings but are not run).
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
                    combined.stop_reason = result.stop_reason
                    return combined

                if result.output:
                    combined.output += result.output + "\n"
                if result.system_message:
                    combined.system_message = result.system_message
                if result.updated_input is not None:
                    combined.updated_input = result.updated_input
                if result.additional_context:
                    if combined.additional_context:
                        combined.additional_context += "\n" + result.additional_context
                    else:
                        combined.additional_context = result.additional_context
                if result.initial_user_message:
                    combined.initial_user_message = result.initial_user_message
                if result.watch_paths:
                    combined.watch_paths = result.watch_paths
                if result.suppress_output:
                    combined.suppress_output = True

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


_SIMPLE_MATCHER_RE = re.compile(r"^[A-Za-z0-9_|]+$")


def _matches(matcher: str, match_query: str) -> bool:
    """Check if *match_query* matches the hook group's matcher pattern.

    Mirrors cc's matchesPattern:
      - empty matcher or '*' matches everything
      - ^[A-Za-z0-9_|]+$ is an exact match (or pipe-separated exact list)
      - otherwise the matcher is compiled as a regex and tested with re.search
        semantics (mirroring JS RegExp.test); invalid regex returns False.
    """
    if not matcher or matcher == "*":
        return True

    if _SIMPLE_MATCHER_RE.match(matcher):
        if "|" in matcher:
            patterns = [p.strip() for p in matcher.split("|")]
            return match_query in patterns
        return match_query == matcher

    # Otherwise treat as a regex (re.search mirrors JS RegExp.test).
    try:
        return re.search(matcher, match_query) is not None
    except re.error:
        # Invalid regex — log and don't match (mirror cc).
        try:
            sys.stderr.write(f"Invalid regex pattern in hook matcher: {matcher}\n")
        except Exception:
            pass
        return False


def _match_query_for_event(
    event: str,
    *,
    tool_name: str,
    tool_input: dict[str, Any],
    extra: dict[str, Any],
) -> str:
    """Resolve the value a hook matcher is tested against for *event*.

    Mirrors cc getMatchingHooks: tool events match the tool name; other events
    map to a field on the hook input (source/trigger/notification_type/etc.).
    FileChanged matches against basename(file_path).
    """
    field_name = _MATCH_QUERY_FIELD.get(event)
    if field_name is None:
        # No per-event query (e.g. Stop, TeammateIdle) — match against tool name
        # if present, else empty so only empty/'*' matchers fire.
        return tool_name
    if field_name == "tool_name":
        return tool_name
    if event == "FileChanged":
        path = extra.get("file_path", "") or tool_input.get("file_path", "")
        return os.path.basename(path) if path else ""
    return str(extra.get(field_name, ""))


def _matches_if_filter(
    if_filter: str,
    tool_name: str,
    tool_input: dict[str, Any],
) -> bool:
    """Check if the `if` permission-rule filter matches the tool call.

    Mirrors cc prepareIfConditionMatcher: parse the rule via permission-rule
    syntax ("Bash(git *)"), require the tool name to match, and if a rule
    content is present, evaluate it with the SAME engine permission allow/deny
    rules use (Bash command-prefix/glob matching, gitignore-style path globbing
    per tool). A rule with no content matches any call to that tool.
    """
    if not if_filter:
        return True

    filter_tool, rule_content = _parse_permission_rule(if_filter)
    if filter_tool and filter_tool != tool_name:
        return False
    if not rule_content:
        # No rule content — matches any call to this tool.
        return True
    return _permission_rule_matches(tool_name, rule_content, tool_input)


def _parse_permission_rule(rule: str) -> tuple[str, str]:
    """Parse "Tool(content)" -> (tool, content). Bare "Tool" -> (tool, "")."""
    rule = rule.strip()
    if "(" in rule and rule.endswith(")"):
        tool = rule[: rule.index("(")].strip()
        content = rule[rule.index("(") + 1 : -1]
        return tool, content
    return rule, ""


def _permission_rule_matches(
    tool_name: str,
    rule_content: str,
    tool_input: dict[str, Any],
) -> bool:
    """Evaluate a permission rule against a tool call (same engine as allow/deny).

    - Bash: prefix match with trailing '*' wildcard (e.g. "git *"), else glob.
    - File tools (Read/Write/Edit/NotebookEdit): gitignore-style path globbing
      against file_path.
    - Glob/Grep: glob match against the pattern argument.
    - Otherwise: glob match against the stringified input.
    """
    if tool_name == "Bash":
        command = str(tool_input.get("command", ""))
        return _bash_command_matches(command, rule_content)

    if tool_name in ("Read", "Write", "Edit", "NotebookEdit"):
        file_path = str(tool_input.get("file_path", "") or tool_input.get("notebook_path", ""))
        return _path_matches(file_path, rule_content)

    if tool_name in ("Glob", "Grep"):
        pattern = str(tool_input.get("pattern", ""))
        return fnmatch.fnmatch(pattern, rule_content)

    return fnmatch.fnmatch(str(tool_input), rule_content)


def _bash_command_matches(command: str, rule_content: str) -> bool:
    """Match a Bash command against a permission rule (prefix/glob semantics)."""
    command = command.strip()
    rule = rule_content.strip()
    if rule in ("", "*"):
        return True
    # "git *" style: prefix match on everything before the trailing '*'.
    if rule.endswith("*") and "*" not in rule[:-1]:
        prefix = rule[:-1]
        return command.startswith(prefix)
    # Exact command or glob fallback.
    return command == rule or fnmatch.fnmatch(command, rule)


def _path_matches(file_path: str, rule_content: str) -> bool:
    """Match a file path against a permission rule (gitignore-style globbing)."""
    if rule_content in ("", "*"):
        return True
    base = os.path.basename(file_path)
    # Match against the full path and the basename (mirror gitignore semantics).
    return (
        fnmatch.fnmatch(file_path, rule_content)
        or fnmatch.fnmatch(base, rule_content)
        or fnmatch.fnmatch(file_path, f"*{os.sep}{rule_content}")
    )


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

    timeout = hook.effective_timeout

    try:
        # Choose shell. Only branch to powershell when explicitly requested;
        # when unset use the platform default shell (shell=True).
        if hook.shell == "powershell":
            cmd = ["pwsh", "-NoProfile", "-NonInteractive", "-Command", hook.command]
            result = subprocess.run(
                cmd,
                input=input_json,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=cwd,
            )
        else:
            result = subprocess.run(
                hook.command,
                input=input_json,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=cwd,
                shell=True,
            )

        hr = HookResult()

        if result.returncode == 0:
            hr.success = True
            hr.output = result.stdout.strip()
            # Try to parse JSON output
            _parse_hook_output(hr, result.stdout, event=event)
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
            error=f"Hook timed out after {timeout}s: {hook.command}",
        )
    except Exception as e:
        return HookResult(success=False, error=f"Hook execution error: {e}")


def _parse_hook_output(hr: HookResult, stdout: str, *, event: str = "") -> None:
    """Parse JSON output from a hook's stdout (mirror cc processHookJSONOutput)."""
    stdout = stdout.strip()
    if not stdout:
        return

    try:
        data = json.loads(stdout)
        if not isinstance(data, dict):
            return

        # continue:false stops execution; stopReason is the surfaced message.
        if "continue" in data:
            hr.continue_execution = bool(data["continue"])
            if hr.continue_execution is False and data.get("stopReason"):
                hr.stop_reason = data["stopReason"]
        if "suppressOutput" in data:
            hr.suppress_output = bool(data["suppressOutput"])
        # decision: 'approve' -> allow, 'block' -> deny + blocking error.
        if "decision" in data:
            decision = data["decision"]
            if decision == "approve":
                hr.decision = "approve"
            elif decision == "block":
                hr.decision = "block"
                hr.continue_execution = False
                hr.reason = data.get("reason", "") or "Blocked by hook"
            else:
                hr.decision = decision
        if "reason" in data and not hr.reason:
            hr.reason = data["reason"]
        # systemMessage: warning message shown to the user.
        if "systemMessage" in data:
            hr.system_message = data["systemMessage"]

        # hookSpecificOutput
        specific = data.get("hookSpecificOutput", {})
        if isinstance(specific, dict):
            if "updatedInput" in specific:
                hr.updated_input = specific["updatedInput"]
            perm = specific.get("permissionDecision")
            if perm == "allow":
                hr.decision = "approve"
            elif perm == "deny":
                hr.decision = "block"
                hr.continue_execution = False
                # permissionDecisionReason precedes json.reason (cc precedence).
                hr.reason = (
                    specific.get("permissionDecisionReason")
                    or data.get("reason")
                    or "Blocked by hook"
                )
            elif perm == "ask":
                hr.decision = "ask"
            elif perm is not None:
                hr.decision = perm
            if "permissionDecisionReason" in specific and not hr.reason:
                hr.reason = specific["permissionDecisionReason"]
            # additionalContext is injected as a system-reminder for
            # UserPromptSubmit/PostToolUse/SessionStart/PreToolUse.
            if specific.get("additionalContext"):
                hr.additional_context = specific["additionalContext"]
            # SessionStart-specific outputs.
            if specific.get("initialUserMessage"):
                hr.initial_user_message = specific["initialUserMessage"]
            if specific.get("watchPaths"):
                hr.watch_paths = list(specific["watchPaths"])

    except (json.JSONDecodeError, TypeError):
        pass  # Non-JSON output is fine

"""Permission manager — gates tool execution based on rules and user decisions."""

from __future__ import annotations

import fnmatch
import re
from enum import Enum
from typing import Any

from ccos.tools.base import PermissionCheck, PermissionDecision, Tool, ToolContext


class PermissionMode(str, Enum):
    DEFAULT = "default"        # Ask for non-read-only operations
    AUTO = "auto"              # Auto-approve most tools, ask for dangerous ops
    TRUST_ALL = "trust_all"    # Auto-allow everything (--dangerously-skip-permissions)
    PLAN = "plan"              # Plan mode: only read + plan file writes
    READ_ONLY = "read_only"    # Only allow read-only operations


# Bash commands that are safe to auto-approve
_SAFE_BASH_PREFIXES = (
    "ls", "dir", "pwd", "echo", "cat", "head", "tail", "wc",
    "which", "where", "type", "file", "stat", "du", "df",
    "git status", "git log", "git diff", "git branch", "git show",
    "git remote", "git tag", "git stash list",
    "python --version", "python -V", "pip list", "pip show",
    "node --version", "npm list", "npx --version",
    "cargo --version", "rustc --version",
    "go version", "java --version", "javac --version",
    "uname", "hostname", "whoami", "date", "env",
    "rg ", "fd ", "find ", "grep ",
    "tree",
)

# Bash commands/patterns that should always be denied
_DANGEROUS_BASH_PATTERNS = (
    r"\brm\s+-rf\s+[/~]",           # rm -rf / or ~
    r"\bsudo\b",                     # anything with sudo
    r"\b(curl|wget)\b.*\|\s*\bsh\b", # pipe to shell
    r"\bdd\s+if=",                   # dd
    r"\bmkfs\b",                     # format disk
    r"\b(shutdown|reboot|halt)\b",   # system control
    r":(){.*};:",                     # fork bomb
    r"\bchmod\s+777\b",             # world-writable
)


class PermissionManager:
    """Manages tool execution permissions."""

    def __init__(
        self,
        mode: PermissionMode = PermissionMode.DEFAULT,
        always_allow: dict[str, set[str]] | None = None,
        always_deny: dict[str, set[str]] | None = None,
    ):
        self.mode = mode
        self.always_allow: dict[str, set[str]] = always_allow or {}
        self.always_deny: dict[str, set[str]] = always_deny or {}
        self._session_allows: dict[str, set[str]] = {}  # Remembered for this session

    def check(self, tool: Tool, params: dict[str, Any], ctx: ToolContext) -> PermissionCheck:
        """Check if tool execution is allowed."""
        if self.mode == PermissionMode.TRUST_ALL:
            return PermissionCheck(PermissionDecision.ALLOW)

        if self.mode == PermissionMode.READ_ONLY:
            if tool.is_read_only(params):
                return PermissionCheck(PermissionDecision.ALLOW)
            return PermissionCheck(
                PermissionDecision.DENY,
                reason="Read-only mode: write operations are not allowed.",
            )

        if self.mode == PermissionMode.PLAN:
            # Plan mode: allow read-only tools + plan file writes
            if tool.is_read_only(params):
                return PermissionCheck(PermissionDecision.ALLOW)
            # Allow writing to plan files
            if tool.name in ("Write", "Edit") and self._is_plan_file(params):
                return PermissionCheck(PermissionDecision.ALLOW)
            if tool.name in ("EnterPlanMode", "ExitPlanMode", "AskUserQuestion", "TodoWrite"):
                return PermissionCheck(PermissionDecision.ALLOW)
            return PermissionCheck(
                PermissionDecision.DENY,
                reason="Plan mode: only read operations and plan file edits are allowed.",
            )

        # AUTO mode: auto-approve most operations, ask only for dangerous ones
        if self.mode == PermissionMode.AUTO:
            return self._check_auto(tool, params, ctx)

        # DEFAULT mode
        if tool.is_read_only(params):
            return PermissionCheck(PermissionDecision.ALLOW)

        # Special Bash classification
        if tool.name == "Bash":
            bash_check = self._check_bash(params)
            if bash_check is not None:
                return bash_check

        # Check deny rules (persistent + session)
        if tool.name in self.always_deny:
            for pattern in self.always_deny[tool.name]:
                if self._matches(params, pattern):
                    return PermissionCheck(
                        PermissionDecision.DENY,
                        reason=f"Denied by rule: {pattern}",
                    )

        # Check allow rules (persistent + session)
        for source in (self.always_allow, self._session_allows):
            if tool.name in source:
                for pattern in source[tool.name]:
                    if self._matches(params, pattern):
                        return PermissionCheck(PermissionDecision.ALLOW)

        # Default: ask the user
        return PermissionCheck(PermissionDecision.ASK)

    def add_always_allow(self, tool_name: str, pattern: str) -> None:
        self.always_allow.setdefault(tool_name, set()).add(pattern)

    def add_always_deny(self, tool_name: str, pattern: str) -> None:
        self.always_deny.setdefault(tool_name, set()).add(pattern)

    def add_session_allow(self, tool_name: str, pattern: str) -> None:
        """Remember an allow for this session only."""
        self._session_allows.setdefault(tool_name, set()).add(pattern)

    def _check_auto(self, tool: Tool, params: dict[str, Any], ctx: ToolContext) -> PermissionCheck:
        """Auto-mode permission check.

        Auto-approves most operations. Only asks for:
        - Dangerous bash commands
        - Commands that affect external systems (git push, etc.)
        - Deny-listed patterns
        """
        # Check deny rules first
        if tool.name in self.always_deny:
            for pattern in self.always_deny[tool.name]:
                if self._matches(params, pattern):
                    return PermissionCheck(
                        PermissionDecision.DENY,
                        reason=f"Denied by rule: {pattern}",
                    )

        # Bash: classify command
        if tool.name == "Bash":
            bash_check = self._check_bash(params)
            if bash_check is not None:
                if bash_check.decision == PermissionDecision.DENY:
                    return bash_check
                # In auto mode, safe commands pass through
                return PermissionCheck(PermissionDecision.ALLOW)

            # Unclassified bash commands: check for external side effects
            cmd = params.get("command", "").strip()
            if self._is_external_command(cmd):
                return PermissionCheck(PermissionDecision.ASK)
            # Auto-approve other bash commands
            return PermissionCheck(PermissionDecision.ALLOW)

        # File operations: auto-approve within the working directory
        if tool.name in ("Write", "Edit"):
            path = params.get("file_path", "")
            if path:
                import os
                abs_path = os.path.abspath(path)
                abs_cwd = os.path.abspath(ctx.cwd)
                if abs_path.startswith(abs_cwd):
                    return PermissionCheck(PermissionDecision.ALLOW)
                # Outside cwd: ask
                return PermissionCheck(PermissionDecision.ASK)

        # All other tools: auto-approve
        return PermissionCheck(PermissionDecision.ALLOW)

    @staticmethod
    def _is_external_command(cmd: str) -> bool:
        """Check if a bash command affects external systems."""
        external_patterns = (
            "git push", "git pull", "git fetch",
            "gh pr", "gh issue", "gh release",
            "npm publish", "pip upload", "cargo publish",
            "docker push",
            "curl -X POST", "curl -X PUT", "curl -X DELETE",
            "wget --post",
            "ssh ", "scp ", "rsync ",
            "aws ", "gcloud ", "az ",
            "kubectl apply", "kubectl delete",
            "terraform apply", "terraform destroy",
        )
        cmd_lower = cmd.lower()
        return any(p in cmd_lower for p in external_patterns)

    def _check_bash(self, params: dict[str, Any]) -> PermissionCheck | None:
        """Classify Bash commands for auto-allow or auto-deny."""
        cmd = params.get("command", "").strip()
        if not cmd:
            return None

        # Check dangerous patterns first
        for pattern in _DANGEROUS_BASH_PATTERNS:
            if re.search(pattern, cmd):
                return PermissionCheck(
                    PermissionDecision.DENY,
                    reason=f"Command matches dangerous pattern.",
                )

        # Check safe prefixes
        cmd_lower = cmd.lower()
        for prefix in _SAFE_BASH_PREFIXES:
            if cmd_lower.startswith(prefix):
                return PermissionCheck(PermissionDecision.ALLOW)

        return None  # Not classified, fall through to normal check

    @staticmethod
    def _is_plan_file(params: dict[str, Any]) -> bool:
        """Check if the target is a plan file."""
        path = params.get("file_path", "")
        if not path:
            return False
        # Allow writes to .ccos/plans/ directory or files ending in plan.md
        return ("/plans/" in path.replace("\\", "/") or
                path.replace("\\", "/").endswith("plan.md"))

    @staticmethod
    def _matches(params: dict[str, Any], pattern: str) -> bool:
        """Pattern matching against tool params."""
        if pattern == "*":
            return True
        # Match against common param fields
        for val in params.values():
            if isinstance(val, str):
                if pattern in val:
                    return True
                # Try glob matching for path patterns
                if fnmatch.fnmatch(val, pattern):
                    return True
        return False

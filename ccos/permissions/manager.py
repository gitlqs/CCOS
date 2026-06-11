"""Permission manager — gates tool execution based on rules and user decisions.

This module ports cc's permission decision model (src/utils/permissions/
permissions.ts, src/tools/BashTool/{bashPermissions,readOnlyValidation,
modeValidation}.ts and src/utils/permissions/filesystem.ts) to CCOS.

Key invariants mirrored from cc:
  * Deny rules are evaluated FIRST, before any auto-allow (permissions.ts
    step 1a getDenyRuleForTool).
  * Bash is never hard-denied from content heuristics. Read-only auto-allow is
    structural (flag-validated allowlists), not a string-prefix match.
  * Bypass-immune safetyCheck paths (.git/.claude/shell rc files) always force a
    prompt on writes, even in bypassPermissions / acceptEdits mode
    (filesystem.ts checkPathSafetyForAutoEdit + permissions.ts step 1g).
"""

from __future__ import annotations

import fnmatch
import os
import re
from enum import Enum
from pathlib import Path
from typing import Any

from ccos.tools.base import PermissionCheck, PermissionDecision, Tool, ToolContext


class PermissionMode(str, Enum):
    """User-addressable permission modes.

    Realigned to cc's EXTERNAL_PERMISSION_MODES (src/types/permissions.ts):
    'acceptEdits', 'bypassPermissions', 'default', 'dontAsk', 'plan'.

    TRUST_ALL is kept as a backwards-compatible alias for BYPASS_PERMISSIONS so
    existing callers (e.g. --dangerously-skip-permissions wiring) keep working.
    """

    DEFAULT = "default"                      # Ask for non-read-only operations
    PLAN = "plan"                            # Plan mode: read + plan-file writes
    ACCEPT_EDITS = "acceptEdits"             # Auto-allow in-cwd edits + fs bash
    BYPASS_PERMISSIONS = "bypassPermissions"  # Allow all except bypass-immune rules
    DONT_ASK = "dontAsk"                     # Convert every 'ask' into a 'deny'

    # Backwards-compatible alias (same value -> aliases BYPASS_PERMISSIONS).
    # Maps --dangerously-skip-permissions / legacy TRUST_ALL onto bypass.
    TRUST_ALL = "bypassPermissions"

    @classmethod
    def _missing_(cls, value: object) -> "PermissionMode":
        """Gracefully coerce unknown / legacy mode strings.

        Mirrors cc's permissionModeFromString, which falls back to 'default'
        for anything it does not recognise instead of throwing. This keeps a
        settings.json mode of e.g. 'acceptEdits' or an invented legacy value
        ('auto', 'trust_all', 'read_only') from crashing startup.
        """
        if isinstance(value, str):
            lowered = value.strip().lower()
            # Legacy CCOS aliases.
            if lowered in ("trust_all", "trustall", "yolo"):
                return cls.BYPASS_PERMISSIONS
            # cc has an ant-only 'auto' mode; CCOS has no classifier, so treat
            # it (and the old invented 'read_only') as default.
            # Case-insensitive match against canonical values.
            for member in cls:
                if member.value.lower() == lowered:
                    return member
        return cls.DEFAULT


# ── Bash read-only command classification (ported from cc) ────────────────────
# cc src/tools/BashTool/readOnlyValidation.ts. cc never hard-denies from content
# heuristics; read-only auto-allow is structural. We port a faithful subset:
#  * SIMPLE_READONLY_COMMANDS  — base commands whose every invocation is safe.
#  * FLAG_VALIDATED_COMMANDS   — commands with a safe-flag allowlist; unknown
#    flags fall through to ASK (NOT deny). Dangerous flags are simply excluded.

# Base commands that only read. Matched by word boundary on the base command;
# they accept arbitrary (non-operator) arguments since they cannot write/execute.
SIMPLE_READONLY_COMMANDS: frozenset[str] = frozenset({
    # File content viewing
    "cat", "head", "tail", "wc", "stat", "strings", "hexdump", "od", "nl",
    "tac", "rev", "fold", "expand", "unexpand", "fmt", "comm", "cmp", "numfmt",
    # Path information
    "basename", "dirname", "realpath", "readlink", "pwd",
    # Text processing
    "cut", "paste", "tr", "column", "diff", "uniq", "sort",
    # System info
    "id", "uname", "free", "df", "du", "locale", "groups", "nproc", "whoami",
    "uptime", "cal", "hostname", "arch",
    # Listing / navigation (path validation handled structurally elsewhere)
    "ls", "dir", "tree",
    # Misc safe
    "echo", "printf", "which", "type", "expr", "test", "true", "false",
    "sleep", "seq", "tsort", "pr", "getconf",
    # Checksums (read + verify only)
    "sha256sum", "sha1sum", "md5sum",
})

# Commands with a safe-flag allowlist. A flag NOT in the set means we cannot
# prove the command is read-only, so it falls through to ASK (never auto-deny).
# SECURITY: dangerous flags are excluded by omission, mirroring cc:
#   fd/rg: -x/--exec/-X/--exec-batch excluded (execute per result);
#          fd -l/--list-details excluded (spawns `ls`).
#   git:   only read-only subcommands (status/log/diff/show/...).
FLAG_VALIDATED_COMMANDS: dict[str, "frozenset[str]"] = {
    # ripgrep — read-only search. Exclude --pre / --pre-glob / --hostname-bin
    # (execute external programs) and -f/--file is allowed (reads patterns).
    "rg": frozenset({
        "-i", "--ignore-case", "-S", "--smart-case", "-s", "--case-sensitive",
        "-w", "--word-regexp", "-x", "--line-regexp", "-v", "--invert-match",
        "-c", "--count", "--count-matches", "-l", "--files-with-matches",
        "--files-without-match", "-o", "--only-matching", "-n", "--line-number",
        "-N", "--no-line-number", "-H", "--with-filename", "--no-filename",
        "-A", "--after-context", "-B", "--before-context", "-C", "--context",
        "-e", "--regexp", "-F", "--fixed-strings", "-P", "--pcre2",
        "-g", "--glob", "--iglob", "-t", "--type", "-T", "--type-not",
        "--hidden", "--no-ignore", "-u", "--unrestricted", "--max-count", "-m",
        "--color", "--colors", "--json", "--no-heading", "--heading",
        "--sort", "--sortr", "--max-depth", "--maxdepth", "-z", "--search-zip",
        "-0", "--null", "--null-data", "-q", "--quiet", "-V", "--version",
        "-h", "--help",
    }),
    # fd — file finder. SECURITY: -x/--exec, -X/--exec-batch, -l/--list-details
    # deliberately excluded (execute commands / spawn ls).
    "fd": frozenset({
        "-h", "--help", "-V", "--version", "-H", "--hidden", "-I", "--no-ignore",
        "--no-ignore-vcs", "-s", "--case-sensitive", "-i", "--ignore-case",
        "-g", "--glob", "--regex", "-F", "--fixed-strings", "-a", "--absolute-path",
        "-L", "--follow", "-p", "--full-path", "-0", "--print0",
        "-d", "--max-depth", "--min-depth", "--exact-depth",
        "-t", "--type", "-e", "--extension", "-S", "--size",
        "-E", "--exclude", "--ignore-file", "-c", "--color", "-1",
        "-q", "--quiet", "--strip-cwd-prefix", "--max-results",
    }),
    "fdfind": frozenset({
        "-h", "--help", "-V", "--version", "-H", "--hidden", "-I", "--no-ignore",
        "--no-ignore-vcs", "-s", "--case-sensitive", "-i", "--ignore-case",
        "-g", "--glob", "--regex", "-F", "--fixed-strings", "-a", "--absolute-path",
        "-L", "--follow", "-p", "--full-path", "-0", "--print0",
        "-d", "--max-depth", "--min-depth", "--exact-depth",
        "-t", "--type", "-e", "--extension", "-S", "--size",
        "-E", "--exclude", "--ignore-file", "-c", "--color", "-1",
        "-q", "--quiet", "--strip-cwd-prefix", "--max-results",
    }),
    # grep — read-only search.
    "grep": frozenset({
        "-e", "--regexp", "-f", "--file", "-F", "--fixed-strings",
        "-G", "--basic-regexp", "-E", "--extended-regexp", "-P", "--perl-regexp",
        "-i", "--ignore-case", "-v", "--invert-match", "-w", "--word-regexp",
        "-x", "--line-regexp", "-c", "--count", "--color", "--colour",
        "-L", "--files-without-match", "-l", "--files-with-matches",
        "-m", "--max-count", "-o", "--only-matching", "-q", "--quiet",
        "--silent", "-s", "--no-messages", "-b", "--byte-offset",
        "-H", "--with-filename", "-h", "--no-filename", "-n", "--line-number",
        "-A", "--after-context", "-B", "--before-context", "-C", "--context",
        "-r", "--recursive", "-R", "--dereference-recursive",
        "--include", "--exclude", "--exclude-dir", "-a", "--text",
    }),
}

# git read-only subcommands (cc GIT_READ_ONLY_COMMANDS). Auto-allowed regardless
# of trailing args/flags since these subcommands don't mutate the repo.
GIT_READ_ONLY_SUBCOMMANDS: frozenset[str] = frozenset({
    "status", "log", "diff", "show", "branch", "remote", "tag",
    "blame", "ls-files", "ls-tree", "rev-parse", "describe", "shortlog",
    "reflog", "cat-file", "whatchanged",
})

# Subcommand operators that split a compound command (cc splitCommand). We split
# on these to evaluate each subcommand independently.
_COMPOUND_SPLIT_RE = re.compile(r"\|\||&&|;|\||\n")

# Leading env-var assignment (VAR=value). Used to strip prefixes for deny/ask
# matching (cc stripAllLeadingEnvVars).
_ENV_VAR_ASSIGN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(?:\[[^\]]*\])?\+?=\S*\s+")

# Safe wrappers that exec their args (cc stripSafeWrappers). Stripped iteratively
# for deny/ask matching so 'timeout 5 rm -rf x' still matches a deny on rm.
_SAFE_WRAPPERS: frozenset[str] = frozenset({"timeout", "time", "nice", "nohup", "stdbuf"})

# Safe redirection patterns that only discard output (to nul / /dev/null).
# These are tolerated in read-only discovery commands (e.g. 'dir ... 2>nul || echo ...')
# without causing the command to be treated as having write/execute side effects.
_SAFE_REDIR_PATTERNS: list[str] = [
    r'2\s*\^?>\s*(nul|NUL|/dev/null)',
    r'>\s*(nul|NUL|/dev/null)',
    r'2\s*&\s*>\s*1',
    r'>\s*/dev/null(?:\s+2\s*&\s*>\s*1)?',
]


# ── Bypass-immune dangerous files / directories (cc filesystem.ts) ────────────
# Exact lists from cc src/utils/permissions/filesystem.ts. Writes touching any
# of these always prompt, even in bypassPermissions / acceptEdits mode.
DANGEROUS_FILES: frozenset[str] = frozenset({
    ".gitconfig",
    ".gitmodules",
    ".bashrc",
    ".bash_profile",
    ".zshrc",
    ".zprofile",
    ".profile",
    ".ripgreprc",
    ".mcp.json",
    ".claude.json",
})

DANGEROUS_DIRECTORIES: frozenset[str] = frozenset({
    ".git",
    ".vscode",
    ".idea",
    ".claude",
})

# acceptEdits filesystem bash allowlist (cc modeValidation.ts
# ACCEPT_EDITS_ALLOWED_COMMANDS). In acceptEdits mode a bash subcommand whose
# base command is in this set is auto-allowed (after deny/ask/safety checks).
ACCEPT_EDITS_BASH_COMMANDS: frozenset[str] = frozenset({
    "mkdir", "touch", "rm", "rmdir", "mv", "cp", "sed",
})


class PermissionManager:
    """Manages tool execution permissions following cc's decision order.

    Root safety invariant:
    - Only the top-level main REPL driver (the engine run by run_interactive /
      run_single) may ever produce an interactive permission prompt.
    - All sub-agents (Agent tool, forked skills, background memory extraction,
      any other forked QueryEngine) MUST be given a PermissionManager with
      prompting_allowed=False. When ASK would be returned, it is converted to
      DENY with a clear reason. This guarantees no rich.console.input() or
      prompt_toolkit conflict can ever occur while the main REPL is blocked
      inside PromptSession.prompt() showing the live ❯ prompt.
    - Memory extraction gets an even stricter policy via for_memory_extraction:
      it may only read/write inside its designated memory directory using a
      tiny allowlist of tools (Read/Write/Edit/Glob/Grep + read-only Bash).
      No interactive tools, no external I/O, no rm, no Agent, etc.
    """

    def __init__(
        self,
        mode: PermissionMode = PermissionMode.DEFAULT,
        always_allow: dict[str, set[str]] | None = None,
        always_deny: dict[str, set[str]] | None = None,
        *,
        prompting_allowed: bool = True,
        memory_dir: str | None = None,
    ):
        self.mode = mode
        self.always_allow: dict[str, set[str]] = always_allow or {}
        self.always_deny: dict[str, set[str]] = always_deny or {}
        self._session_allows: dict[str, set[str]] = {}  # Remembered for this session
        self.prompting_allowed: bool = prompting_allowed
        self.memory_dir: str | None = os.path.abspath(memory_dir) if memory_dir else None

    # ──────────────────────────────────────────────────────────────────────
    # Main decision flow
    # ──────────────────────────────────────────────────────────────────────
    def check(self, tool: Tool, params: dict[str, Any], ctx: ToolContext) -> PermissionCheck:
        """Check if tool execution is allowed.

        Order mirrors cc hasPermissionsToUseToolInner + the tools' own
        checkPermissions:
          1. Deny rules (tool-wide, then content-specific). Always first.
          2. Ask rules (tool-wide, then content-specific). Bypass-immune.
          3. Bypass-immune write safetyCheck (.git/.claude/shell rc).
          4. Mode handling (bypassPermissions / acceptEdits / plan).
          5. Read-only structural allow.
          6. Allow rules (persistent + session).
          7. Default: ASK (or DENY under dontAsk).
        """
        is_bash = tool.name == "Bash"
        command = params.get("command", "") if is_bash else ""

        # 1a. Tool-wide deny rule (cc getDenyRuleForTool, step 1a).
        if self._tool_wide_rule(self.always_deny, tool.name):
            return PermissionCheck(
                PermissionDecision.DENY,
                reason=f"Permission to use {tool.name} has been denied.",
            )

        # 1b. Content-specific deny rule.
        deny_pattern = self._matching_content_pattern(
            self.always_deny, tool, params, behavior="deny"
        )
        if deny_pattern is not None:
            return PermissionCheck(
                PermissionDecision.DENY,
                reason=f"Denied by rule: {deny_pattern}",
            )

        # 2a. Tool-wide ask rule (bypass-immune).
        if self._tool_wide_rule(getattr(self, "always_ask", {}), tool.name):
            return self._finish_ask(tool)

        # 2b. Content-specific ask rule (bypass-immune).
        ask_pattern = self._matching_content_pattern(
            getattr(self, "always_ask", {}), tool, params, behavior="ask"
        )
        if ask_pattern is not None:
            return self._finish_ask(tool)

        # 3. Bypass-immune write safetyCheck: dangerous files/dirs always prompt.
        if tool.name in ("Write", "Edit"):
            path = params.get("file_path", "")
            if path and self._is_dangerous_path(path):
                return self._finish_ask(tool)

        # 4a. bypassPermissions: allow everything that survived deny/ask/safety.
        if self.mode == PermissionMode.BYPASS_PERMISSIONS:
            return PermissionCheck(PermissionDecision.ALLOW)

        # 4b. PLAN mode handling (after deny/ask/safety, mirroring cc step 2a).
        if self.mode == PermissionMode.PLAN:
            return self._check_plan(tool, params, command, is_bash)

        # 5. Read-only structural allow (cc step 7: BashTool.isReadOnly / Read).
        if is_bash:
            if self._bash_is_read_only(command):
                return PermissionCheck(PermissionDecision.ALLOW)
        elif tool.is_read_only(params):
            return PermissionCheck(PermissionDecision.ALLOW)

        # 6. Memory-extraction scoped auto-allow (strict, non-interactive only).
        # This runs AFTER structural read-only allow, so read-only tools are
        # already allowed above. We only need to auto-allow the write-side tools
        # when they target the designated memory dir, and deny everything else.
        if self.memory_dir and not self.prompting_allowed:
            mem_allow = self._check_memory_extraction_policy(tool, params, ctx)
            if mem_allow is not None:
                return mem_allow
            # If we are in a memory-scoped manager and the action is not
            # explicitly allowed by the memory policy, deny it rather than
            # falling through to a generic ASK (which would be turned into
            # DENY by _finish_ask anyway). This gives a clearer reason.
            return PermissionCheck(
                PermissionDecision.DENY,
                reason="Memory extraction policy: only read/write inside the memory directory using Read/Write/Edit/Glob/Grep and read-only Bash is permitted.",
            )

        # 7. acceptEdits auto-allow (writes in cwd + filesystem bash commands).
        if self.mode == PermissionMode.ACCEPT_EDITS:
            accept = self._check_accept_edits(tool, params, command, is_bash, ctx)
            if accept is not None:
                return accept

        # 8. Allow rules (persistent + session).
        for source in (self.always_allow, self._session_allows):
            if self._matching_content_pattern(source, tool, params, behavior="allow") is not None:
                return PermissionCheck(PermissionDecision.ALLOW)
            if self._tool_wide_rule(source, tool.name):
                return PermissionCheck(PermissionDecision.ALLOW)

        # 9. Default: ask the user (dontAsk converts to deny).
        # If prompting_allowed=False this becomes DENY (see _finish_ask).
        return self._finish_ask(tool)

    def _finish_ask(self, tool: Tool) -> PermissionCheck:
        """Final ASK, applying dontAsk-mode transformation (cc step at end).

        Architectural safety: if prompting_allowed is False (any sub-engine,
        background task, memory extractor, forked agent, etc.), we MUST NEVER
        return ASK, because that would lead to a blocking rich.console.input()
        or similar while the main REPL is inside prompt_toolkit's PromptSession
        showing the live ❯ prompt. Converting to DENY is the only safe choice.
        """
        if not self.prompting_allowed or self.mode == PermissionMode.DONT_ASK:
            return PermissionCheck(
                PermissionDecision.DENY,
                reason=(
                    f"{tool.name} requires approval but interactive permission prompts "
                    "are disabled in this context (sub-agent, background task, or memory extraction)."
                ),
            )
        return PermissionCheck(PermissionDecision.ASK)

    def _check_plan(
        self, tool: Tool, params: dict[str, Any], command: str, is_bash: bool
    ) -> PermissionCheck:
        """Plan mode allowances (run AFTER deny/ask/safety so deny always wins)."""
        if is_bash:
            if self._bash_is_read_only(command):
                return PermissionCheck(PermissionDecision.ALLOW)
        elif tool.is_read_only(params):
            return PermissionCheck(PermissionDecision.ALLOW)
        if tool.name in ("Write", "Edit") and self._is_plan_file(params):
            return PermissionCheck(PermissionDecision.ALLOW)
        if tool.name in ("EnterPlanMode", "ExitPlanMode", "AskUserQuestion", "TodoWrite"):
            return PermissionCheck(PermissionDecision.ALLOW)
        return PermissionCheck(
            PermissionDecision.DENY,
            reason="Plan mode: only read operations and plan file edits are allowed.",
        )

    def _check_accept_edits(
        self,
        tool: Tool,
        params: dict[str, Any],
        command: str,
        is_bash: bool,
        ctx: ToolContext,
    ) -> PermissionCheck | None:
        """acceptEdits auto-allow. Dangerous-path safety already handled upstream.

        Returns ALLOW if the action qualifies, else None to keep evaluating.
        """
        if tool.name in ("Write", "Edit"):
            path = params.get("file_path", "")
            if path and self._path_in_cwd(path, ctx.cwd):
                return PermissionCheck(PermissionDecision.ALLOW)
            return None
        if is_bash:
            # Allow if EVERY subcommand's base command is a filesystem command.
            subcommands = self._split_compound(command)
            if subcommands and all(
                self._base_command(sub) in ACCEPT_EDITS_BASH_COMMANDS
                for sub in subcommands
            ):
                return PermissionCheck(PermissionDecision.ALLOW)
            return None
        return None

    # ──────────────────────────────────────────────────────────────────────
    # Rule mutation API (kept stable for other ccos modules)
    # ──────────────────────────────────────────────────────────────────────
    def add_always_allow(self, tool_name: str, pattern: str) -> None:
        self.always_allow.setdefault(tool_name, set()).add(pattern)

    def add_always_deny(self, tool_name: str, pattern: str) -> None:
        self.always_deny.setdefault(tool_name, set()).add(pattern)

    def add_session_allow(self, tool_name: str, pattern: str) -> None:
        """Remember an allow for this session only."""
        self._session_allows.setdefault(tool_name, set()).add(pattern)

    # ──────────────────────────────────────────────────────────────────────
    # Factory helpers for safe sub-contexts (the architectural fix)
    # ──────────────────────────────────────────────────────────────────────
    def for_non_interactive(self) -> "PermissionManager":
        """Return a copy configured so no interactive prompts are ever emitted.

        ASK decisions become DENY. This copy shares the same mode/allow/deny
        rule sets (so persistent config still applies), but it can never block
        on user input. Use this for all forked sub-engines that run while the
        main REPL may be waiting at the PTK ❯ prompt.
        """
        return PermissionManager(
            mode=self.mode,
            always_allow={k: set(v) for k, v in self.always_allow.items()},
            always_deny={k: set(v) for k, v in self.always_deny.items()},
            prompting_allowed=False,
            memory_dir=self.memory_dir,
        )

    def for_memory_extraction(self, memory_dir: str | None = None) -> "PermissionManager":
        """Return a strict non-interactive manager scoped to a memory directory.

        Only Read/Write/Edit/Glob/Grep + read-only Bash are permitted.
        All writes are forced to be inside the memory dir (absolute path check).
        No Agent, no AskUserQuestion, no Web*, no Task*, no Notebook*, no MCP,
        no write-capable Bash, no Edit/Write outside the memory dir.

        prompting_allowed is always False for this variant.
        """
        mem_dir = os.path.abspath(memory_dir) if memory_dir else self.memory_dir
        # Start from a clean slate — do not inherit broad always_allow.
        # Only the memory-specific auto-allow logic inside check() applies.
        mgr = PermissionManager(
            mode=self.mode,
            always_allow={},
            always_deny={},
            prompting_allowed=False,
            memory_dir=mem_dir,
        )
        return mgr

    # ──────────────────────────────────────────────────────────────────────
    # Rule matching (cc shellRuleMatching.ts + filesystem.ts)
    # ──────────────────────────────────────────────────────────────────────
    @staticmethod
    def _is_tool_wide_pattern(pattern: str) -> bool:
        """cc: 'Bash(*)'/'Bash()' collapse to a tool-wide rule."""
        return pattern == "" or pattern == "*"

    def _tool_wide_rule(self, source: dict[str, set[str]] | None, tool_name: str) -> bool:
        if not source or tool_name not in source:
            return False
        return any(self._is_tool_wide_pattern(p) for p in source[tool_name])

    def _matching_content_pattern(
        self,
        source: dict[str, set[str]] | None,
        tool: Tool,
        params: dict[str, Any],
        behavior: str,
    ) -> str | None:
        """Return the first content-scoped pattern that matches, else None.

        Tool-wide ('*'/'') patterns are handled separately by _tool_wide_rule.
        """
        if not source or tool.name not in source:
            return None
        for pattern in source[tool.name]:
            if self._is_tool_wide_pattern(pattern):
                continue
            if self._matches(tool, params, pattern, behavior):
                return pattern
        return None

    def _matches(self, tool: Tool, params: dict[str, Any], pattern: str, behavior: str) -> bool:
        """Tool-aware content matching (replaces naive substring/fnmatch).

        * Bash: parse pattern into exact/prefix/wildcard, split the command on
          operators, require word boundaries. For deny/ask, normalise the
          command by stripping leading env vars + safe wrappers (cc
          stripAllLeadingEnvVars/stripSafeWrappers) so prefixed denied commands
          stay denied. Prefix/wildcard ALLOW rules never match compound commands.
        * Write/Edit/Read: match the resolved file_path as a path glob only.
        * Other tools: glob against string params (conservative fallback).
        """
        if tool.name == "Bash":
            return self._bash_matches(params.get("command", ""), pattern, behavior)
        if tool.name in ("Write", "Edit", "Read"):
            return self._path_matches(params.get("file_path", ""), pattern)
        # Generic fallback: glob each string param. NO unconstrained substring.
        for val in params.values():
            if isinstance(val, str) and fnmatch.fnmatch(val, pattern):
                return True
        return False

    @staticmethod
    def _path_matches(file_path: str, pattern: str) -> bool:
        if not file_path:
            return False
        pat = pattern.replace("\\", "/").rstrip("/") or pattern.replace("\\", "/")
        # Try both the OS-resolved absolute path and the raw normalised input.
        # On Windows os.path.abspath injects a drive letter (C:\...), which would
        # break matching against POSIX-style settings patterns like
        # '/proj/secrets/**'; matching the raw form too keeps rules portable
        # (cc normalises via windowsPathToPosixPath + relative-path comparison).
        candidates = {
            os.path.abspath(file_path).replace("\\", "/"),
            file_path.replace("\\", "/"),
        }
        for resolved in candidates:
            if PermissionManager._single_path_match(resolved, pat):
                return True
        return False

    @staticmethod
    def _single_path_match(resolved: str, pat: str) -> bool:
        # Direct glob.
        if fnmatch.fnmatch(resolved, pat):
            return True
        # gitignore-style 'dir/**' covers the dir itself and everything under it.
        if pat.endswith("/**"):
            base = pat[:-3]
            if fnmatch.fnmatch(resolved, base + "/*") or fnmatch.fnmatch(resolved, base):
                return True
            if resolved == base or resolved.startswith(base + "/"):
                return True
        # Suffix match for relative patterns (e.g. 'secrets/**', '*.env') so a
        # rule authored relative to the project still matches an absolute path.
        if not pat.startswith("/") and "/" in pat:
            suffix_pat = "*/" + pat
            if fnmatch.fnmatch(resolved, suffix_pat):
                return True
            if pat.endswith("/**"):
                base = "*/" + pat[:-3]
                if fnmatch.fnmatch(resolved, base + "/*") or fnmatch.fnmatch(resolved, base):
                    return True
        return False

    # ── Bash rule matching ────────────────────────────────────────────────
    def _bash_matches(self, command: str, pattern: str, behavior: str) -> bool:
        rule_type, rule_value = self._parse_bash_rule(pattern)

        candidates = self._bash_match_candidates(command, behavior)

        for cmd in candidates:
            if rule_type == "exact":
                if cmd == rule_value:
                    return True
                continue
            # prefix / wildcard: reject compound commands for ALLOW rules so a
            # prefix like Bash(cd:*) can't match "cd x && evil". Deny/ask rules
            # intentionally still match the per-subcommand candidates produced
            # by _bash_match_candidates.
            subs = self._split_compound(cmd)
            is_compound = len(subs) > 1
            targets = [cmd] if not is_compound else subs
            for target in targets:
                target = target.strip()
                if rule_type == "prefix":
                    if behavior == "allow" and is_compound:
                        continue
                    if target == rule_value or target.startswith(rule_value + " "):
                        return True
                elif rule_type == "wildcard":
                    if behavior == "allow" and is_compound:
                        continue
                    if self._match_wildcard(rule_value, target):
                        return True
        return False

    def _bash_match_candidates(self, command: str, behavior: str) -> list[str]:
        """Build the set of command strings to match a rule against.

        For deny/ask, add env-var/wrapper-stripped forms (and per-subcommand
        forms) so denied commands cannot be hidden behind prefixes. For allow,
        only the trimmed command (compound handling happens in _bash_matches).
        """
        base = command.strip()
        seen: list[str] = []

        def add(c: str) -> None:
            c = c.strip()
            if c and c not in seen:
                seen.append(c)

        add(base)
        if behavior in ("deny", "ask"):
            # Strip wrappers/env-vars iteratively to a fixed point.
            add(self._strip_bash_prefixes(base))
            # Also evaluate each subcommand of a compound command, stripped.
            for sub in self._split_compound(base):
                add(sub)
                add(self._strip_bash_prefixes(sub))
        return seen

    @staticmethod
    def _parse_bash_rule(pattern: str) -> tuple[str, str]:
        """Parse a stored Bash pattern into (type, value).

        cc parsePermissionRule: 'foo:*' -> prefix 'foo'; contains unescaped '*'
        -> wildcard; otherwise exact.
        """
        if pattern.endswith(":*"):
            return ("prefix", pattern[:-2])
        if "*" in pattern:
            return ("wildcard", pattern)
        return ("exact", pattern)

    @staticmethod
    def _match_wildcard(pattern: str, command: str) -> bool:
        """Match a command against a 'foo *' wildcard (cc matchWildcardPattern)."""
        trimmed = pattern.strip()
        regex = re.escape(trimmed).replace(r"\*", ".*")
        # 'git *' should also match bare 'git' (trailing optional args) when the
        # trailing ' *' is the only wildcard, mirroring cc's optional-tail rule.
        if regex.endswith(r"\ .\*") and trimmed.count("*") == 1:
            regex = regex[: -len(r"\ .\*")] + r"(?:\ .*)?"
        return re.fullmatch(regex, command, re.DOTALL) is not None

    def _strip_bash_prefixes(self, command: str) -> str:
        """Strip leading env-var assignments + safe wrappers to a fixed point."""
        prev = None
        cur = command.strip()
        while cur != prev:
            prev = cur
            # Strip a leading env-var assignment.
            m = _ENV_VAR_ASSIGN_RE.match(cur)
            if m:
                cur = cur[m.end():].strip()
                continue
            # Strip a leading safe wrapper (and its simple flags/args).
            tokens = cur.split()
            if tokens and tokens[0] in _SAFE_WRAPPERS:
                # Drop the wrapper word; also drop a following numeric duration
                # for timeout and -n N for nice (best-effort, mirrors cc intent).
                rest = tokens[1:]
                while rest and (
                    rest[0].startswith("-")
                    or re.fullmatch(r"\d+(?:\.\d+)?[smhd]?", rest[0])
                ):
                    rest = rest[1:]
                cur = " ".join(rest).strip()
                continue
        return cur

    # ──────────────────────────────────────────────────────────────────────
    # Bash structural read-only classification (cc readOnlyValidation.ts)
    # ──────────────────────────────────────────────────────────────────────
    def _bash_is_read_only(self, command: str) -> bool:
        """True only if EVERY subcommand is structurally read-only.

        Never hard-denies. Unclassified commands simply return False, falling
        through to allow-rule / ASK handling in check().
        """
        command = command.strip()
        if not command:
            return False
        subcommands = self._split_compound(command)
        if not subcommands:
            return False
        return all(self._subcommand_is_read_only(sub) for sub in subcommands)

    def _subcommand_is_read_only(self, sub: str) -> bool:
        sub = self._strip_bash_prefixes(sub).strip()
        if not sub:
            return False
        # Reject anything with shell metacharacters that could write/execute,
        # EXCEPT safe output redirections to nul (Windows) or /dev/null.
        # These are commonly used in discovery probes like:
        #   dir /b "path" 2^>nul || echo "empty_or_missing"
        # We strip only the safe redir suffix for the metacharacter scan.
        core = self._strip_safe_redirections(sub)
        if any(ch in core for ch in "`$<>"):
            return False
        tokens = core.split()
        if not tokens:
            return False
        base = tokens[0]

        # git: only read-only subcommands.
        if base == "git":
            if len(tokens) >= 2 and tokens[1] in GIT_READ_ONLY_SUBCOMMANDS:
                return True
            return False

        # Flag-validated commands: every flag token must be in the allowlist.
        if base in FLAG_VALIDATED_COMMANDS:
            allowed = FLAG_VALIDATED_COMMANDS[base]
            for tok in tokens[1:]:
                if tok == "--":
                    break
                if tok.startswith("-"):
                    # Split fused flags like -in -> -i -n only for short combos;
                    # validate the whole token first, then each char.
                    flag = tok.split("=", 1)[0]
                    if flag in allowed:
                        continue
                    if not flag.startswith("--") and len(flag) > 2:
                        # Bundled short flags: each must be allowed.
                        if all(("-" + c) in allowed for c in flag[1:]):
                            continue
                    return False
                # positional arg (pattern/path) — fine for read-only search.
            return True

        # Simple read-only commands (word-boundary base match).
        if base in SIMPLE_READONLY_COMMANDS:
            # 'find' is intentionally NOT in the simple set because of -delete/
            # -exec; only treat it read-only via explicit allowlist below.
            return True

        # find: read-only only when no write/exec actions are present.
        if base == "find":
            dangerous = {"-delete", "-exec", "-execdir", "-ok", "-okdir",
                         "-fprint", "-fprint0", "-fls", "-fprintf"}
            if any(t in dangerous for t in tokens[1:]):
                return False
            return True

        return False

    # ──────────────────────────────────────────────────────────────────────
    # Path helpers
    # ──────────────────────────────────────────────────────────────────────
    @staticmethod
    def _split_compound(command: str) -> list[str]:
        """Split a compound command on &&, ||, ;, |, newline (cc splitCommand)."""
        parts = _COMPOUND_SPLIT_RE.split(command)
        return [p.strip() for p in parts if p.strip()]

    @staticmethod
    def _strip_safe_redirections(cmd: str) -> str:
        """Remove safe output redirections (to nul / /dev/null) for classification.

        This is only used for the metacharacter safety scan and tokenization in
        _subcommand_is_read_only. The original command is still passed to the shell;
        we are just proving it is "read-only plus harmless redirection".
        """
        s = cmd
        for pat in _SAFE_REDIR_PATTERNS:
            s = re.sub(pat, "", s, flags=re.IGNORECASE)
        return s.strip()

    @staticmethod
    def _base_command(subcommand: str) -> str:
        """Return the base command word of a subcommand (after env-var strip)."""
        m = _ENV_VAR_ASSIGN_RE.match(subcommand.strip())
        rest = subcommand[m.end():] if m else subcommand
        tokens = rest.strip().split()
        return tokens[0] if tokens else ""

    @staticmethod
    def _path_in_cwd(path: str, cwd: str) -> bool:
        try:
            abs_path = os.path.abspath(path)
            abs_cwd = os.path.abspath(cwd)
        except (OSError, ValueError):
            return False
        try:
            return os.path.commonpath([abs_path, abs_cwd]) == abs_cwd
        except ValueError:
            # Different drives on Windows, etc.
            return False

    @staticmethod
    def _is_dangerous_path(path: str) -> bool:
        """Case-insensitive check for bypass-immune dangerous files/dirs.

        Mirrors cc filesystem.ts isDangerousFilePathToAutoEdit: returns True if
        any path segment is a dangerous directory or the basename is a dangerous
        file. Comparison is case-insensitive (normalizeCaseForComparison).
        """
        if not path:
            return False
        # UNC / network paths are always treated as dangerous (cc).
        if path.startswith("\\\\") or path.startswith("//"):
            return True
        abs_path = os.path.abspath(path)
        segments = re.split(r"[\\/]+", abs_path)
        lowered = [s.lower() for s in segments if s]
        dangerous_dirs = {d.lower() for d in DANGEROUS_DIRECTORIES}
        for i, seg in enumerate(lowered):
            if seg in dangerous_dirs:
                # cc carve-out: .claude/worktrees/ is structural, not dangerous.
                if seg == ".claude" and i + 1 < len(lowered) and lowered[i + 1] == "worktrees":
                    continue
                return True
        basename = lowered[-1] if lowered else ""
        if basename in {f.lower() for f in DANGEROUS_FILES}:
            return True
        return False

    @staticmethod
    def _is_plan_file(params: dict[str, Any]) -> bool:
        """Check if the target is a plan file."""
        path = params.get("file_path", "")
        if not path:
            return False
        normalized = path.replace("\\", "/")
        return "/plans/" in normalized or normalized.endswith("plan.md")

    def _check_memory_extraction_policy(
        self, tool: Tool, params: dict[str, Any], ctx: ToolContext
    ) -> PermissionCheck | None:
        """Strict allowlist for background memory extraction sub-agents.

        Returns ALLOW if the operation is within policy, DENY if it is
        explicitly out of policy for memory extraction, or None to let the
        caller apply the generic memory-scoped denial message.
        """
        if not self.memory_dir:
            return None

        mem_dir = os.path.abspath(self.memory_dir)
        allowed_write_tools = {"Write", "Edit"}
        allowed_read_tools = {"Read", "Glob", "Grep"}

        # Only these tools are ever interesting for memory extraction.
        if tool.name not in allowed_write_tools | allowed_read_tools | {"Bash"}:
            # Everything else (Agent, AskUserQuestion, Web*, Task*, Notebook*,
            # MCP deferred, etc.) is denied for memory extraction.
            return PermissionCheck(
                PermissionDecision.DENY,
                reason=f"Memory extraction policy denies {tool.name}.",
            )

        if tool.name in allowed_read_tools:
            # Read/Glob/Grep are always OK inside the memory dir (or anywhere,
            # but the extractor prompt already tells it to stay focused).
            # We still force the path check for Write/Edit below; for reads
            # we are permissive to allow the initial ls/scan of the dir.
            return PermissionCheck(PermissionDecision.ALLOW)

        if tool.name in allowed_write_tools:
            path = params.get("file_path", "")
            if not path:
                return PermissionCheck(
                    PermissionDecision.DENY,
                    reason="Memory extraction: Write/Edit without file_path.",
                )
            try:
                abs_path = os.path.abspath(path)
            except Exception:
                return PermissionCheck(
                    PermissionDecision.DENY,
                    reason="Memory extraction: unresolvable path.",
                )
            # Must be inside the memory dir.
            try:
                common = os.path.commonpath([abs_path, mem_dir])
            except ValueError:
                common = ""
            if os.path.normcase(common) != os.path.normcase(mem_dir):
                return PermissionCheck(
                    PermissionDecision.DENY,
                    reason="Memory extraction: Write/Edit must target the memory directory.",
                )
            return PermissionCheck(PermissionDecision.ALLOW)

        if tool.name == "Bash":
            # Only allow if the entire command is structurally read-only
            # (after safe redirection stripping). No write-capable Bash for
            # the memory extractor, even inside the memory dir.
            command = params.get("command", "") or ""
            if self._bash_is_read_only(command):
                return PermissionCheck(PermissionDecision.ALLOW)
            return PermissionCheck(
                PermissionDecision.DENY,
                reason="Memory extraction policy: only read-only Bash is allowed.",
            )

        return None

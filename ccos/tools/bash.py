"""Bash tool — execute shell commands."""

from __future__ import annotations

import asyncio
import os
import subprocess
import tempfile
import time
from typing import Any

from ccos.tools.base import Tool, ToolContext, ToolOutput
from ccos.utils.platform_info import get_platform

_MAX_TIMEOUT_MS = 600_000  # 10 minutes
_DEFAULT_TIMEOUT_MS = 120_000  # 2 minutes
_MAX_RESULT_CHARS = 30_000

# Commands generally considered read-only
_READ_ONLY_PREFIXES = (
    "ls", "dir", "cat", "head", "tail", "less", "more",
    "echo", "printf", "pwd", "whoami", "which", "where",
    "git status", "git log", "git diff", "git show", "git branch",
    "git remote", "git tag", "git stash list",
    "find", "wc", "file", "stat", "du", "df",
    "grep", "rg", "ag", "ack",
    "python --version", "python3 --version", "node --version",
    "npm list", "pip list", "pip show",
    "type", "test", "true", "false",
    "date", "uname", "hostname", "env", "printenv",
)


class BashTool(Tool):
    name = "Bash"
    description = (
        "Executes a given bash command and returns its output.\n"
        "\n"
        "The working directory persists between commands, but shell state does not. "
        "The shell environment is initialized from the user's profile (bash or zsh).\n"
        "\n"
        "IMPORTANT: Avoid using this tool to run `find`, `grep`, `cat`, `head`, `tail`, "
        "`sed`, `awk`, or `echo` commands, unless explicitly instructed or after you have "
        "verified that a dedicated tool cannot accomplish your task. Instead, use the "
        "appropriate dedicated tool as this will provide a much better experience for the user:\n"
        "- File search: Use Glob (NOT find or ls)\n"
        "- Content search: Use Grep (NOT grep or rg)\n"
        "- Read files: Use Read (NOT cat/head/tail)\n"
        "- Edit files: Use Edit (NOT sed/awk)\n"
        "- Write files: Use Write (NOT echo >/cat <<EOF)\n"
        "- Communication: Output text directly (NOT echo/printf)\n"
        "While the Bash tool can do similar things, it's better to use the built-in tools "
        "as they provide a better user experience and make it easier to review tool calls "
        "and give permission.\n"
        "\n"
        "# Instructions\n"
        "- If your command will create new directories or files, first use this tool to run "
        "`ls` to verify the parent directory exists and is the correct location.\n"
        "- Always quote file paths that contain spaces with double quotes in your command "
        "(e.g., cd \"path with spaces/file.txt\")\n"
        "- Try to maintain your current working directory throughout the session by using "
        "absolute paths and avoiding usage of `cd`. You may use `cd` if the User explicitly requests it.\n"
        f"- You may specify an optional timeout in milliseconds (up to {_MAX_TIMEOUT_MS}ms / "
        f"{_MAX_TIMEOUT_MS // 60000} minutes). By default, your command will timeout after "
        f"{_DEFAULT_TIMEOUT_MS}ms ({_DEFAULT_TIMEOUT_MS // 60000} minutes).\n"
        "- You can use the `run_in_background` parameter to run the command in the background. "
        "Only use this if you don't need the result immediately and are OK being notified when "
        "the command completes later. You do not need to check the output right away - you'll be "
        "notified when it finishes. You do not need to use '&' at the end of the command when "
        "using this parameter.\n"
        "- When issuing multiple commands:\n"
        "  - If the commands are independent and can run in parallel, make multiple Bash tool "
        "calls in a single message. Example: if you need to run \"git status\" and \"git diff\", "
        "send a single message with two Bash tool calls in parallel.\n"
        "  - If the commands depend on each other and must run sequentially, use a single Bash "
        "call with '&&' to chain them together.\n"
        "  - Use ';' only when you need to run commands sequentially but don't care if earlier "
        "commands fail.\n"
        "  - DO NOT use newlines to separate commands (newlines are ok in quoted strings).\n"
        "- For git commands:\n"
        "  - Prefer to create a new commit rather than amending an existing commit.\n"
        "  - Before running destructive operations (e.g., git reset --hard, git push --force, "
        "git checkout --), consider whether there is a safer alternative that achieves the same "
        "goal. Only use destructive operations when they are truly the best approach.\n"
        "  - Never skip hooks (--no-verify) or bypass signing (--no-gpg-sign, -c commit.gpgsign=false) "
        "unless the user has explicitly asked for it. If a hook fails, investigate and fix the "
        "underlying issue.\n"
        "- Avoid unnecessary `sleep` commands:\n"
        "  - Do not sleep between commands that can run immediately — just run them.\n"
        "  - If your command is long running and you would like to be notified when it finishes — "
        "use `run_in_background`. No sleep needed.\n"
        "  - Do not retry failing commands in a sleep loop — diagnose the root cause.\n"
        "  - If waiting for a background task you started with `run_in_background`, you will be "
        "notified when it completes — do not poll.\n"
        "  - If you must poll an external process, use a check command (e.g. `gh run view`) rather "
        "than sleeping first.\n"
        "  - If you must sleep, keep the duration short (1-5 seconds) to avoid blocking the user."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "The command to execute",
            },
            "timeout": {
                "type": "integer",
                "description": f"Optional timeout in milliseconds (max {_MAX_TIMEOUT_MS})",
            },
            "description": {
                "type": "string",
                "description": (
                    "Clear, concise description of what this command does in active voice. "
                    "Never use words like \"complex\" or \"risk\" in the description - just "
                    "describe what it does.\n"
                    "\n"
                    "For simple commands (git, npm, standard CLI tools), keep it brief (5-10 words):\n"
                    "- ls → \"List files in current directory\"\n"
                    "- git status → \"Show working tree status\"\n"
                    "- npm install → \"Install package dependencies\"\n"
                    "\n"
                    "For commands that are harder to parse at a glance (piped commands, obscure "
                    "flags, etc.), add enough context to clarify what it does:\n"
                    "- find . -name \"*.tmp\" -exec rm {} \\; → \"Find and delete all .tmp files recursively\"\n"
                    "- git reset --hard origin/main → \"Discard all local changes and match remote main\"\n"
                    "- curl -s url | jq '.data[]' → \"Fetch JSON from URL and extract data array elements\""
                ),
            },
            "run_in_background": {
                "type": "boolean",
                "description": "Set to true to run this command in the background. Use Read to read the output later.",
            },
        },
        "required": ["command"],
        "additionalProperties": False,
    }

    def is_read_only(self, params: dict[str, Any]) -> bool:
        cmd = params.get("command", "").strip()
        cmd_lower = cmd.lower()
        return any(cmd_lower.startswith(p) for p in _READ_ONLY_PREFIXES)

    async def execute(self, params: dict[str, Any], ctx: ToolContext) -> ToolOutput:
        command = params["command"]
        timeout_ms = min(params.get("timeout", _DEFAULT_TIMEOUT_MS), _MAX_TIMEOUT_MS)
        run_in_background = params.get("run_in_background", False)

        timeout_s = timeout_ms / 1000.0

        if run_in_background:
            shell_cmd = self._build_shell_cmd(command)
            return await self._run_background(shell_cmd, command, ctx)

        # Use subprocess.run in a thread executor for maximum compatibility.
        # asyncio.create_subprocess_exec has known issues on Windows
        # (ProactorEventLoop + pipes can silently fail).
        loop = asyncio.get_event_loop()
        try:
            result = await asyncio.wait_for(
                loop.run_in_executor(
                    None,
                    lambda: self._run_sync(command, ctx.cwd, timeout_s),
                ),
                timeout=timeout_s + 5,  # small buffer over inner timeout
            )
            return result
        except asyncio.TimeoutError:
            return ToolOutput(
                content=f"Command timed out after {timeout_s:.0f}s. Consider using run_in_background=true for long-running commands.",
                is_error=True,
            )
        except Exception as e:
            return ToolOutput(
                content=f"Error executing command: {type(e).__name__}: {e!r}",
                is_error=True,
            )

    @staticmethod
    def _run_sync(command: str, cwd: str, timeout_s: float) -> ToolOutput:
        """Run a command synchronously using subprocess.run (thread-safe)."""
        try:
            proc = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                cwd=cwd,
                timeout=timeout_s,
                env={**os.environ},
            )

            stdout = proc.stdout.decode("utf-8", errors="replace") if proc.stdout else ""
            stderr = proc.stderr.decode("utf-8", errors="replace") if proc.stderr else ""

            # Combine stdout + stderr
            output = stdout
            if stderr:
                output = f"{stdout}\n{stderr}".strip() if stdout else stderr

            # Truncate if too large
            if len(output) > _MAX_RESULT_CHARS:
                output = output[:_MAX_RESULT_CHARS] + f"\n\n... (output truncated, {len(output)} total characters)"

            if proc.returncode != 0:
                if output:
                    output = f"{output}\n\nExit code: {proc.returncode}"
                else:
                    output = f"Exit code: {proc.returncode}"

            return ToolOutput(content=output or "(no output)")

        except subprocess.TimeoutExpired:
            return ToolOutput(
                content=f"Command timed out after {timeout_s:.0f}s. Consider using run_in_background=true for long-running commands.",
                is_error=True,
            )
        except FileNotFoundError as e:
            return ToolOutput(
                content=f"Error: Shell not found ({e}). Ensure bash or cmd is available.",
                is_error=True,
            )
        except Exception as e:
            return ToolOutput(
                content=f"Error executing command: {type(e).__name__}: {e!r}",
                is_error=True,
            )

    @staticmethod
    def _build_shell_cmd(command: str) -> list[str]:
        """Build shell command list for background execution."""
        if get_platform() == "windows":
            # Prefer bash (Git Bash) if available, fallback to cmd
            try:
                subprocess.run(["bash", "--version"], capture_output=True, timeout=5)
                return ["bash", "-c", command]
            except (FileNotFoundError, subprocess.TimeoutExpired):
                return ["cmd", "/c", command]
        return ["bash", "-c", command]

    async def _run_background(
        self,
        shell_cmd: list[str],
        command: str,
        ctx: ToolContext,
    ) -> ToolOutput:
        task_id = f"bg_{ctx.next_task_id}"
        ctx.next_task_id += 1

        # Create output file
        output_dir = os.path.join(ctx.cwd, ".ccos_tasks")
        os.makedirs(output_dir, exist_ok=True)
        output_file = os.path.join(output_dir, f"{task_id}.log")

        try:
            f = open(output_file, "w", encoding="utf-8")
            proc = await asyncio.create_subprocess_exec(
                *shell_cmd,
                stdout=f,
                stderr=asyncio.subprocess.STDOUT,
                cwd=ctx.cwd,
            )
            ctx.background_tasks[task_id] = {
                "pid": proc.pid,
                "command": command,
                "output_file": output_file,
                "file_handle": f,
                "process": proc,
                "started": time.time(),
            }
            return ToolOutput(
                content=(
                    f"Command started in background.\n"
                    f"Task ID: {task_id}\n"
                    f"Output file: {output_file}\n"
                    f"Use the Read tool on the output file to check progress."
                )
            )
        except Exception as e:
            return ToolOutput(content=f"Error starting background command: {e}", is_error=True)

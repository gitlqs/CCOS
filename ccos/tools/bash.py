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
        "Executes a given bash command and returns its output.\n\n"
        "The working directory persists between commands, but shell state does not.\n\n"
        "IMPORTANT: Avoid using this tool for tasks that dedicated tools handle better:\n"
        "- File search: Use Glob (NOT find or ls)\n"
        "- Content search: Use Grep (NOT grep or rg)\n"
        "- Read files: Use Read (NOT cat/head/tail)\n"
        "- Edit files: Use Edit (NOT sed/awk)\n"
        "- Write files: Use Write (NOT echo >/cat <<EOF)\n\n"
        "Reserve Bash exclusively for system commands and terminal operations that require shell execution."
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
                "description": "Clear, concise description of what this command does",
            },
            "run_in_background": {
                "type": "boolean",
                "description": "Set to true to run this command in the background.",
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

        # Determine shell
        if get_platform() == "windows":
            shell_cmd = ["bash", "-c", command]
            # Fallback to cmd if bash isn't available
            try:
                subprocess.run(["bash", "--version"], capture_output=True, timeout=5)
            except (FileNotFoundError, subprocess.TimeoutExpired):
                shell_cmd = ["cmd", "/c", command]
        else:
            shell_cmd = ["bash", "-c", command]

        if run_in_background:
            return await self._run_background(shell_cmd, command, ctx)

        try:
            proc = await asyncio.wait_for(
                asyncio.create_subprocess_exec(
                    *shell_cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                    cwd=ctx.cwd,
                    env={**os.environ},
                ),
                timeout=10,  # timeout for process creation
            )

            try:
                stdout_bytes, _ = await asyncio.wait_for(
                    proc.communicate(),
                    timeout=timeout_s,
                )
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                return ToolOutput(
                    content=f"Command timed out after {timeout_s:.0f}s. Consider using run_in_background=true for long-running commands.",
                    is_error=True,
                )

            output = stdout_bytes.decode("utf-8", errors="replace") if stdout_bytes else ""

            # Truncate if too large
            if len(output) > _MAX_RESULT_CHARS:
                truncated = output[:_MAX_RESULT_CHARS]
                output = truncated + f"\n\n... (output truncated, {len(output)} total characters)"

            if proc.returncode != 0:
                output = f"{output}\n\nExit code: {proc.returncode}" if output else f"Exit code: {proc.returncode}"

            return ToolOutput(content=output or "(no output)")

        except FileNotFoundError:
            return ToolOutput(
                content="Error: Shell not found. Ensure bash or cmd is available.",
                is_error=True,
            )
        except Exception as e:
            return ToolOutput(content=f"Error executing command: {e}", is_error=True)

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

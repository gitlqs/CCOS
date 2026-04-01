"""PowerShell tool -- execute PowerShell commands on Windows."""

from __future__ import annotations

import asyncio
import os
import sys
from typing import Any

from ccos.tools.base import Tool, ToolContext, ToolOutput

_MAX_TIMEOUT_MS = 600_000
_DEFAULT_TIMEOUT_MS = 120_000
_MAX_RESULT_CHARS = 30_000


class PowerShellTool(Tool):
    name = "PowerShell"
    description = (
        "Execute PowerShell commands on Windows.\n\n"
        "Use this tool for Windows-specific operations that require PowerShell cmdlets.\n"
        "For cross-platform commands, prefer the Bash tool instead."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "The PowerShell command to execute",
            },
            "timeout": {
                "type": "integer",
                "description": f"Optional timeout in milliseconds (max {_MAX_TIMEOUT_MS})",
            },
        },
        "required": ["command"],
        "additionalProperties": False,
    }

    def is_read_only(self, params: dict[str, Any]) -> bool:
        cmd = params.get("command", "").strip().lower()
        read_only_starts = (
            "get-", "test-", "select-", "where-", "measure-",
            "format-", "out-string", "convertto-", "write-host",
            "echo", "$env:", "dir", "ls", "cat", "type",
        )
        return any(cmd.startswith(p) for p in read_only_starts)

    async def execute(self, params: dict[str, Any], ctx: ToolContext) -> ToolOutput:
        if sys.platform != "win32":
            return ToolOutput(
                content="Error: PowerShell tool is only available on Windows. Use Bash instead.",
                is_error=True,
            )

        command = params["command"]
        timeout_ms = min(params.get("timeout", _DEFAULT_TIMEOUT_MS), _MAX_TIMEOUT_MS)
        timeout_s = timeout_ms / 1000.0

        try:
            proc = await asyncio.create_subprocess_exec(
                "powershell", "-NoProfile", "-NonInteractive", "-Command", command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd=ctx.cwd,
            )
            try:
                stdout_bytes, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                return ToolOutput(content=f"Command timed out after {timeout_s:.0f}s", is_error=True)

            output = stdout_bytes.decode("utf-8", errors="replace") if stdout_bytes else ""

            if len(output) > _MAX_RESULT_CHARS:
                output = output[:_MAX_RESULT_CHARS] + f"\n\n... (truncated, {len(output)} total chars)"

            if proc.returncode != 0:
                output = f"{output}\n\nExit code: {proc.returncode}" if output else f"Exit code: {proc.returncode}"

            return ToolOutput(content=output or "(no output)")

        except FileNotFoundError:
            return ToolOutput(content="Error: PowerShell not found.", is_error=True)
        except Exception as e:
            return ToolOutput(content=f"Error: {e}", is_error=True)

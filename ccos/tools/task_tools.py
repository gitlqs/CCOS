"""Task management tools -- TaskOutput, TaskStop for background task control."""

from __future__ import annotations

import asyncio
import time
from typing import Any

from ccos.tools.base import Tool, ToolContext, ToolOutput


# cc TaskOutputTool description() + DEPRECATED prompt() (TaskOutputTool.tsx), verbatim.
_TASK_OUTPUT_DESCRIPTION = (
    "[Deprecated] — prefer Read on the task output file path\n\n"
    "DEPRECATED: Prefer using the Read tool on the task's output file path "
    "instead. Background tasks return their output file path in the tool result, "
    "and you receive a <task-notification> with the same path when the task "
    "completes — Read that file directly.\n\n"
    "- Retrieves output from a running or completed task (background shell, "
    "agent, or remote session)\n"
    "- Takes a task_id parameter identifying the task\n"
    "- Returns the task output along with status information\n"
    "- Use block=true (default) to wait for task completion\n"
    "- Use block=false for non-blocking check of current status\n"
    "- Task IDs can be found using the /tasks command\n"
    "- Works with all task types: background shells, async agents, and remote "
    "sessions"
)


# cc TaskStopTool description() + DESCRIPTION prompt() (TaskStopTool/prompt.ts), verbatim.
_TASK_STOP_DESCRIPTION = (
    "Stop a running background task by ID\n"
    "\n"
    "- Stops a running background task by its ID\n"
    "- Takes a task_id parameter identifying the task to stop\n"
    "- Returns a success or failure status\n"
    "- Use this tool when you need to terminate a long-running task\n"
)


class TaskOutputTool(Tool):
    name = "TaskOutput"
    description = _TASK_OUTPUT_DESCRIPTION
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "task_id": {
                "type": "string",
                "description": "The task ID to get output from",
            },
            "block": {
                "type": "boolean",
                "default": True,
                "description": "Whether to wait for completion",
            },
            "timeout": {
                "type": "number",
                "minimum": 0,
                "maximum": 600000,
                "default": 30000,
                "description": "Max wait time in ms",
            },
        },
        "required": ["task_id"],
        "additionalProperties": False,
    }

    def is_read_only(self, params: dict[str, Any]) -> bool:
        return True

    async def execute(self, params: dict[str, Any], ctx: ToolContext) -> ToolOutput:
        task_id = params["task_id"]
        block = params.get("block", True)
        timeout_ms = params.get("timeout", 30000)
        task_info = ctx.background_tasks.get(task_id)

        if task_info is None:
            return ToolOutput(content=f"Error: No background task with ID: {task_id}", is_error=True)

        # When block=true, wait for the task to complete up to timeout ms.
        if block:
            await self._wait_for_completion(task_info, timeout_ms)

        # For agent tasks
        if task_info.get("type") == "agent":
            atask = task_info.get("task")
            if atask and atask.done():
                try:
                    result = atask.result()
                    return ToolOutput(content=f"Agent completed:\n{result}")
                except Exception as e:
                    return ToolOutput(content=f"Agent failed: {e}", is_error=True)
            return ToolOutput(content=f"Agent '{task_info.get('description', task_id)}' is still running...")

        # For bash tasks -- read output file
        output_file = task_info.get("output_file")
        if output_file:
            import os
            if os.path.exists(output_file):
                try:
                    with open(output_file, "r", encoding="utf-8", errors="replace") as f:
                        content = f.read()
                    proc = task_info.get("process")
                    if proc and proc.returncode is not None:
                        content += f"\n\n(Process exited with code {proc.returncode})"
                        # Clean up file handle
                        fh = task_info.get("file_handle")
                        if fh:
                            fh.close()
                    elif proc:
                        content += "\n\n(Process still running...)"
                    return ToolOutput(content=content or "(no output yet)")
                except Exception as e:
                    return ToolOutput(content=f"Error reading output: {e}", is_error=True)

        return ToolOutput(content=f"Task {task_id}: no output available yet")

    async def _wait_for_completion(self, task_info: dict[str, Any], timeout_ms: float) -> None:
        """Wait up to timeout_ms for the task to finish (best-effort)."""
        deadline = time.monotonic() + (timeout_ms / 1000.0)
        while time.monotonic() < deadline:
            if task_info.get("type") == "agent":
                atask = task_info.get("task")
                if atask is None or atask.done():
                    return
            else:
                proc = task_info.get("process")
                if proc is None or proc.returncode is not None:
                    return
            await asyncio.sleep(0.1)


class TaskStopTool(Tool):
    name = "TaskStop"
    description = _TASK_STOP_DESCRIPTION
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "task_id": {
                "type": "string",
                "description": "The ID of the background task to stop",
            },
            # shell_id is accepted for backward compatibility with the
            # deprecated KillShell tool.
            "shell_id": {
                "type": "string",
                "description": "Deprecated: use task_id instead",
            },
        },
        "required": [],
        "additionalProperties": False,
    }

    async def execute(self, params: dict[str, Any], ctx: ToolContext) -> ToolOutput:
        # Support both task_id and shell_id (deprecated KillShell compat).
        task_id = params.get("task_id") or params.get("shell_id")
        if not task_id:
            return ToolOutput(content="Missing required parameter: task_id", is_error=True)

        task_info = ctx.background_tasks.get(task_id)

        if task_info is None:
            return ToolOutput(content=f"Error: No background task with ID: {task_id}", is_error=True)

        # Agent task
        if task_info.get("type") == "agent":
            command = task_info.get("description", task_id)
            atask = task_info.get("task")
            if atask and not atask.done():
                atask.cancel()
                return ToolOutput(
                    content=f"Successfully stopped task: {task_id} ({command})"
                )
            return ToolOutput(content=f"Agent task {task_id} already completed.")

        # Bash task
        command = task_info.get("command", task_id)
        proc = task_info.get("process")
        if proc:
            try:
                proc.kill()
                await proc.wait()
            except Exception:
                pass
            fh = task_info.get("file_handle")
            if fh:
                fh.close()
            return ToolOutput(
                content=f"Successfully stopped task: {task_id} ({command})"
            )

        return ToolOutput(content=f"Task {task_id}: nothing to stop")

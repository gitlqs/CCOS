"""Task management tools -- TaskOutput, TaskStop for background task control."""

from __future__ import annotations

import asyncio
from typing import Any

from ccos.tools.base import Tool, ToolContext, ToolOutput


class TaskOutputTool(Tool):
    name = "TaskOutput"
    description = (
        "Read the output of a background task.\n\n"
        "Use this to check on tasks that were started with run_in_background=true."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "task_id": {
                "type": "string",
                "description": "The ID of the background task to check",
            },
        },
        "required": ["task_id"],
        "additionalProperties": False,
    }

    def is_read_only(self, params: dict[str, Any]) -> bool:
        return True

    async def execute(self, params: dict[str, Any], ctx: ToolContext) -> ToolOutput:
        task_id = params["task_id"]
        task_info = ctx.background_tasks.get(task_id)

        if task_info is None:
            return ToolOutput(content=f"Error: No background task with ID: {task_id}", is_error=True)

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


class TaskStopTool(Tool):
    name = "TaskStop"
    description = (
        "Stop a running background task.\n\n"
        "Use this to terminate tasks that are no longer needed or are stuck."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "task_id": {
                "type": "string",
                "description": "The ID of the background task to stop",
            },
        },
        "required": ["task_id"],
        "additionalProperties": False,
    }

    async def execute(self, params: dict[str, Any], ctx: ToolContext) -> ToolOutput:
        task_id = params["task_id"]
        task_info = ctx.background_tasks.get(task_id)

        if task_info is None:
            return ToolOutput(content=f"Error: No background task with ID: {task_id}", is_error=True)

        # Agent task
        if task_info.get("type") == "agent":
            atask = task_info.get("task")
            if atask and not atask.done():
                atask.cancel()
                return ToolOutput(content=f"Agent task {task_id} cancelled.")
            return ToolOutput(content=f"Agent task {task_id} already completed.")

        # Bash task
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
            return ToolOutput(content=f"Background task {task_id} stopped.")

        return ToolOutput(content=f"Task {task_id}: nothing to stop")

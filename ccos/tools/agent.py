"""Agent tool -- spawn sub-agents for complex, multi-step tasks."""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

from ccos.tools.base import Tool, ToolContext, ToolOutput


class AgentTool(Tool):
    name = "Agent"
    description = (
        "Launch a new agent to handle complex, multi-step tasks autonomously.\n\n"
        "The Agent tool launches specialized sub-agents that work independently.\n"
        "Each agent gets its own conversation context and can use all available tools.\n\n"
        "Usage notes:\n"
        "- Always include a short description (3-5 words) summarizing what the agent will do\n"
        "- Launch multiple agents concurrently when tasks are independent\n"
        "- The agent's result is returned as a single message back to you\n"
        "- Provide clear, detailed prompts so the agent can work autonomously"
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "description": {
                "type": "string",
                "description": "A short (3-5 word) description of the task",
            },
            "prompt": {
                "type": "string",
                "description": "The task for the agent to perform",
            },
            "model": {
                "type": "string",
                "enum": ["sonnet", "opus", "haiku"],
                "description": "Optional model override for this agent.",
            },
            "run_in_background": {
                "type": "boolean",
                "description": "Set to true to run this agent in the background.",
            },
        },
        "required": ["description", "prompt"],
        "additionalProperties": False,
    }

    def __init__(self, engine_factory: Any = None):
        """engine_factory: callable(model) -> QueryEngine for sub-agents."""
        self._engine_factory = engine_factory

    async def execute(self, params: dict[str, Any], ctx: ToolContext) -> ToolOutput:
        prompt = params["prompt"]
        description = params.get("description", "sub-agent")
        model_hint = params.get("model")
        run_bg = params.get("run_in_background", False)

        if self._engine_factory is None:
            return ToolOutput(
                content="Error: Agent tool not configured. No engine factory provided.",
                is_error=True,
            )

        # Resolve model
        model_map = {
            "sonnet": "claude-sonnet-4-6",
            "opus": "claude-opus-4-6",
            "haiku": "claude-haiku-4-5-20251001",
        }
        model = model_map.get(model_hint, "") if model_hint else ""

        try:
            sub_engine = self._engine_factory(model_override=model)
            if run_bg:
                agent_id = f"agent_{uuid.uuid4().hex[:8]}"
                task = asyncio.create_task(sub_engine.run_turn(prompt))
                ctx.background_tasks[agent_id] = {
                    "type": "agent",
                    "description": description,
                    "task": task,
                }
                return ToolOutput(
                    content=(
                        f"Agent '{description}' launched in background.\n"
                        f"Agent ID: {agent_id}\n"
                        f"The agent is working autonomously. You will be notified when it completes."
                    )
                )
            else:
                result = await sub_engine.run_turn(prompt)
                return ToolOutput(content=result or "(Agent returned no output)")
        except Exception as e:
            return ToolOutput(content=f"Agent error: {e}", is_error=True)

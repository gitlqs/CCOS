"""Agent tool -- spawn sub-agents for complex, multi-step tasks."""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

from ccos.tools.base import Tool, ToolContext, ToolOutput


# cc AgentTool description() + the shared body and Usage notes from
# AgentTool/prompt.ts (non-coordinator, non-fork external branch), verbatim
# English. CCOS exposes a single general-purpose agent, so the dynamic
# agent-type listing is replaced with a static line. The git-worktree
# `isolation` usage note is omitted because CCOS does not support worktree
# isolation for sub-agents (see AGENT-05). The SendMessage continuation note is
# omitted because CCOS sub-agents always start fresh.
_DESCRIPTION = (
    "Launch a new agent to handle complex, multi-step tasks autonomously.\n\n"
    "The Agent tool launches specialized agents (subprocesses) that autonomously "
    "handle complex tasks. Each agent type has specific capabilities and tools "
    "available to it.\n\n"
    "Available agent types and the tools they have access to:\n"
    "- general-purpose: General-purpose agent for researching complex questions, "
    "searching for code, and executing multi-step tasks. (Tools: All tools)\n\n"
    "When using the Agent tool, specify a subagent_type parameter to select which "
    "agent type to use. If omitted, the general-purpose agent is used.\n\n"
    "When NOT to use the Agent tool:\n"
    "- If you want to read a specific file path, use the Read tool or Glob tool "
    "instead of the Agent tool, to find the match more quickly\n"
    '- If you are searching for a specific class definition like "class Foo", use '
    "the Glob tool instead, to find the match more quickly\n"
    "- If you are searching for code within a specific file or set of 2-3 files, "
    "use the Read tool instead of the Agent tool, to find the match more quickly\n"
    "- Other tasks that are not related to the agent descriptions above\n\n"
    "Usage notes:\n"
    "- Always include a short description (3-5 words) summarizing what the agent "
    "will do\n"
    "- Launch multiple agents concurrently whenever possible, to maximize "
    "performance; to do that, use a single message with multiple tool uses\n"
    "- When the agent is done, it will return a single message back to you. The "
    "result returned by the agent is not visible to the user. To show the user "
    "the result, you should send a text message back to the user with a concise "
    "summary of the result.\n"
    "- You can optionally run agents in the background using the run_in_background "
    "parameter. When an agent runs in the background, you will be automatically "
    "notified when it completes — do NOT sleep, poll, or proactively check on its "
    "progress. Continue with other work or respond to the user instead.\n"
    "- **Foreground vs background**: Use foreground (default) when you need the "
    "agent's results before you can proceed — e.g., research agents whose findings "
    "inform your next steps. Use background when you have genuinely independent "
    "work to do in parallel.\n"
    "- Each Agent invocation starts fresh — provide a complete task description.\n"
    "- The agent's outputs should generally be trusted\n"
    "- Clearly tell the agent whether you expect it to write code or just to do "
    "research (search, file reads, web fetches, etc.), since it is not aware of "
    "the user's intent\n"
    "- If the agent description mentions that it should be used proactively, then "
    "you should try your best to use it without the user having to ask for it "
    "first. Use your judgement.\n"
    '- If the user specifies that they want you to run agents "in parallel", you '
    "MUST send a single message with multiple Agent tool use content blocks. For "
    "example, if you need to launch both a build-validator agent and a test-runner "
    "agent in parallel, send a single message with both tool calls."
)


class AgentTool(Tool):
    name = "Agent"
    description = _DESCRIPTION
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
            "subagent_type": {
                "type": "string",
                "description": "The type of specialized agent to use for this task",
            },
            "model": {
                "type": "string",
                "enum": ["sonnet", "opus", "haiku"],
                "description": (
                    "Optional model override for this agent. Takes precedence over "
                    "the agent definition's model frontmatter. If omitted, uses the "
                    "agent definition's model, or inherits from the parent."
                ),
            },
            "run_in_background": {
                "type": "boolean",
                "description": (
                    "Set to true to run this agent in the background. You will be "
                    "notified when it completes."
                ),
            },
        },
        "required": ["description", "prompt"],
        "additionalProperties": False,
    }

    # Default agent type when subagent_type is omitted (cc call() default).
    DEFAULT_AGENT_TYPE = "general-purpose"

    def __init__(self, engine_factory: Any = None):
        """engine_factory: callable(model) -> QueryEngine for sub-agents."""
        self._engine_factory = engine_factory

    def _resolve_model(self, model_hint: str | None) -> str:
        """Resolve a sonnet/opus/haiku alias to a concrete model id.

        The alias enum is the provider-abstraction boundary. These aliases are
        Anthropic-specific, so they map to current Anthropic model ids; an
        unknown/omitted hint resolves to "" which makes the engine factory
        inherit the parent's model (and provider), preserving the multi-provider
        abstraction for non-Anthropic parents.
        """
        if not model_hint:
            return ""
        alias_map = {
            "sonnet": "claude-sonnet-4-6",
            "opus": "claude-opus-4-8",
            "haiku": "claude-haiku-4-5",
        }
        return alias_map.get(model_hint, "")

    async def execute(self, params: dict[str, Any], ctx: ToolContext) -> ToolOutput:
        prompt = params["prompt"]
        description = params.get("description", "sub-agent")
        model_hint = params.get("model")
        run_bg = params.get("run_in_background", False)
        # cc defaults subagent_type to the general-purpose agent when omitted.
        subagent_type = params.get("subagent_type") or self.DEFAULT_AGENT_TYPE

        if self._engine_factory is None:
            return ToolOutput(
                content="Error: Agent tool not configured. No engine factory provided.",
                is_error=True,
            )

        # Resolve model alias to a concrete id (or "" to inherit the parent).
        model = self._resolve_model(model_hint)

        try:
            sub_engine = self._engine_factory(model_override=model)
            if run_bg:
                agent_id = f"agent_{uuid.uuid4().hex[:8]}"
                task = asyncio.create_task(sub_engine.run_turn(prompt))
                ctx.background_tasks[agent_id] = {
                    "type": "agent",
                    "description": description,
                    "subagent_type": subagent_type,
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

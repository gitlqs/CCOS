"""ToolSearch — resolve deferred tool schemas on demand.

In CC, some tools are "deferred" — the model sees their name but not their
full schema until it fetches them via ToolSearch. This allows a large number
of tools (MCP tools, etc.) to be exposed without bloating every API call.
"""

from __future__ import annotations

from typing import Any

from ccos.tools.base import Tool, ToolContext, ToolOutput


class ToolSearchTool(Tool):
    name = "ToolSearch"
    description = (
        "Fetches full schema definitions for deferred tools so they can be called.\n\n"
        "Deferred tools appear by name in <available-deferred-tools> messages. Until fetched, "
        "only the name is known — there is no parameter schema, so the tool cannot be invoked. "
        "This tool takes a query, matches it against the deferred tool list, and returns the "
        "matched tools' complete JSONSchema definitions inside a <functions> block. Once a "
        "tool's schema appears in that result, it is callable exactly like any tool defined "
        "at the top of the prompt.\n\n"
        "Query forms:\n"
        '- "select:Read,Edit,Grep" — fetch these exact tools by name\n'
        '- "notebook jupyter" — keyword search, up to max_results best matches\n'
        '- "+slack send" — require "slack" in the name, rank by remaining terms'
    )
    input_schema = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": (
                    'Query to find deferred tools. Use "select:<tool_name>" for '
                    "direct selection, or keywords to search."
                ),
            },
            "max_results": {
                "type": "number",
                "description": "Maximum number of results to return (default: 5)",
                "default": 5,
            },
        },
        "required": ["query"],
    }
    is_read_only_default = True

    def __init__(self) -> None:
        self._deferred_tools: dict[str, Tool] = {}
        self._tool_registry: Any = None  # Set by App to enable activation

    def register_deferred(self, tool: Tool) -> None:
        """Register a tool as deferred — schema available via ToolSearch only."""
        self._deferred_tools[tool.name] = tool

    @property
    def deferred_names(self) -> list[str]:
        return list(self._deferred_tools.keys())

    def _activate_tool(self, tool: Tool) -> None:
        """Move a deferred tool into the main registry so it can be called."""
        if self._tool_registry is not None:
            self._tool_registry.register(tool)

    async def execute(self, params: dict[str, Any], ctx: ToolContext) -> ToolOutput:
        query = params.get("query", "")
        max_results = int(params.get("max_results", 5))

        if not query:
            return ToolOutput(
                content="Please provide a query to search for tools.",
                is_error=True,
            )

        matched: list[Tool] = []

        # Direct selection: "select:ToolA,ToolB"
        if query.startswith("select:"):
            names = [n.strip() for n in query[7:].split(",")]
            for name in names:
                if name in self._deferred_tools:
                    matched.append(self._deferred_tools[name])

        # Keyword search with required prefix: "+keyword rest"
        elif query.startswith("+"):
            parts = query[1:].split(None, 1)
            required = parts[0].lower() if parts else ""
            rest = parts[1].lower() if len(parts) > 1 else ""
            for name, tool in self._deferred_tools.items():
                if required in name.lower():
                    if not rest or rest in (tool.description or "").lower():
                        matched.append(tool)

        # Fuzzy keyword search
        else:
            keywords = query.lower().split()
            for name, tool in self._deferred_tools.items():
                text = f"{name} {tool.description}".lower()
                if all(kw in text for kw in keywords):
                    matched.append(tool)

        if not matched:
            available = ", ".join(sorted(self._deferred_tools.keys()))
            return ToolOutput(
                content=f"No tools matched query '{query}'. Available deferred tools: {available}",
            )

        # Activate matched tools so they become callable
        matched = matched[:max_results]
        for tool in matched:
            self._activate_tool(tool)

        # Format as <functions> block matching the provider's tool format
        import json
        func_lines = []
        for tool in matched:
            func_def = {
                "description": tool.description,
                "name": tool.name,
                "parameters": tool.input_schema,
            }
            func_lines.append(f'<function>{json.dumps(func_def)}</function>')

        result = "<functions>\n" + "\n".join(func_lines) + "\n</functions>"
        return ToolOutput(content=result)

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
        "Fetches full schema definitions for deferred tools so they can be called. "
        "Deferred tools appear by name in system messages. Until fetched, only the "
        "name is known. Use this tool with a query to search for tools by name or "
        "keyword. Result format: matched tools' complete definitions."
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

    def register_deferred(self, tool: Tool) -> None:
        """Register a tool as deferred — schema available via ToolSearch only."""
        self._deferred_tools[tool.name] = tool

    @property
    def deferred_names(self) -> list[str]:
        return list(self._deferred_tools.keys())

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

        # Format as tool definitions
        matched = matched[:max_results]
        lines = []
        for tool in matched:
            import json
            schema_str = json.dumps(tool.input_schema, indent=2)
            lines.append(
                f"## {tool.name}\n"
                f"Description: {tool.description}\n"
                f"Schema:\n```json\n{schema_str}\n```"
            )

        return ToolOutput(content="\n\n".join(lines))

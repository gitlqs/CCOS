"""ToolSearch — resolve deferred tool schemas on demand.

In CC, some tools are "deferred" — the model sees their name but not their
full schema until it fetches them via ToolSearch. This allows a large number
of tools (MCP tools, etc.) to be exposed without bloating every API call.
"""

from __future__ import annotations

import re
from typing import Any

from ccos.tools.base import Tool, ToolContext, ToolOutput


def _parse_tool_name(name: str) -> list[str]:
    """Split a tool name into searchable lowercase parts.

    Handles MCP tools (``mcp__server__action``) and regular CamelCase /
    underscore tool names, mirroring cc's parseToolName().
    """
    if name.startswith("mcp__"):
        without_prefix = name[len("mcp__"):].lower()
        parts: list[str] = []
        for chunk in without_prefix.split("__"):
            parts.extend(chunk.split("_"))
        return [p for p in parts if p]

    # Regular tool — split CamelCase boundaries and underscores.
    spaced = re.sub(r"([a-z])([A-Z])", r"\1 \2", name).replace("_", " ")
    return [p for p in spaced.lower().split() if p]


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
        'Result format: each matched tool appears as one <function>{"description": "...", '
        '"name": "...", "parameters": {...}}</function> line inside the <functions> block — '
        "the same encoding as the tool list at the top of this prompt.\n\n"
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

    def _find_in_registry(self, name: str) -> Tool | None:
        """Look up a tool in the main registry by name (case-insensitive)."""
        if self._tool_registry is None:
            return None
        # Exact match first
        tool = self._tool_registry.get(name)
        if tool is not None:
            return tool
        # Case-insensitive fallback
        lowered = name.lower()
        for candidate in self._tool_registry.names():
            if candidate.lower() == lowered:
                return self._tool_registry.get(candidate)
        return None

    def _keyword_search(self, query: str, max_results: int) -> list[Tool]:
        """Scored keyword search over deferred tools.

        Partitions ``+``-prefixed terms into required terms (which every
        candidate must match in its name parts or description) and the rest
        into optional terms, then ranks candidates by a weighted score and
        returns the top ``max_results``. Mirrors cc's searchToolsWithKeywords.
        """
        terms = [t for t in query.lower().split() if t]

        required_terms: list[str] = []
        optional_terms: list[str] = []
        for term in terms:
            if term.startswith("+") and len(term) > 1:
                required_terms.append(term[1:])
            else:
                optional_terms.append(term)

        scoring_terms = (
            [*required_terms, *optional_terms] if required_terms else terms
        )
        if not scoring_terms:
            return []

        patterns = {
            term: re.compile(r"\b" + re.escape(term) + r"\b")
            for term in scoring_terms
        }

        def term_in_tool(term: str, name_parts: list[str], desc: str) -> bool:
            pattern = patterns[term]
            return (
                term in name_parts
                or any(term in part for part in name_parts)
                or bool(pattern.search(desc))
            )

        scored: list[tuple[int, Tool]] = []
        for name, tool in self._deferred_tools.items():
            name_parts = _parse_tool_name(name)
            desc = (tool.description or "").lower()
            is_mcp = name.startswith("mcp__")

            # Pre-filter: every required term must match somewhere.
            if required_terms and not all(
                term_in_tool(term, name_parts, desc) for term in required_terms
            ):
                continue

            score = 0
            for term in scoring_terms:
                pattern = patterns[term]
                if term in name_parts:
                    score += 12 if is_mcp else 10
                elif any(term in part for part in name_parts):
                    score += 6 if is_mcp else 5
                if pattern.search(desc):
                    score += 2

            if score > 0:
                scored.append((score, tool))

        scored.sort(key=lambda item: item[0], reverse=True)
        return [tool for _, tool in scored[:max_results]]

    async def execute(self, params: dict[str, Any], ctx: ToolContext) -> ToolOutput:
        query = params.get("query", "")
        max_results = int(params.get("max_results", 5))

        if not query:
            return ToolOutput(
                content="Please provide a query to search for tools.",
                is_error=True,
            )

        matched: list[Tool] = []
        query_lower = query.lower().strip()

        # Direct selection: "select:ToolA,ToolB". A failed select returns empty
        # (does NOT fall through to keyword search), matching cc.
        if query.startswith("select:"):
            names = [n.strip() for n in query[7:].split(",") if n.strip()]
            seen: set[str] = set()
            for name in names:
                tool = self._deferred_tools.get(name)
                if tool is None:
                    # Not deferred — fall back to the full registry. The tool
                    # is already loaded, so "selecting" it is a harmless no-op.
                    tool = self._find_in_registry(name)
                if tool is not None and tool.name not in seen:
                    seen.add(tool.name)
                    matched.append(tool)

        else:
            # Fast path: if the whole query matches a tool name exactly, return
            # it directly. Handles models using a bare tool name instead of the
            # select: prefix. Checks deferred first, then the full tool set —
            # selecting an already-loaded tool is a harmless no-op.
            exact: Tool | None = None
            for name, tool in self._deferred_tools.items():
                if name.lower() == query_lower:
                    exact = tool
                    break
            if exact is None:
                exact = self._find_in_registry(query)

            if exact is not None:
                matched = [exact]
            elif query_lower.startswith("mcp__") and len(query_lower) > 5:
                # MCP prefix search — match deferred tools by name prefix.
                for name, tool in self._deferred_tools.items():
                    if name.lower().startswith(query_lower):
                        matched.append(tool)
                if not matched:
                    matched = self._keyword_search(query, max_results)
            else:
                # Keyword / required-term search.
                matched = self._keyword_search(query, max_results)

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

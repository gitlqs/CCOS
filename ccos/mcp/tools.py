"""MCP tool wrapper — exposes MCP server tools as CCOS tools.

Each MCP tool becomes a CCOS tool with a prefixed name:
  mcp__{server_name}__{tool_name}

This matches Claude Code's naming convention for MCP tools.
"""

from __future__ import annotations

import re
from typing import Any

from ccos.mcp.client import MCPManager
from ccos.mcp.types import MCPToolDef
from ccos.tools.base import Tool, ToolContext, ToolOutput


def _normalize_tool_name(name: str) -> str:
    """Normalize tool name: replace spaces and special chars with underscores."""
    return re.sub(r"[^\w]", "_", name)


class MCPToolWrapper(Tool):
    """Wraps an MCP tool as a CCOS Tool."""

    is_read_only_default = False  # MCP tools may have side effects

    def __init__(
        self,
        mcp_tool: MCPToolDef,
        mcp_manager: MCPManager,
    ):
        self._mcp_tool = mcp_tool
        self._mcp_manager = mcp_manager
        # CC naming convention: mcp__{server}__{tool}
        server = _normalize_tool_name(mcp_tool.server_name)
        tool = _normalize_tool_name(mcp_tool.name)
        self.name = f"mcp__{server}__{tool}"
        self.description = mcp_tool.description or f"MCP tool: {mcp_tool.name}"
        self.input_schema = mcp_tool.input_schema or {
            "type": "object",
            "properties": {},
        }

    async def execute(self, params: dict[str, Any], ctx: ToolContext) -> ToolOutput:
        try:
            result = await self._mcp_manager.call_tool(
                server_name=self._mcp_tool.server_name,
                tool_name=self._mcp_tool.name,
                arguments=params,
            )
            return ToolOutput(content=result)
        except Exception as e:
            return ToolOutput(
                content=f"MCP tool error: {type(e).__name__}: {e}",
                is_error=True,
            )


def register_mcp_tools(
    mcp_manager: MCPManager,
    tool_registry: Any,  # ToolRegistry
) -> list[str]:
    """Register MCP tools as *deferred* tools via ToolSearch.

    MCP tools are NOT added to the main tool registry directly.
    Instead they are registered with the ToolSearchTool so the LLM
    can discover and load them on demand. This avoids bloating the
    tool schema list sent with every API call.

    Returns list of registered (deferred) tool names.
    """
    from ccos.tools.tool_search import ToolSearchTool

    # Find the ToolSearchTool instance in the registry
    tool_search = tool_registry.get("ToolSearch")
    if not isinstance(tool_search, ToolSearchTool):
        # Fallback: register directly if ToolSearch not available
        registered = []
        for mcp_tool in mcp_manager.all_tools:
            wrapper = MCPToolWrapper(mcp_tool, mcp_manager)
            tool_registry.register(wrapper)
            registered.append(wrapper.name)
        return registered

    registered = []
    for mcp_tool in mcp_manager.all_tools:
        wrapper = MCPToolWrapper(mcp_tool, mcp_manager)
        tool_search.register_deferred(wrapper)
        registered.append(wrapper.name)
    return registered


def unregister_mcp_tools(
    server_name: str,
    tool_registry: Any,
) -> int:
    """Remove all MCP tools for a server from the registry.

    Returns number of tools removed.
    """
    prefix = f"mcp__{_normalize_tool_name(server_name)}__"
    to_remove = [
        name for name in tool_registry.names()
        if name.startswith(prefix)
    ]
    for name in to_remove:
        tool_registry.unregister(name)
    return len(to_remove)

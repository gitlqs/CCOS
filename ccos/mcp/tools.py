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

# Claude.ai server names are prefixed with this string.
CLAUDEAI_SERVER_PREFIX = "claude.ai "

# Cap on MCP tool descriptions sent to the model (matches cc's
# MAX_MCP_DESCRIPTION_LENGTH). OpenAPI-derived servers can otherwise dump tens
# of KB into a single description on every request.
MAX_MCP_DESCRIPTION_LENGTH = 2048


def normalize_name_for_mcp(name: str) -> str:
    """Normalize a server/tool name for the API pattern ^[a-zA-Z0-9_-]{1,64}$.

    Port of cc's normalizeNameForMCP (cc/src/services/mcp/normalization.ts).
    Replaces only characters outside [a-zA-Z0-9_-] with '_' — the hyphen is
    KEPT (unlike a `\\w`-based substitution). For claude.ai servers it also
    collapses runs of underscores and strips leading/trailing underscores so
    they don't interfere with the '__' delimiter in MCP tool names.
    """
    normalized = re.sub(r"[^a-zA-Z0-9_-]", "_", name)
    if name.startswith(CLAUDEAI_SERVER_PREFIX):
        normalized = re.sub(r"_+", "_", normalized)
        normalized = re.sub(r"^_|_$", "", normalized)
    return normalized


def build_mcp_tool_name(server_name: str, tool_name: str) -> str:
    """Build a fully qualified MCP tool name: mcp__{server}__{tool}."""
    return f"mcp__{normalize_name_for_mcp(server_name)}__{normalize_name_for_mcp(tool_name)}"


class MCPToolWrapper(Tool):
    """Wraps an MCP tool as a CCOS Tool."""

    def __init__(
        self,
        mcp_tool: MCPToolDef,
        mcp_manager: MCPManager,
    ):
        self._mcp_tool = mcp_tool
        self._mcp_manager = mcp_manager
        # CC naming convention: mcp__{server}__{tool}
        self.name = build_mcp_tool_name(mcp_tool.server_name, mcp_tool.name)

        # Read-only / side-effect classification comes from the tool's
        # annotations (cc: isReadOnly()/isConcurrencySafe() = readOnlyHint ?? false).
        annotations = mcp_tool.annotations or {}
        self._read_only = bool(annotations.get("readOnlyHint", False))
        self._destructive = bool(annotations.get("destructiveHint", False))
        self._open_world = bool(annotations.get("openWorldHint", False))

        # Cap the description at MAX_MCP_DESCRIPTION_LENGTH using cc's exact
        # truncation suffix '… [truncated]' (U+2026 ellipsis + space).
        desc = mcp_tool.description or f"MCP tool: {mcp_tool.name}"
        if len(desc) > MAX_MCP_DESCRIPTION_LENGTH:
            desc = desc[:MAX_MCP_DESCRIPTION_LENGTH] + "… [truncated]"
        self.description = desc

        self.input_schema = mcp_tool.input_schema or {
            "type": "object",
            "properties": {},
        }

    def is_read_only(self, params: dict[str, Any]) -> bool:
        # cc derives this from the tool's readOnlyHint annotation.
        return self._read_only

    async def execute(self, params: dict[str, Any], ctx: ToolContext) -> ToolOutput:
        try:
            result_text, is_error = await self._mcp_manager.call_tool(
                server_name=self._mcp_tool.server_name,
                tool_name=self._mcp_tool.name,
                arguments=params,
            )
            return ToolOutput(content=result_text, is_error=is_error)
        except Exception as e:
            return ToolOutput(
                content=f"MCP tool error: {type(e).__name__}: {e}",
                is_error=True,
            )


# ---------------------------------------------------------------------------
# Resource tools (ListMcpResourcesTool / ReadMcpResourceTool)
#
# Ported from cc/src/tools/ListMcpResourcesTool and ReadMcpResourceTool.
# Registered once (as deferred tools) when any connected server advertises the
# resources capability. DESCRIPTION text is verbatim from cc.
# ---------------------------------------------------------------------------

LIST_MCP_RESOURCES_TOOL_NAME = "ListMcpResourcesTool"
READ_MCP_RESOURCE_TOOL_NAME = "ReadMcpResourceTool"

_LIST_MCP_RESOURCES_DESCRIPTION = """
Lists available resources from configured MCP servers.
Each resource object includes a 'server' field indicating which server it's from.

Usage examples:
- List all resources from all servers: `listMcpResources`
- List resources from a specific server: `listMcpResources({ server: "myserver" })`
"""

_READ_MCP_RESOURCE_DESCRIPTION = """
Reads a specific resource from an MCP server.
- server: The name of the MCP server to read from
- uri: The URI of the resource to read

Usage examples:
- Read a resource from a server: `readMcpResource({ server: "myserver", uri: "my-resource-uri" })`
"""


class ListMcpResourcesTool(Tool):
    """Lists available resources across configured MCP servers."""

    name = LIST_MCP_RESOURCES_TOOL_NAME
    description = _LIST_MCP_RESOURCES_DESCRIPTION
    input_schema = {
        "type": "object",
        "properties": {
            "server": {
                "type": "string",
                "description": "Optional server name to filter resources by",
            },
        },
    }

    def __init__(self, mcp_manager: MCPManager):
        self._mcp_manager = mcp_manager

    def is_read_only(self, params: dict[str, Any]) -> bool:
        return True

    async def execute(self, params: dict[str, Any], ctx: ToolContext) -> ToolOutput:
        import json

        target_server = params.get("server")
        connections = self._mcp_manager.connections

        if target_server and target_server not in connections:
            available = ", ".join(connections.keys())
            return ToolOutput(
                content=(
                    f'Server "{target_server}" not found. '
                    f"Available servers: {available}"
                ),
                is_error=True,
            )

        names = [target_server] if target_server else list(connections.keys())
        resources: list[dict[str, Any]] = []
        for name in names:
            conn = connections.get(name)
            if conn is None or not conn.is_connected:
                continue
            for res in conn.resources:
                resources.append({
                    "uri": res.uri,
                    "name": res.name,
                    "mimeType": res.mime_type,
                    "description": res.description,
                    "server": name,
                })

        if not resources:
            return ToolOutput(
                content=(
                    "No resources found. MCP servers may still provide tools "
                    "even if they have no resources."
                )
            )
        return ToolOutput(content=json.dumps(resources))


class ReadMcpResourceTool(Tool):
    """Reads a specific resource from an MCP server by URI."""

    name = READ_MCP_RESOURCE_TOOL_NAME
    description = _READ_MCP_RESOURCE_DESCRIPTION
    input_schema = {
        "type": "object",
        "required": ["server", "uri"],
        "properties": {
            "server": {
                "type": "string",
                "description": "The MCP server name",
            },
            "uri": {
                "type": "string",
                "description": "The resource URI to read",
            },
        },
    }

    def __init__(self, mcp_manager: MCPManager):
        self._mcp_manager = mcp_manager

    def is_read_only(self, params: dict[str, Any]) -> bool:
        return True

    async def execute(self, params: dict[str, Any], ctx: ToolContext) -> ToolOutput:
        import json

        server_name = params.get("server", "")
        uri = params.get("uri", "")
        connections = self._mcp_manager.connections
        conn = connections.get(server_name)

        if conn is None:
            available = ", ".join(connections.keys())
            return ToolOutput(
                content=(
                    f'Server "{server_name}" not found. '
                    f"Available servers: {available}"
                ),
                is_error=True,
            )
        if not conn.is_connected:
            return ToolOutput(
                content=f'Server "{server_name}" is not connected',
                is_error=True,
            )
        if not conn.capabilities.get("resources"):
            return ToolOutput(
                content=f'Server "{server_name}" does not support resources',
                is_error=True,
            )

        contents = await conn.read_resource_contents(uri)
        return ToolOutput(content=json.dumps({"contents": contents}))


def register_mcp_tools(
    mcp_manager: MCPManager,
    tool_registry: Any,  # ToolRegistry
) -> list[str]:
    """Register MCP tools as *deferred* tools via ToolSearch.

    MCP tools are NOT added to the main tool registry directly.
    Instead they are registered with the ToolSearchTool so the LLM
    can discover and load them on demand. This avoids bloating the
    tool schema list sent with every API call.

    The ListMcpResourcesTool / ReadMcpResourceTool pair is registered once
    (also deferred) when any connected server advertises the resources
    capability, matching cc's gating.

    Returns list of registered (deferred) tool names.
    """
    from ccos.tools.tool_search import ToolSearchTool

    any_resources = any(
        conn.is_connected and conn.capabilities.get("resources")
        for conn in mcp_manager.connections.values()
    )

    # Find the ToolSearchTool instance in the registry
    tool_search = tool_registry.get("ToolSearch")
    if not isinstance(tool_search, ToolSearchTool):
        # Fallback: register directly if ToolSearch not available
        registered = []
        for mcp_tool in mcp_manager.all_tools:
            wrapper = MCPToolWrapper(mcp_tool, mcp_manager)
            tool_registry.register(wrapper)
            registered.append(wrapper.name)
        if any_resources:
            for rtool in (
                ListMcpResourcesTool(mcp_manager),
                ReadMcpResourceTool(mcp_manager),
            ):
                tool_registry.register(rtool)
                registered.append(rtool.name)
        return registered

    registered = []
    for mcp_tool in mcp_manager.all_tools:
        wrapper = MCPToolWrapper(mcp_tool, mcp_manager)
        tool_search.register_deferred(wrapper)
        registered.append(wrapper.name)
    if any_resources:
        for rtool in (
            ListMcpResourcesTool(mcp_manager),
            ReadMcpResourceTool(mcp_manager),
        ):
            tool_search.register_deferred(rtool)
            registered.append(rtool.name)
    return registered


def unregister_mcp_tools(
    server_name: str,
    tool_registry: Any,
) -> int:
    """Remove all MCP tools for a server from the registry.

    Returns number of tools removed.
    """
    prefix = f"mcp__{normalize_name_for_mcp(server_name)}__"
    to_remove = [
        name for name in tool_registry.names()
        if name.startswith(prefix)
    ]
    for name in to_remove:
        tool_registry.unregister(name)
    return len(to_remove)

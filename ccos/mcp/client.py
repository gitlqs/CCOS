"""MCP client — manages connections to MCP servers.

Each MCPConnection manages a single server through its transport layer.
The MCPManager orchestrates multiple connections, handles reconnection,
and exposes aggregated tools/resources/prompts.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Callable

from ccos.mcp.transport import MCPTransport, create_transport
from ccos.mcp.types import (
    ConnectionState,
    MCPPrompt,
    MCPResource,
    MCPServerConfig,
    MCPToolDef,
    TransportType,
)

logger = logging.getLogger(__name__)

# Reconnection config
MAX_RECONNECT_ATTEMPTS = 5
RECONNECT_BASE_DELAY = 1.0  # seconds
RECONNECT_MAX_DELAY = 30.0  # seconds

# Result size limits
MAX_RESULT_SIZE = 100_000  # 100KB


class MCPConnection:
    """A connection to a single MCP server."""

    def __init__(self, name: str, config: MCPServerConfig):
        self.name = name
        self.config = config
        self._transport: MCPTransport | None = None
        self._request_id = 0
        self._tools: list[MCPToolDef] = []
        self._resources: list[MCPResource] = []
        self._prompts: list[MCPPrompt] = []
        self._state = ConnectionState.DISCONNECTED
        self._error: str = ""
        self._reconnect_attempts = 0
        self._server_info: dict[str, Any] = {}
        self._on_tools_changed: Callable[[], None] | None = None
        self._on_resources_changed: Callable[[], None] | None = None

    @property
    def state(self) -> ConnectionState:
        return self._state

    @property
    def error(self) -> str:
        return self._error

    @property
    def is_connected(self) -> bool:
        return self._state == ConnectionState.CONNECTED

    @property
    def tools(self) -> list[MCPToolDef]:
        return self._tools

    @property
    def resources(self) -> list[MCPResource]:
        return self._resources

    @property
    def prompts(self) -> list[MCPPrompt]:
        return self._prompts

    @property
    def server_info(self) -> dict[str, Any]:
        return self._server_info

    async def connect(self) -> None:
        """Start the transport and initialize the MCP session."""
        if not self.config.enabled:
            self._state = ConnectionState.DISABLED
            return

        self._state = ConnectionState.CONNECTING
        self._error = ""

        try:
            self._transport = create_transport(self.config)
            self._transport.on_notification = self._handle_notification
            await self._transport.start()

            # MCP initialize handshake
            init_result = await self._send_request("initialize", {
                "protocolVersion": "2024-11-05",
                "capabilities": {
                    "roots": {"listChanged": True},
                    "sampling": {},
                },
                "clientInfo": {
                    "name": "ccos",
                    "version": "0.1.0",
                },
            })

            if init_result is None:
                raise ConnectionError(
                    f"MCP server '{self.name}' did not respond to initialize"
                )

            self._server_info = init_result.get("serverInfo", {})

            # Send initialized notification
            await self._send_notification("notifications/initialized", {})

            # Fetch capabilities
            await self._fetch_tools()
            await self._fetch_resources()
            await self._fetch_prompts()

            self._state = ConnectionState.CONNECTED
            self._reconnect_attempts = 0

        except Exception as e:
            self._state = ConnectionState.FAILED
            self._error = str(e)
            await self._cleanup_transport()
            raise

    async def disconnect(self) -> None:
        """Gracefully close the connection."""
        await self._cleanup_transport()
        self._state = ConnectionState.DISCONNECTED
        self._tools.clear()
        self._resources.clear()
        self._prompts.clear()

    async def reconnect(self) -> None:
        """Attempt to reconnect with exponential backoff."""
        self._state = ConnectionState.RECONNECTING

        for attempt in range(MAX_RECONNECT_ATTEMPTS):
            self._reconnect_attempts = attempt + 1
            delay = min(
                RECONNECT_BASE_DELAY * (2 ** attempt),
                RECONNECT_MAX_DELAY,
            )
            await asyncio.sleep(delay)

            try:
                await self._cleanup_transport()
                await self.connect()
                return  # Success
            except Exception as e:
                self._error = f"Reconnect attempt {attempt + 1} failed: {e}"
                logger.debug(self._error)

        self._state = ConnectionState.FAILED
        self._error = (
            f"Failed to reconnect after {MAX_RECONNECT_ATTEMPTS} attempts"
        )

    async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> str:
        """Call a tool on the MCP server and return the result text."""
        result = await self._send_request("tools/call", {
            "name": tool_name,
            "arguments": arguments,
        })

        if result is None:
            return "Error: No response from MCP server"

        return self._format_tool_result(result)

    async def read_resource(self, uri: str) -> str:
        """Read a resource from the MCP server."""
        result = await self._send_request("resources/read", {"uri": uri})
        if result is None:
            return "Error: No response from MCP server"

        if isinstance(result, dict):
            contents = result.get("contents", [])
            if isinstance(contents, list) and contents:
                first = contents[0]
                # Handle text content
                if "text" in first:
                    return first["text"]
                # Handle blob content
                if "blob" in first:
                    mime = first.get("mimeType", "application/octet-stream")
                    return f"[Binary content: {mime}, {len(first['blob'])} bytes base64]"
                return str(first)
        return str(result)

    async def get_prompt(
        self, prompt_name: str, arguments: dict[str, str] | None = None
    ) -> dict[str, Any]:
        """Get a prompt from the MCP server."""
        params: dict[str, Any] = {"name": prompt_name}
        if arguments:
            params["arguments"] = arguments

        result = await self._send_request("prompts/get", params)
        return result or {}

    # -- Notification handling -----------------------------------------------

    def _handle_notification(self, method: str, params: dict[str, Any]) -> None:
        """Handle server-initiated notifications."""
        if method == "notifications/tools/list_changed":
            asyncio.create_task(self._refresh_tools())
        elif method == "notifications/resources/list_changed":
            asyncio.create_task(self._refresh_resources())
        elif method == "notifications/prompts/list_changed":
            asyncio.create_task(self._refresh_prompts())

    async def _refresh_tools(self) -> None:
        """Re-fetch tools after notification."""
        try:
            await self._fetch_tools()
            if self._on_tools_changed:
                self._on_tools_changed()
        except Exception:
            pass

    async def _refresh_resources(self) -> None:
        try:
            await self._fetch_resources()
            if self._on_resources_changed:
                self._on_resources_changed()
        except Exception:
            pass

    async def _refresh_prompts(self) -> None:
        try:
            await self._fetch_prompts()
        except Exception:
            pass

    # -- Internal fetchers ---------------------------------------------------

    async def _fetch_tools(self) -> None:
        try:
            result = await self._send_request("tools/list", {})
        except RuntimeError:
            return
        if result and isinstance(result, dict):
            self._tools = [
                MCPToolDef(
                    name=t.get("name", ""),
                    description=t.get("description", ""),
                    input_schema=t.get("inputSchema", {}),
                    server_name=self.name,
                )
                for t in result.get("tools", [])
            ]

    async def _fetch_resources(self) -> None:
        try:
            result = await self._send_request("resources/list", {})
        except RuntimeError:
            return  # Server doesn't support resources — that's fine
        if result and isinstance(result, dict):
            self._resources = [
                MCPResource(
                    uri=r.get("uri", ""),
                    name=r.get("name", ""),
                    description=r.get("description", ""),
                    mime_type=r.get("mimeType", ""),
                    server_name=self.name,
                )
                for r in result.get("resources", [])
            ]

    async def _fetch_prompts(self) -> None:
        try:
            result = await self._send_request("prompts/list", {})
        except RuntimeError:
            return  # Server doesn't support prompts — that's fine
        if result and isinstance(result, dict):
            self._prompts = [
                MCPPrompt(
                    name=p.get("name", ""),
                    description=p.get("description", ""),
                    arguments=p.get("arguments", []),
                    server_name=self.name,
                )
                for p in result.get("prompts", [])
            ]

    # -- Transport helpers ---------------------------------------------------

    async def _send_request(
        self, method: str, params: dict[str, Any]
    ) -> dict[str, Any] | None:
        if not self._transport:
            return None
        self._request_id += 1
        try:
            return await self._transport.send_request(method, params, self._request_id)
        except RuntimeError:
            raise
        except Exception:
            return None

    async def _send_notification(
        self, method: str, params: dict[str, Any]
    ) -> None:
        if not self._transport:
            return
        await self._transport.send_notification(method, params)

    async def _cleanup_transport(self) -> None:
        if self._transport:
            try:
                await self._transport.close()
            except Exception:
                pass
            self._transport = None

    @staticmethod
    def _format_tool_result(result: dict[str, Any]) -> str:
        """Format MCP tool result into text, with size limits."""
        content = result.get("content", [])
        if isinstance(content, list):
            texts = []
            for block in content:
                if isinstance(block, dict):
                    if block.get("type") == "text":
                        texts.append(block.get("text", ""))
                    elif block.get("type") == "image":
                        mime = block.get("mimeType", "unknown")
                        texts.append(f"[Image: {mime}]")
                    elif block.get("type") == "resource":
                        uri = block.get("resource", {}).get("uri", "?")
                        texts.append(f"[Resource: {uri}]")
            output = "\n".join(texts)
        else:
            output = str(content)

        # Truncate large results
        if len(output) > MAX_RESULT_SIZE:
            output = output[:MAX_RESULT_SIZE] + (
                f"\n\n⚠️ Output truncated at {MAX_RESULT_SIZE:,} bytes "
                f"(original: {len(output):,} bytes)"
            )
        return output if output else json.dumps(result)


class MCPManager:
    """Manages multiple MCP server connections."""

    def __init__(self) -> None:
        self._connections: dict[str, MCPConnection] = {}

    @property
    def connections(self) -> dict[str, MCPConnection]:
        return self._connections

    @property
    def server_names(self) -> list[str]:
        return list(self._connections.keys())

    @property
    def all_tools(self) -> list[MCPToolDef]:
        tools = []
        for conn in self._connections.values():
            if conn.is_connected:
                tools.extend(conn.tools)
        return tools

    @property
    def all_resources(self) -> list[MCPResource]:
        resources = []
        for conn in self._connections.values():
            if conn.is_connected:
                resources.extend(conn.resources)
        return resources

    @property
    def all_prompts(self) -> list[MCPPrompt]:
        prompts = []
        for conn in self._connections.values():
            if conn.is_connected:
                prompts.extend(conn.prompts)
        return prompts

    async def connect_server(
        self, name: str, config: MCPServerConfig
    ) -> str:
        """Connect to a single MCP server. Returns error string (empty = success)."""
        # Disconnect existing connection with same name
        if name in self._connections:
            await self._connections[name].disconnect()

        conn = MCPConnection(name, config)
        self._connections[name] = conn

        try:
            await conn.connect()
            return ""
        except Exception as e:
            return str(e)

    async def connect_servers(
        self, servers: dict[str, dict[str, Any]]
    ) -> dict[str, str]:
        """Connect to all configured MCP servers.

        Returns dict of server_name -> error message (empty = success).
        """
        results: dict[str, str] = {}
        for name, cfg_dict in servers.items():
            config = MCPServerConfig.from_dict(cfg_dict)
            results[name] = await self.connect_server(name, config)
        return results

    async def disconnect_server(self, name: str) -> bool:
        """Disconnect a single server. Returns True if found."""
        conn = self._connections.pop(name, None)
        if conn:
            await conn.disconnect()
            return True
        return False

    async def disconnect_all(self) -> None:
        for conn in self._connections.values():
            await conn.disconnect()
        self._connections.clear()

    async def reconnect_server(self, name: str) -> str:
        """Reconnect a server. Returns error string (empty = success)."""
        conn = self._connections.get(name)
        if not conn:
            return f"Server '{name}' not found"
        try:
            await conn.reconnect()
            return "" if conn.is_connected else conn.error
        except Exception as e:
            return str(e)

    def get_connection(self, server_name: str) -> MCPConnection | None:
        return self._connections.get(server_name)

    async def call_tool(
        self, server_name: str, tool_name: str, arguments: dict[str, Any]
    ) -> str:
        conn = self._connections.get(server_name)
        if not conn:
            return f"Error: MCP server '{server_name}' not found"
        if not conn.is_connected:
            return f"Error: MCP server '{server_name}' is not connected (state: {conn.state.value})"
        return await conn.call_tool(tool_name, arguments)

    def get_status_summary(self) -> list[dict[str, Any]]:
        """Get status of all servers for display."""
        result = []
        for name, conn in self._connections.items():
            info: dict[str, Any] = {
                "name": name,
                "state": conn.state.value,
                "transport": conn.config.type.value,
                "tools": len(conn.tools),
                "resources": len(conn.resources),
                "prompts": len(conn.prompts),
            }
            if conn.error:
                info["error"] = conn.error
            if conn.server_info:
                srv = conn.server_info
                info["server_name"] = srv.get("name", "")
                info["server_version"] = srv.get("version", "")
            result.append(info)
        return result

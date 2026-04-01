"""MCP type definitions — server config, connection states, tool/resource/prompt defs."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


# ---------------------------------------------------------------------------
# Transport types
# ---------------------------------------------------------------------------

class TransportType(str, Enum):
    STDIO = "stdio"
    SSE = "sse"
    HTTP = "http"
    WS = "ws"


# ---------------------------------------------------------------------------
# Connection states
# ---------------------------------------------------------------------------

class ConnectionState(str, Enum):
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    FAILED = "failed"
    RECONNECTING = "reconnecting"
    DISABLED = "disabled"


# ---------------------------------------------------------------------------
# Server config (from config.json)
# ---------------------------------------------------------------------------

@dataclass
class MCPServerConfig:
    """Configuration for a single MCP server."""
    # Common
    type: TransportType = TransportType.STDIO

    # Stdio transport
    command: str = ""
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    cwd: str = ""

    # Network transports (SSE, HTTP, WS)
    url: str = ""
    headers: dict[str, str] = field(default_factory=dict)

    # Feature flags
    enabled: bool = True

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> MCPServerConfig:
        transport_str = d.get("type", "stdio")
        try:
            transport = TransportType(transport_str)
        except ValueError:
            transport = TransportType.STDIO

        return cls(
            type=transport,
            command=d.get("command", ""),
            args=d.get("args", []) if isinstance(d.get("args"), list) else [d["args"]] if d.get("args") else [],
            env=d.get("env", {}),
            cwd=d.get("cwd", ""),
            url=d.get("url", ""),
            headers=d.get("headers", {}),
            enabled=d.get("enabled", True),
        )

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"type": self.type.value}
        if self.type == TransportType.STDIO:
            d["command"] = self.command
            if self.args:
                d["args"] = self.args
            if self.env:
                d["env"] = self.env
            if self.cwd:
                d["cwd"] = self.cwd
        else:
            d["url"] = self.url
            if self.headers:
                d["headers"] = self.headers
        if not self.enabled:
            d["enabled"] = False
        return d


# ---------------------------------------------------------------------------
# Tool / Resource / Prompt definitions
# ---------------------------------------------------------------------------

@dataclass
class MCPToolDef:
    """A tool exposed by an MCP server."""
    name: str
    description: str
    input_schema: dict[str, Any]
    server_name: str


@dataclass
class MCPResource:
    """A resource exposed by an MCP server."""
    uri: str
    name: str
    description: str = ""
    mime_type: str = ""
    server_name: str = ""


@dataclass
class MCPPrompt:
    """A prompt exposed by an MCP server."""
    name: str
    description: str = ""
    arguments: list[dict[str, Any]] = field(default_factory=list)
    server_name: str = ""

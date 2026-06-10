"""MCP type definitions — server config, connection states, tool/resource/prompt defs."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


# ---------------------------------------------------------------------------
# Environment-variable expansion (mirrors cc's envExpansion.ts)
# ---------------------------------------------------------------------------

_ENV_VAR_PATTERN = re.compile(r"\$\{([^}]+)\}")


def expand_env_vars(value: str) -> tuple[str, list[str]]:
    """Expand ${VAR} and ${VAR:-default} in a string against os.environ.

    Port of cc's expandEnvVarsInString (cc/src/services/mcp/envExpansion.ts).
    Returns the expanded string plus a list of any referenced variables that
    were unset and had no default (for error/warning reporting).
    """
    missing_vars: list[str] = []

    def _replace(match: re.Match[str]) -> str:
        var_content = match.group(1)
        # Split on ':-' (limit 2) to support default values while preserving
        # any ':-' that appears inside the default itself.
        parts = var_content.split(":-", 1)
        var_name = parts[0]
        default_value = parts[1] if len(parts) > 1 else None

        env_value = os.environ.get(var_name)
        if env_value is not None:
            return env_value
        if default_value is not None:
            return default_value

        missing_vars.append(var_name)
        # Leave the original placeholder so it's debuggable; caller reports it.
        return match.group(0)

    expanded = _ENV_VAR_PATTERN.sub(_replace, value)
    return expanded, missing_vars


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

    # Names of ${VAR} references that could not be resolved during expansion.
    missing_env_vars: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> MCPServerConfig:
        transport_str = d.get("type", "stdio")
        try:
            transport = TransportType(transport_str)
        except ValueError:
            transport = TransportType.STDIO

        missing: list[str] = []

        def _expand(value: str) -> str:
            expanded, miss = expand_env_vars(value)
            missing.extend(miss)
            return expanded

        # Normalize args into a list before expansion.
        raw_args = d.get("args")
        if isinstance(raw_args, list):
            args = list(raw_args)
        elif raw_args:
            args = [raw_args]
        else:
            args = []

        command = _expand(d.get("command", ""))
        args = [_expand(a) if isinstance(a, str) else a for a in args]
        env = {
            k: _expand(v) if isinstance(v, str) else v
            for k, v in (d.get("env", {}) or {}).items()
        }
        url = _expand(d.get("url", ""))
        headers = {
            k: _expand(v) if isinstance(v, str) else v
            for k, v in (d.get("headers", {}) or {}).items()
        }

        return cls(
            type=transport,
            command=command,
            args=args,
            env=env,
            cwd=d.get("cwd", ""),
            url=url,
            headers=headers,
            enabled=d.get("enabled", True),
            missing_env_vars=missing,
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
    # Tool annotations (readOnlyHint / destructiveHint / openWorldHint / title).
    annotations: dict[str, Any] = field(default_factory=dict)


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

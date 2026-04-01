"""MCP transport layer — abstract base + stdio, SSE, HTTP, WebSocket implementations.

Each transport handles the low-level communication:
- Sending JSON-RPC 2.0 requests and notifications
- Receiving responses and server-initiated notifications
- Connection lifecycle (start, close)
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from abc import ABC, abstractmethod
from typing import Any, Callable

# JSON-RPC message type
JSONRPCMessage = dict[str, Any]


class MCPTransport(ABC):
    """Abstract base for MCP transports."""

    on_notification: Callable[[str, dict[str, Any]], None] | None = None

    @abstractmethod
    async def start(self) -> None:
        """Open the transport connection."""

    @abstractmethod
    async def close(self) -> None:
        """Close the transport connection."""

    @abstractmethod
    async def send_request(
        self, method: str, params: dict[str, Any], request_id: int
    ) -> dict[str, Any] | None:
        """Send a JSON-RPC request and return the result (or None on error)."""

    async def send_notification(
        self, method: str, params: dict[str, Any]
    ) -> None:
        """Send a JSON-RPC notification (no response expected)."""
        # Default: use _send_raw if available
        msg = {"jsonrpc": "2.0", "method": method, "params": params}
        await self._send_raw(msg)

    @abstractmethod
    async def _send_raw(self, message: JSONRPCMessage) -> None:
        """Send a raw JSON-RPC message."""


# ---------------------------------------------------------------------------
# Stdio transport
# ---------------------------------------------------------------------------

class StdioTransport(MCPTransport):
    """Transport over subprocess stdin/stdout pipes."""

    def __init__(
        self,
        command: str,
        args: list[str] | None = None,
        env: dict[str, str] | None = None,
        cwd: str = "",
    ):
        self._command = command
        self._args = args or []
        self._env = env or {}
        self._cwd = cwd
        self._process: subprocess.Popen | None = None
        self._read_lock = asyncio.Lock()
        self._notification_task: asyncio.Task | None = None
        # Buffer for reading — we may receive notifications mixed with responses
        self._pending_responses: dict[int, asyncio.Future[dict[str, Any] | None]] = {}

    async def start(self) -> None:
        env = {**os.environ, **self._env}
        cmd = [self._command, *self._args]
        try:
            self._process = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
                cwd=self._cwd or None,
            )
        except FileNotFoundError:
            raise ConnectionError(
                f"MCP server command not found: {self._command}"
            )
        # Start background reader
        self._notification_task = asyncio.create_task(self._read_loop())

    async def close(self) -> None:
        if self._notification_task:
            self._notification_task.cancel()
            try:
                await self._notification_task
            except asyncio.CancelledError:
                pass
        # Cancel pending requests
        for fut in self._pending_responses.values():
            if not fut.done():
                fut.set_result(None)
        self._pending_responses.clear()

        if self._process and self._process.poll() is None:
            try:
                if self._process.stdin:
                    self._process.stdin.close()
                self._process.terminate()
                self._process.wait(timeout=5)
            except Exception:
                if self._process and self._process.poll() is None:
                    self._process.kill()

    async def send_request(
        self, method: str, params: dict[str, Any], request_id: int
    ) -> dict[str, Any] | None:
        if not self._process or not self._process.stdin:
            return None

        msg: JSONRPCMessage = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params,
        }

        # Create future for this request
        loop = asyncio.get_event_loop()
        fut: asyncio.Future[dict[str, Any] | None] = loop.create_future()
        self._pending_responses[request_id] = fut

        await self._send_raw(msg)

        try:
            return await asyncio.wait_for(fut, timeout=60)
        except asyncio.TimeoutError:
            self._pending_responses.pop(request_id, None)
            return None

    async def _send_raw(self, message: JSONRPCMessage) -> None:
        if not self._process or not self._process.stdin:
            return
        try:
            data = json.dumps(message) + "\n"
            self._process.stdin.write(data.encode("utf-8"))
            self._process.stdin.flush()
        except OSError:
            pass

    async def _read_loop(self) -> None:
        """Background task: read lines from stdout and dispatch."""
        loop = asyncio.get_event_loop()
        while True:
            try:
                if not self._process or not self._process.stdout:
                    break
                line = await loop.run_in_executor(
                    None, self._process.stdout.readline
                )
                if not line:
                    break
                msg = json.loads(line.decode("utf-8"))
                self._dispatch_message(msg)
            except (json.JSONDecodeError, OSError):
                continue
            except asyncio.CancelledError:
                break

    def _dispatch_message(self, msg: JSONRPCMessage) -> None:
        """Route a received message to the right handler."""
        if "id" in msg:
            # Response to a request
            req_id = msg["id"]
            fut = self._pending_responses.pop(req_id, None)
            if fut and not fut.done():
                if "error" in msg:
                    err = msg["error"]
                    fut.set_exception(RuntimeError(
                        f"MCP error ({err.get('code', '?')}): {err.get('message', '?')}"
                    ))
                else:
                    fut.set_result(msg.get("result"))
        elif "method" in msg and "id" not in msg:
            # Server-initiated notification
            method = msg.get("method", "")
            params = msg.get("params", {})
            if self.on_notification:
                try:
                    self.on_notification(method, params)
                except Exception:
                    pass


# ---------------------------------------------------------------------------
# SSE transport
# ---------------------------------------------------------------------------

class SSETransport(MCPTransport):
    """Transport over Server-Sent Events (HTTP GET stream + POST for requests).

    The SSE protocol:
    1. Client opens a long-lived GET to the server URL → receives an `endpoint` event
    2. Client sends JSON-RPC requests via POST to that endpoint
    3. Server sends responses and notifications as SSE events on the GET stream
    """

    def __init__(self, url: str, headers: dict[str, str] | None = None):
        self._url = url
        self._headers = headers or {}
        self._endpoint_url: str | None = None
        self._session: Any = None  # httpx.AsyncClient
        self._sse_task: asyncio.Task | None = None
        self._pending_responses: dict[int, asyncio.Future[dict[str, Any] | None]] = {}
        self._endpoint_ready = asyncio.Event()

    async def start(self) -> None:
        import httpx
        self._session = httpx.AsyncClient(timeout=httpx.Timeout(300, connect=30))
        self._sse_task = asyncio.create_task(self._sse_stream())
        # Wait for the endpoint event (with timeout)
        try:
            await asyncio.wait_for(self._endpoint_ready.wait(), timeout=30)
        except asyncio.TimeoutError:
            raise ConnectionError("SSE server did not send endpoint event within 30s")

    async def close(self) -> None:
        if self._sse_task:
            self._sse_task.cancel()
            try:
                await self._sse_task
            except asyncio.CancelledError:
                pass
        for fut in self._pending_responses.values():
            if not fut.done():
                fut.set_result(None)
        self._pending_responses.clear()
        if self._session:
            await self._session.aclose()

    async def send_request(
        self, method: str, params: dict[str, Any], request_id: int
    ) -> dict[str, Any] | None:
        if not self._endpoint_url or not self._session:
            return None

        msg: JSONRPCMessage = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params,
        }

        loop = asyncio.get_event_loop()
        fut: asyncio.Future[dict[str, Any] | None] = loop.create_future()
        self._pending_responses[request_id] = fut

        await self._send_raw(msg)

        try:
            return await asyncio.wait_for(fut, timeout=60)
        except asyncio.TimeoutError:
            self._pending_responses.pop(request_id, None)
            return None

    async def _send_raw(self, message: JSONRPCMessage) -> None:
        if not self._endpoint_url or not self._session:
            return
        headers = {**self._headers, "Content-Type": "application/json"}
        try:
            resp = await self._session.post(
                self._endpoint_url,
                content=json.dumps(message),
                headers=headers,
            )
            resp.raise_for_status()
        except Exception:
            pass

    async def _sse_stream(self) -> None:
        """Long-lived GET stream to receive SSE events."""
        import httpx
        headers = {**self._headers, "Accept": "text/event-stream"}
        try:
            async with self._session.stream(
                "GET", self._url, headers=headers
            ) as resp:
                resp.raise_for_status()
                event_type = ""
                data_lines: list[str] = []

                async for line_bytes in resp.aiter_lines():
                    line = line_bytes
                    if line.startswith("event:"):
                        event_type = line[6:].strip()
                    elif line.startswith("data:"):
                        data_lines.append(line[5:].strip())
                    elif line == "":
                        # End of event
                        if data_lines:
                            data = "\n".join(data_lines)
                            self._handle_sse_event(event_type, data)
                        event_type = ""
                        data_lines = []
        except asyncio.CancelledError:
            raise
        except Exception:
            pass

    def _handle_sse_event(self, event_type: str, data: str) -> None:
        """Process a received SSE event."""
        if event_type == "endpoint":
            # The server tells us where to POST requests
            endpoint = data.strip()
            if endpoint.startswith("/"):
                # Relative URL — resolve against base
                from urllib.parse import urljoin
                self._endpoint_url = urljoin(self._url, endpoint)
            else:
                self._endpoint_url = endpoint
            self._endpoint_ready.set()
            return

        if event_type == "message" or not event_type:
            try:
                msg = json.loads(data)
            except json.JSONDecodeError:
                return
            self._dispatch_message(msg)

    def _dispatch_message(self, msg: JSONRPCMessage) -> None:
        if "id" in msg and "method" not in msg:
            # Response
            req_id = msg["id"]
            fut = self._pending_responses.pop(req_id, None)
            if fut and not fut.done():
                if "error" in msg:
                    err = msg["error"]
                    fut.set_exception(RuntimeError(
                        f"MCP error ({err.get('code', '?')}): {err.get('message', '?')}"
                    ))
                else:
                    fut.set_result(msg.get("result"))
        elif "method" in msg and "id" not in msg:
            # Notification
            method = msg.get("method", "")
            params = msg.get("params", {})
            if self.on_notification:
                try:
                    self.on_notification(method, params)
                except Exception:
                    pass


# ---------------------------------------------------------------------------
# HTTP Streamable transport
# ---------------------------------------------------------------------------

class HTTPTransport(MCPTransport):
    """HTTP Streamable transport — each request is a POST, response may be streamed.

    Unlike SSE, there's no long-lived GET connection. Each JSON-RPC request
    is a POST, and the response comes back in the same HTTP response.
    For notifications from server, client polls or uses SSE upgrade.
    """

    def __init__(self, url: str, headers: dict[str, str] | None = None):
        self._url = url
        self._headers = headers or {}
        self._session: Any = None
        self._session_id: str | None = None

    async def start(self) -> None:
        import httpx
        self._session = httpx.AsyncClient(timeout=httpx.Timeout(300, connect=30))

    async def close(self) -> None:
        if self._session:
            await self._session.aclose()

    async def send_request(
        self, method: str, params: dict[str, Any], request_id: int
    ) -> dict[str, Any] | None:
        if not self._session:
            return None

        msg: JSONRPCMessage = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params,
        }

        headers = {
            **self._headers,
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id

        try:
            resp = await self._session.post(
                self._url,
                content=json.dumps(msg),
                headers=headers,
            )
            resp.raise_for_status()

            # Track session ID from response headers
            sid = resp.headers.get("mcp-session-id")
            if sid:
                self._session_id = sid

            content_type = resp.headers.get("content-type", "")

            if "text/event-stream" in content_type:
                # Parse SSE response
                return self._parse_sse_response(resp.text, request_id)
            else:
                # Regular JSON response
                result = resp.json()
                if "error" in result:
                    err = result["error"]
                    raise RuntimeError(
                        f"MCP error ({err.get('code', '?')}): {err.get('message', '?')}"
                    )
                return result.get("result")

        except RuntimeError:
            raise
        except Exception:
            return None

    async def _send_raw(self, message: JSONRPCMessage) -> None:
        if not self._session:
            return
        headers = {
            **self._headers,
            "Content-Type": "application/json",
        }
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        try:
            await self._session.post(
                self._url,
                content=json.dumps(message),
                headers=headers,
            )
        except Exception:
            pass

    def _parse_sse_response(
        self, text: str, request_id: int
    ) -> dict[str, Any] | None:
        """Parse SSE-formatted response body for the matching request ID."""
        for line in text.split("\n"):
            if line.startswith("data:"):
                data = line[5:].strip()
                try:
                    msg = json.loads(data)
                    if msg.get("id") == request_id:
                        if "error" in msg:
                            err = msg["error"]
                            raise RuntimeError(
                                f"MCP error ({err.get('code', '?')}): {err.get('message', '?')}"
                            )
                        return msg.get("result")
                    # Could be a notification
                    if "method" in msg and "id" not in msg:
                        if self.on_notification:
                            self.on_notification(
                                msg.get("method", ""), msg.get("params", {})
                            )
                except json.JSONDecodeError:
                    continue
        return None


# ---------------------------------------------------------------------------
# WebSocket transport
# ---------------------------------------------------------------------------

class WebSocketTransport(MCPTransport):
    """WebSocket transport — full-duplex communication."""

    def __init__(self, url: str, headers: dict[str, str] | None = None):
        self._url = url
        self._headers = headers or {}
        self._ws: Any = None
        self._recv_task: asyncio.Task | None = None
        self._pending_responses: dict[int, asyncio.Future[dict[str, Any] | None]] = {}

    async def start(self) -> None:
        try:
            import websockets
        except ImportError:
            raise ImportError(
                "WebSocket transport requires the 'websockets' package. "
                "Install with: pip install websockets"
            )

        extra_headers = {**self._headers}
        self._ws = await websockets.connect(
            self._url,
            additional_headers=extra_headers,
            subprotocols=["mcp"],
        )
        self._recv_task = asyncio.create_task(self._recv_loop())

    async def close(self) -> None:
        if self._recv_task:
            self._recv_task.cancel()
            try:
                await self._recv_task
            except asyncio.CancelledError:
                pass
        for fut in self._pending_responses.values():
            if not fut.done():
                fut.set_result(None)
        self._pending_responses.clear()
        if self._ws:
            await self._ws.close()

    async def send_request(
        self, method: str, params: dict[str, Any], request_id: int
    ) -> dict[str, Any] | None:
        if not self._ws:
            return None

        msg: JSONRPCMessage = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params,
        }

        loop = asyncio.get_event_loop()
        fut: asyncio.Future[dict[str, Any] | None] = loop.create_future()
        self._pending_responses[request_id] = fut

        await self._send_raw(msg)

        try:
            return await asyncio.wait_for(fut, timeout=60)
        except asyncio.TimeoutError:
            self._pending_responses.pop(request_id, None)
            return None

    async def _send_raw(self, message: JSONRPCMessage) -> None:
        if not self._ws:
            return
        try:
            await self._ws.send(json.dumps(message))
        except Exception:
            pass

    async def _recv_loop(self) -> None:
        """Read messages from WebSocket and dispatch."""
        try:
            async for raw in self._ws:
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                self._dispatch_message(msg)
        except asyncio.CancelledError:
            raise
        except Exception:
            pass

    def _dispatch_message(self, msg: JSONRPCMessage) -> None:
        if "id" in msg and "method" not in msg:
            req_id = msg["id"]
            fut = self._pending_responses.pop(req_id, None)
            if fut and not fut.done():
                if "error" in msg:
                    err = msg["error"]
                    fut.set_exception(RuntimeError(
                        f"MCP error ({err.get('code', '?')}): {err.get('message', '?')}"
                    ))
                else:
                    fut.set_result(msg.get("result"))
        elif "method" in msg and "id" not in msg:
            method = msg.get("method", "")
            params = msg.get("params", {})
            if self.on_notification:
                try:
                    self.on_notification(method, params)
                except Exception:
                    pass


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def create_transport(config: Any) -> MCPTransport:
    """Create the appropriate transport from an MCPServerConfig."""
    from ccos.mcp.types import MCPServerConfig, TransportType

    if not isinstance(config, MCPServerConfig):
        raise TypeError(f"Expected MCPServerConfig, got {type(config)}")

    if config.type == TransportType.STDIO:
        return StdioTransport(
            command=config.command,
            args=config.args,
            env=config.env,
            cwd=config.cwd,
        )
    elif config.type == TransportType.SSE:
        return SSETransport(url=config.url, headers=config.headers)
    elif config.type == TransportType.HTTP:
        return HTTPTransport(url=config.url, headers=config.headers)
    elif config.type == TransportType.WS:
        return WebSocketTransport(url=config.url, headers=config.headers)
    else:
        raise ValueError(f"Unknown transport type: {config.type}")

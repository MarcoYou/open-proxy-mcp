"""Real Streamable HTTP transport; no vendor LLM SDK or local result files."""
from __future__ import annotations

from contextlib import AsyncExitStack
import json
from typing import Any, Protocol


class MCPTransport(Protocol):
    async def call_tool(self, name: str, arguments: dict[str, Any]) -> dict: ...


class TransportError(RuntimeError):
    """Never includes remote exception text, URLs, or submitted values."""


def _payload(result: Any) -> dict:
    if isinstance(result, dict):
        return result
    if getattr(result, "is_error", getattr(result, "isError", False)):
        raise TransportError("mcp_tool_error")
    structured = getattr(result, "structured_content", getattr(result, "structuredContent", None))
    if isinstance(structured, dict):
        # Some MCP versions wrap scalar tool outputs in a `result` field.
        if isinstance(structured.get("result"), str):
            try:
                parsed = json.loads(structured["result"])
            except (ValueError, TypeError):
                raise TransportError("invalid_mcp_payload") from None
            if isinstance(parsed, dict):
                return parsed
        if "status" in structured or "data" in structured:
            return structured
    for item in getattr(result, "content", []):
        if getattr(item, "type", None) == "text":
            try:
                parsed = json.loads(item.text)
            except (ValueError, TypeError):
                continue
            if isinstance(parsed, dict):
                return parsed
    raise TransportError("invalid_mcp_payload")


class StreamableHTTPTransport:
    """Use ``async with`` to initialize/close the MCP SDK 2 session.

    The optional HTTP client is supplied by the caller (for example for auth).
    The harness never logs the endpoint, headers, arguments, or error text.
    Only the read-only proxy advice tool is callable through this transport.
    """

    def __init__(self, endpoint: str, *, http_client: Any = None,
                 read_timeout_seconds: float = 180):
        self._endpoint = endpoint
        self._http_client = http_client
        self._timeout = read_timeout_seconds
        self._stack: AsyncExitStack | None = None
        self._session: Any = None

    async def __aenter__(self) -> StreamableHTTPTransport:
        from mcp.client.session import ClientSession
        from mcp.client.streamable_http import streamable_http_client

        stack = AsyncExitStack()
        try:
            streams = await stack.enter_async_context(streamable_http_client(
                self._endpoint, http_client=self._http_client))
            self._session = await stack.enter_async_context(ClientSession(
                streams[0], streams[1], read_timeout_seconds=self._timeout))
            await self._session.initialize()
        except Exception:
            try:
                await stack.aclose()
            except Exception:
                pass
            self._session = None
            raise TransportError("mcp_connection_failed") from None
        self._stack = stack
        return self

    async def __aexit__(self, exc_type, exc, tb):
        try:
            if self._stack:
                await self._stack.aclose()
        except Exception:
            raise TransportError("mcp_close_failed") from None
        finally:
            self._session = None
            self._stack = None

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> dict:
        if name != "proxy_advise_before_meeting":
            raise TransportError("tool_not_allowed")
        if self._session is None:
            raise TransportError("mcp_session_not_open")
        try:
            result = await self._session.call_tool(
                name, arguments, read_timeout_seconds=self._timeout)
            return _payload(result)
        except TransportError:
            raise
        except Exception:
            raise TransportError("mcp_call_failed") from None

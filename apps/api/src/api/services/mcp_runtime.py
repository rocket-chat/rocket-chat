"""Run MCP server tools as agent tools.

A turn borrows the tools of the MCP servers its caller may use - the organization's and the
member's own - namespaced ``mcp__<server>__<tool>``. Connections are opened per call with the
official ``mcp`` client; the tool list is cached briefly so a turn does not reconnect just to
advertise tools.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import time
from collections.abc import Callable, Iterable
from contextlib import AbstractAsyncContextManager, AsyncExitStack
from dataclasses import dataclass, field
from typing import Any

import httpx2
from agent_core.tools.clamp import clamp_output
from agent_core.tools.safe_http import UnsafeURLError, resolve_public_target
from config_engine.crypto import CredentialCipher
from mcp import Client
from mcp.client.sse import sse_client
from mcp.client.streamable_http import streamable_http_client

from specifications.interfaces.llm import ToolDefinition

logger = logging.getLogger(__name__)

TOOL_NAME_PREFIX = "mcp"
SEPARATOR = "__"
# Providers reject tool names outside [a-zA-Z0-9_-]{1,64}.
MAX_TOOL_NAME_LENGTH = 64
LIST_TIMEOUT_SECONDS = 8.0
CALL_TIMEOUT_SECONDS = 120.0
CACHE_TTL_SECONDS = 300.0
REMOTE_TRANSPORTS = {"http", "sse"}
STDIO = "stdio"


@dataclass(frozen=True)
class McpServerTarget:
    """What is needed to reach one MCP server (secrets are decrypted per turn, never stored)."""

    server_id: str
    name: str
    transport: str
    url: str
    updated_at: int = 0
    headers: dict[str, str] = field(default_factory=dict, repr=False)
    # Command servers: the script that installs their program (run once on the MCP runner).
    setup: str = ""


McpConnector = Callable[[McpServerTarget], AbstractAsyncContextManager[Client]]


def canonical_transport(transport: str) -> str:
    lowered = transport.strip().lower()
    return {"streamable_http": "http", "streamable-http": "http"}.get(lowered, lowered)


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", name).strip("-").lower()
    return slug or "server"


def namespaced_tool_name(server_slug: str, tool: str) -> str:
    """``mcp__<server>__<tool>`` clipped to the provider limit, keeping the tool's tail."""
    prefix = f"{TOOL_NAME_PREFIX}{SEPARATOR}{server_slug}{SEPARATOR}"
    safe_tool = re.sub(r"[^a-zA-Z0-9_-]", "_", tool)
    return (prefix + safe_tool)[:MAX_TOOL_NAME_LENGTH]


def _field(row: Any, name: str, default: Any = None) -> Any:
    return row.get(name, default) if isinstance(row, dict) else getattr(row, name, default)


def build_targets(
    rows: Iterable[Any],
    *,
    cipher: CredentialCipher,
    allowed_org_transports: Iterable[str],
    allowed_user_transports: Iterable[str],
    stdio_available: bool = False,
) -> list[McpServerTarget]:
    """Servers a turn may call: the transports the organization's policy permits.

    stdio servers are arbitrary commands, so they never run in the API process: they are only
    offered when the deployment has an MCP runner (``stdio_available``), and they run there.
    WebSocket has no SDK client.
    """
    org_ok = {canonical_transport(t) for t in allowed_org_transports}
    user_ok = {canonical_transport(t) for t in allowed_user_transports}
    usable = REMOTE_TRANSPORTS | ({STDIO} if stdio_available else set())
    targets: list[McpServerTarget] = []
    for row in rows:
        if _field(row, "is_builtin", False):
            continue
        transport = canonical_transport(str(_field(row, "transport", "")))
        scope = _field(row, "scope", "org")
        permitted = user_ok if scope == "user" else org_ok
        if transport not in usable or transport not in permitted:
            continue
        url = str(_field(row, "endpoint_or_command", "")).strip()
        if transport == STDIO:
            if not url or "\n" in url or "\r" in url:
                continue
        elif not url.startswith(("http://", "https://")):
            continue
        targets.append(
            McpServerTarget(
                server_id=str(_field(row, "id")),
                name=str(_field(row, "name", "")),
                transport=transport,
                url=url,
                updated_at=int(_field(row, "updated_at", 0) or 0),
                headers=auth_headers(_field(row, "encrypted_auth", {}) or {}, cipher),
                setup=str((_field(row, "encrypted_auth", {}) or {}).get("install_command") or ""),
            )
        )
    return targets


def auth_headers(auth: dict[str, Any], cipher: CredentialCipher) -> dict[str, str]:
    """Request headers carrying the server's stored credentials."""
    headers: dict[str, str] = {}
    api_key = auth.get("api_key")
    if api_key:
        prefix = auth.get("header_prefix") or "Bearer"
        headers[auth.get("header_name") or "Authorization"] = (
            f"{prefix} {cipher.decrypt(api_key)}".strip()
        )
    for header in auth.get("headers") or []:
        # An empty secret is a variable left for members to provide; there is nothing to decrypt.
        is_sealed = header.get("is_secret") and header["value"]
        value = cipher.decrypt(header["value"]) if is_sealed else header["value"]
        headers[header["name"]] = value
    token = (
        cipher.decrypt(auth["access_token_enc"])
        if auth.get("access_token_enc")
        else auth.get("access_token")  # legacy plaintext rows, rewritten encrypted on next login
    )
    if token and "Authorization" not in headers:
        headers["Authorization"] = f"{auth.get('token_type') or 'Bearer'} {token}"
    return headers


def connect_remote(target: McpServerTarget) -> AbstractAsyncContextManager[Client]:
    """Open an MCP client session to a remote server."""
    if target.transport == "sse":
        return Client(sse_client(target.url, headers=target.headers))
    http_client = httpx2.AsyncClient(headers=target.headers, timeout=CALL_TIMEOUT_SECONDS)
    return Client(streamable_http_client(target.url, http_client=http_client))


async def _assert_reachable_target(target: McpServerTarget) -> None:
    """Members can register their own server URLs, so they get the same SSRF guard as web_fetch."""
    await resolve_public_target(target.url)


def render_result(result: Any) -> str:
    """Flatten an MCP ``CallToolResult`` into text for the model."""
    parts: list[str] = []
    for block in getattr(result, "content", None) or []:
        text = getattr(block, "text", None)
        parts.append(text if text is not None else f"[{getattr(block, 'type', 'content')} content]")
    if not parts and getattr(result, "structured_content", None):
        parts.append(str(result.structured_content))
    text = "\n".join(parts) or "(no output)"
    return clamp_output(f"MCP tool error: {text}" if getattr(result, "is_error", False) else text)


def summarize_schema(schema: Any) -> str:
    """Format a JSON Schema as a short TypeScript-like parameter signature."""
    if not isinstance(schema, dict):
        return "options?: object"
    properties = schema.get("properties", {})
    required = set(schema.get("required", []))
    return ", ".join(
        f"{name}{'' if name in required else '?'}: "
        f"{data.get('type', 'any') if isinstance(data, dict) else 'any'}"
        for name, data in properties.items()
    )


def summarize_tool(tool: Any) -> dict[str, Any]:
    """The stored (display) form of a tool; execution re-lists the live schema."""
    return {
        "name": tool.name,
        "description": tool.description or "",
        "parameters_summary": summarize_schema(tool.input_schema),
    }


async def probe_target(
    target: McpServerTarget,
    timeout_seconds: float,
    *,
    connector: McpConnector | None = None,
) -> tuple[str, list[dict[str, Any]], str | None]:
    """Handshake with a remote server and list its tools.

    Returns ``(status, tools, error)`` where status is ``connected`` only if the MCP session
    really initialised and answered ``tools/list``.
    """
    try:
        await resolve_public_target(target.url)
    except UnsafeURLError as err:
        return "error", [], str(err)
    try:
        async with asyncio.timeout(timeout_seconds):
            async with (connector or connect_remote)(target) as client:
                result = await client.list_tools()
    except TimeoutError:
        return "offline", [], f"No MCP response from {target.url} within {timeout_seconds:g}s"
    except (OSError, httpx2.TransportError, httpx2.TimeoutException) as err:
        return "offline", [], f"Connection failed: {err}"
    except Exception as err:
        logger.info("MCP probe of %s failed: %s", target.url, err)
        return "error", [], str(err)
    return "connected", [summarize_tool(t) for t in result.tools], None


@dataclass
class _Listing:
    fetched_at: float
    updated_at: int
    tools: list[tuple[str, ToolDefinition]]


_listing_cache: dict[str, _Listing] = {}


@dataclass
class _Connection:
    client: Client
    stack: AsyncExitStack


class McpToolProvider:
    """``ExternalToolProvider`` over a set of MCP servers, built once per turn.

    Connections are opened on first use and kept for the turn (a stdio server is a process on
    the runner; a remote one a TLS handshake), reopened once if one breaks, and closed by
    :meth:`aclose` when the turn ends.
    """

    def __init__(
        self,
        targets: list[McpServerTarget],
        *,
        connector: McpConnector = connect_remote,
        stdio_connector: McpConnector | None = None,
        guard: Callable[[McpServerTarget], Any] = _assert_reachable_target,
    ) -> None:
        self._targets = {t.server_id: t for t in targets}
        self._connector = connector
        self._stdio_connector = stdio_connector
        self._guard = guard
        self._definitions: dict[str, ToolDefinition] = {}
        self._routes: dict[str, tuple[McpServerTarget, str]] = {}
        self._pool: dict[str, _Connection] = {}
        self._opening: dict[str, asyncio.Lock] = {}

    # -- connections --------------------------------------------------------------------

    def _connector_for(self, target: McpServerTarget) -> McpConnector:
        if target.transport == STDIO:
            if self._stdio_connector is None:
                raise RuntimeError("command (stdio) MCP servers need the MCP runner")
            return self._stdio_connector
        return self._connector

    async def _client(self, target: McpServerTarget) -> Client:
        async with self._opening.setdefault(target.server_id, asyncio.Lock()):
            pooled = self._pool.get(target.server_id)
            if pooled is not None:
                return pooled.client
            if target.transport != STDIO:
                await self._guard(target)
            stack = AsyncExitStack()
            try:
                client = await stack.enter_async_context(self._connector_for(target)(target))
            except BaseException:
                await stack.aclose()
                raise
            self._pool[target.server_id] = _Connection(client, stack)
            return client

    async def _drop(self, target: McpServerTarget) -> None:
        pooled = self._pool.pop(target.server_id, None)
        if pooled is not None:
            with contextlib.suppress(Exception):
                await pooled.stack.aclose()

    async def aclose(self) -> None:
        """Close every connection (and stop every stdio server) this turn opened."""
        for pooled in list(self._pool.values()):
            with contextlib.suppress(Exception):
                await pooled.stack.aclose()
        self._pool.clear()

    # -- discovery ----------------------------------------------------------------------

    async def load(self) -> McpToolProvider:
        """Discover every server's tools concurrently; an unreachable server is skipped."""
        listings = await asyncio.gather(*(self._list(t) for t in self._targets.values()))
        used_slugs: set[str] = set()
        for target, listing in zip(self._targets.values(), listings, strict=True):
            slug = slugify(target.name)
            while slug in used_slugs:
                slug = f"{slug}-{target.server_id[-4:]}"
            used_slugs.add(slug)
            for tool_name, definition in listing:
                name = namespaced_tool_name(slug, tool_name)
                if name in self._routes:
                    continue
                self._definitions[name] = ToolDefinition(
                    name=name,
                    description=f"[{target.name}] {definition.description}".strip(),
                    parameters_json_schema=definition.parameters_json_schema,
                )
                self._routes[name] = (target, tool_name)
        return self

    async def _list(self, target: McpServerTarget) -> list[tuple[str, ToolDefinition]]:
        cached = _listing_cache.get(target.server_id)
        if (
            cached
            and cached.updated_at == target.updated_at
            and time.monotonic() - cached.fetched_at < CACHE_TTL_SECONDS
        ):
            return cached.tools
        try:
            async with asyncio.timeout(LIST_TIMEOUT_SECONDS):
                result = await (await self._client(target)).list_tools()
        except Exception as err:  # an unreachable or hostile server must not break the turn
            logger.warning("MCP server '%s' unavailable for this turn: %s", target.name, err)
            await self._drop(target)
            return []
        tools = [
            (
                tool.name,
                ToolDefinition(
                    name=tool.name,
                    description=tool.description or "",
                    parameters_json_schema=dict(
                        tool.input_schema or {"type": "object", "properties": {}}
                    ),
                ),
            )
            for tool in result.tools
        ]
        _listing_cache[target.server_id] = _Listing(time.monotonic(), target.updated_at, tools)
        return tools

    def definitions(self) -> list[ToolDefinition]:
        return list(self._definitions.values())

    def owns(self, name: str) -> bool:
        return name in self._routes

    async def call(self, name: str, arguments: dict[str, Any]) -> str:
        target, tool_name = self._routes[name]
        last_error: Exception | None = None
        for attempt in range(2):  # a pooled connection may have died since it was opened
            try:
                async with asyncio.timeout(CALL_TIMEOUT_SECONDS):
                    result = await (await self._client(target)).call_tool(tool_name, arguments)
                return render_result(result)
            except TimeoutError:
                await self._drop(target)
                return f"MCP tool error: '{tool_name}' on '{target.name}' timed out."
            except Exception as err:
                last_error = err
                logger.warning("MCP call %s failed (attempt %d): %s", name, attempt + 1, err)
                await self._drop(target)
        return f"MCP tool error: could not call '{tool_name}' on '{target.name}': {last_error}"

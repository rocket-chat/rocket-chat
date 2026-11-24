"""Run MCP server tools as agent tools.

A turn borrows the tools of the MCP servers its caller may use - the organization's and the
member's own - namespaced ``mcp__<server>__<tool>``. Connections are opened per call with the
official ``mcp`` client; the tool list is cached briefly so a turn does not reconnect just to
advertise tools. A command server is offered from the tool schemas stored when it was probed,
so its process starts on the turn's first call, never just to advertise tools.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
import time
from collections.abc import Callable, Iterable
from contextlib import AbstractAsyncContextManager, AsyncExitStack
from dataclasses import dataclass, field, replace
from typing import Any

import httpx2
from agent_core.guidance import server_guidance
from agent_core.tools.clamp import clamp_output
from agent_core.tools.safe_http import UnsafeURLError, resolve_public_target
from config_engine.crypto import CredentialCipher
from mcp import Client
from mcp.client.sse import sse_client
from mcp.client.streamable_http import streamable_http_client

from specifications.interfaces.agent import ServerGuidance
from specifications.interfaces.llm import ToolDefinition

logger = logging.getLogger(__name__)

TOOL_NAME_PREFIX = "mcp"
SEPARATOR = "__"
# Providers reject tool names outside [a-zA-Z0-9_-]{1,64}.
MAX_TOOL_NAME_LENGTH = 64
LIST_TIMEOUT_SECONDS = 8.0
CALL_TIMEOUT_SECONDS = 120.0
CACHE_TTL_SECONDS = 300.0
# A tool's input schema is stored with the server (so a command server is offered without
# starting it) up to this size; a larger one is listed live.
MAX_STORED_SCHEMA_CHARS = 32_000
# The MCP runner refuses a process past its per-organization limit and asks to retry shortly:
# a call waits these delays (seconds) for a slot before it reports the runner as full.
CAPACITY_RETRY_DELAYS = (2.0, 4.0, 8.0)
# What the model sees is bounded by the dispatcher, which offloads any result past the
# organization's threshold to a file and keeps its head and tail. Clamping here first cut a
# one-line JSON answer (Prometheus, most APIs) to 2,000 characters before it could be saved, so
# this only bounds what a server can make the platform hold: the session file limit (4 MiB).
MAX_RESULT_CHARS = 4 * 1024 * 1024
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
    # The administrator's usage guidance (markdown), empty for most servers.
    guidance: str = ""
    # The tools stored when the server was last probed, with their schemas; empty when any
    # schema is missing (the server is then listed live).
    stored_tools: tuple[tuple[str, ToolDefinition], ...] = field(default=(), repr=False)


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
                guidance=str(_field(row, "guidance", "") or ""),
                stored_tools=stored_definitions(_field(row, "tools", []) or []),
            )
        )
    return targets


def stored_definitions(tools: Iterable[Any]) -> tuple[tuple[str, ToolDefinition], ...]:
    """A server's stored tools as definitions, or nothing when one lacks its schema."""
    found: list[tuple[str, ToolDefinition]] = []
    for tool in tools:
        schema = tool.get("input_schema") if isinstance(tool, dict) else None
        if not isinstance(schema, dict) or not tool.get("name"):
            return ()
        definition = ToolDefinition(
            name=str(tool["name"]),
            description=str(tool.get("description") or ""),
            parameters_json_schema=schema,
        )
        found.append((str(tool["name"]), definition))
    return tuple(found)


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
        headers["Authorization"] = f"{_authorization_scheme(auth.get('token_type'))} {token}"
    return headers


def _authorization_scheme(token_type: object) -> str:
    # token_type is case-insensitive (RFC 6749 §5.1) but some servers match the scheme exactly:
    # Sentry's MCP (workers-oauth-provider) issues "bearer" and accepts only "Bearer ".
    scheme = str(token_type or "").strip()
    return "Bearer" if scheme.lower() in ("", "bearer") else scheme


def leaf_messages(error: BaseException) -> list[str]:
    """The messages of the exceptions an exception group wraps, however deeply."""
    nested = getattr(error, "exceptions", None)
    if not nested:
        return [str(error)]
    return [message for inner in nested for message in leaf_messages(inner)]


def is_unauthorized(err: BaseException) -> bool:
    """Whether a failure is the server refusing the credentials (HTTP 401), however wrapped."""
    pending: list[BaseException] = [err]
    seen: set[int] = set()
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        response = getattr(current, "response", None)
        if getattr(response, "status_code", None) == 401:
            return True
        if isinstance(current, BaseExceptionGroup):
            pending.extend(current.exceptions)
        for linked in (current.__cause__, current.__context__):
            if linked is not None and linked is not current:
                pending.append(linked)
    return False


def connect_remote(target: McpServerTarget) -> AbstractAsyncContextManager[Client]:
    """Open an MCP client session to a remote server."""
    if target.transport == "sse":
        return Client(sse_client(target.url, headers=target.headers))
    http_client = httpx2.AsyncClient(
        headers=target.headers,
        timeout=CALL_TIMEOUT_SECONDS,
        event_hooks={"response": [_raise_when_refused]},
    )
    return Client(streamable_http_client(target.url, http_client=http_client))


async def _raise_when_refused(response: httpx2.Response) -> None:
    # mcp 2.2 turns every HTTP error into a bare JSON-RPC -32603, losing the 401 that tells a
    # refused token (refresh it, or sign in again) apart from a broken server.
    if response.status_code == 401:
        response.raise_for_status()


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
    if getattr(result, "is_error", False):
        return clamp_output(f"MCP tool error: {text}")
    if len(text) > MAX_RESULT_CHARS:
        cut = len(text) - MAX_RESULT_CHARS
        text = f"{text[:MAX_RESULT_CHARS]}\n... [{cut:,} characters cut: the result exceeded {MAX_RESULT_CHARS:,}]"
    return text


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


class ProbedTools(list[dict[str, Any]]):
    """The tools a probe listed, carrying the usage ``instructions`` the server gave when the
    connection was opened (the MCP initialize result). A list, so callers that only want the
    tools are unaffected."""

    instructions: str = ""

    def __init__(self, tools: Iterable[dict[str, Any]], instructions: str | None = None) -> None:
        super().__init__(tools)
        self.instructions = (instructions or "").strip()


def summarize_tool(tool: Any) -> dict[str, Any]:
    """The stored (display) form of a tool; execution re-lists the live schema."""
    stored = {
        "name": tool.name,
        "description": tool.description or "",
        "parameters_summary": summarize_schema(tool.input_schema),
    }
    schema = dict(tool.input_schema or {"type": "object", "properties": {}})
    if len(json.dumps(schema)) <= MAX_STORED_SCHEMA_CHARS:
        stored["input_schema"] = schema
    return stored


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
                instructions = client.instructions
    except TimeoutError:
        return "offline", [], f"No MCP response from {target.url} within {timeout_seconds:g}s"
    except (OSError, httpx2.TransportError, httpx2.TimeoutException) as err:
        return "offline", [], f"Connection failed: {err}"
    except Exception as err:  # the SDK wraps transport failures in exception groups
        if is_unauthorized(err):
            return "error", [], "The server refused the credentials (HTTP 401)."
        reason = "; ".join(leaf_messages(err)) or str(err)
        logger.info("MCP probe of %s failed: %s", target.url, reason)
        return "error", [], reason
    return "connected", ProbedTools((summarize_tool(t) for t in result.tools), instructions), None


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
        offered: Callable[[str], bool] | None = None,
    ) -> None:
        self._targets = {t.server_id: t for t in targets}
        self._connector = connector
        self._stdio_connector = stdio_connector
        self._guard = guard
        self._definitions: dict[str, ToolDefinition] = {}
        self._routes: dict[str, tuple[McpServerTarget, str]] = {}
        # server id -> the <server> of its tool names, for the guidance the turn is given.
        self._slugs: dict[str, str] = {}
        self._pool: dict[str, _Connection] = {}
        self._opening: dict[str, asyncio.Lock] = {}
        # Servers that refused this turn's credentials (HTTP 401): the caller may refresh them.
        self.unauthorized: set[str] = set()
        # Servers whose tools could not be listed this turn (names, for the agent to be told).
        self.unavailable: list[str] = []
        # Whether a server (by the <server> of its tool names) may be used at all this turn: the
        # agent's tool list decides, so a server it cannot call is neither listed nor started.
        self._offered = offered

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
        """Discover the offered servers' tools concurrently; an unreachable server is skipped."""
        slugs = self._assign_slugs()
        targets = [
            target
            for target in self._targets.values()
            if self._offered is None or self._offered(slugs[target.server_id])
        ]
        self.unavailable = []
        listings = await asyncio.gather(*(self._list(t) for t in targets))
        for target, listing in zip(targets, listings, strict=True):
            slug = slugs[target.server_id]
            self._slugs[target.server_id] = slug
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

    def _assign_slugs(self) -> dict[str, str]:
        """The <server> of each server's tool names, unique within the turn."""
        slugs: dict[str, str] = {}
        for target in self._targets.values():
            slug = slugify(target.name)
            while slug in slugs.values():
                slug = f"{slug}-{target.server_id[-4:]}"
            slugs[target.server_id] = slug
        return slugs

    async def _list(self, target: McpServerTarget) -> list[tuple[str, ToolDefinition]]:
        if target.transport == STDIO and target.stored_tools:
            return list(target.stored_tools)
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
            if is_unauthorized(err):
                self.unauthorized.add(target.server_id)
            else:
                self.unavailable.append(target.name)
            await self._drop(target)
            return []
        if target.transport == STDIO:
            # Listing must not hold a runner process for the rest of the turn: the first call
            # starts it again.
            await self._drop(target)
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

    async def retry(self, server_id: str, headers: dict[str, str]) -> bool:
        """List a server again with fresh credentials; whether its tools are now available."""
        target = self._targets.get(server_id)
        if target is None:
            return False
        await self._drop(target)
        self._targets[server_id] = replace(target, headers=headers)
        _listing_cache.pop(server_id, None)
        self.unauthorized.discard(server_id)
        await self.load()
        return server_id not in self.unauthorized and any(
            routed.server_id == server_id for routed, _ in self._routes.values()
        )

    def definitions(self) -> list[ToolDefinition]:
        return list(self._definitions.values())

    def guidance(self) -> list[ServerGuidance]:
        """The usage guidance of the servers that offered tools this turn (most have none)."""
        offering = {target.server_id for target, _ in self._routes.values()}
        found = (
            server_guidance(slug, self._targets[server_id].guidance)
            for server_id, slug in self._slugs.items()
            if server_id in offering
        )
        return [entry for entry in found if entry is not None]

    def owns(self, name: str) -> bool:
        return name in self._routes

    async def call(self, name: str, arguments: dict[str, Any]) -> str:
        target, tool_name = self._routes[name]
        last_error: Exception | None = None
        waits = iter(CAPACITY_RETRY_DELAYS)
        failures = 0
        while failures < 2:  # a pooled connection may have died since it was opened
            try:
                async with asyncio.timeout(CALL_TIMEOUT_SECONDS):
                    result = await (await self._client(target)).call_tool(tool_name, arguments)
                return render_result(result)
            except TimeoutError:
                await self._drop(target)
                return f"MCP tool error: '{tool_name}' on '{target.name}' timed out."
            except Exception as err:
                last_error = err
                await self._drop(target)
                if is_at_capacity(err) and (wait := next(waits, None)) is not None:
                    logger.info("MCP runner full for %s; retrying in %.0fs", name, wait)
                    await asyncio.sleep(wait)
                    continue
                failures += 1
                logger.warning("MCP call %s failed (attempt %d): %s", name, failures, err)
        if last_error is not None and is_at_capacity(last_error):
            return (
                f"MCP tool error: '{target.name}' could not start: the MCP runner is running as "
                "many command servers as the organization allows. Try again in a minute; if it "
                "is still full, answer without it and say which source you could not reach."
            )
        return f"MCP tool error: could not call '{tool_name}' on '{target.name}': {last_error}"


def is_at_capacity(err: BaseException) -> bool:
    """Whether the MCP runner refused to start a process because it is full."""
    return "at capacity" in str(err)

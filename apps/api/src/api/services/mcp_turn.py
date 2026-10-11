"""MCP server rows -> the tools a turn may use (shared by the MCP routers and the turn engine)."""

import logging
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime
from typing import Any

from config_engine.crypto import CredentialCipher
from fastapi import (
    Request,
)
from mcp import Client
from sqlalchemy import or_, select

from api.db.models import (
    McpServerEntityModel,
)
from api.services.mcp_engine import (
    OAUTH_TOKEN_KEYS,
    oauth_token_needs_refresh,
    refresh_oauth_tokens,
)
from api.services.mcp_member_tokens import McpMemberTokenStore, is_member_mode
from api.services.mcp_runtime import McpServerTarget, McpToolProvider
from api.services.mcp_runtime import build_targets as build_mcp_targets
from api.services.sandbox_stdio import sandbox_stdio_transport
from api.services.settings import (
    effective_settings,
)
from specifications.interfaces.sandbox import (
    SandboxDriverProtocol,
    SandboxStatus,
)

logger = logging.getLogger(__name__)


# --- MCP SERVER REGISTRY & ENCRYPTED AUTH STORAGE ---

_cipher = CredentialCipher()


# Development-only registry used when no database is configured, keyed by server id; every
# record carries its organization. There are no pre-seeded "built-in" servers: the sandbox file
# and git tools are the agent's own tools, not MCP servers, and a server that does not exist
# must never be listed as connected.
_mcp_servers_store: dict[str, dict[str, Any]] = {}


def _probe_timestamp() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


async def _rows_with_member_tokens(
    db_mgr: Any, org_id: str, user_id: str, rows: list[Any]
) -> list[Any]:
    """Give each per-member server the *caller's* tokens, and drop it for a caller with none.

    Such a server is shared configuration but personal credentials: the turn of a member who has
    not connected an account must not run with someone else's, and one member's listing must not
    be served to another (the id is made member-specific for that reason).
    """
    shared_mode = [r for r in rows if is_member_mode(_row_auth(r))]
    if not shared_mode:
        return rows
    store = McpMemberTokenStore(db_mgr)
    resolved: list[Any] = []
    for row in rows:
        auth = _row_auth(row)
        if not is_member_mode(auth):
            resolved.append(row)
            continue
        row_id = str(_row_value(row, "id"))
        tokens = await store.get(org_id, row_id, user_id)
        if not tokens:
            continue
        if oauth_token_needs_refresh({**auth, **tokens}):
            merged = {**auth, **tokens}
            if await refresh_oauth_tokens(merged):
                tokens = {k: merged[k] for k in OAUTH_TOKEN_KEYS if k in merged}
                await store.put(org_id, row_id, user_id, tokens)
        resolved.append(
            {
                "id": f"{row_id}:{user_id}",
                "name": _row_value(row, "name"),
                "transport": _row_value(row, "transport"),
                "scope": "org",
                "endpoint_or_command": _row_value(row, "endpoint_or_command"),
                "is_builtin": False,
                "updated_at": _row_value(row, "updated_at") or 0,
                "encrypted_auth": {
                    **{k: v for k, v in auth.items() if k not in OAUTH_TOKEN_KEYS},
                    **tokens,
                },
            }
        )
    return resolved


def _row_value(row: Any, name: str) -> Any:
    return row.get(name) if isinstance(row, dict) else getattr(row, name, None)


def _row_auth(row: Any) -> dict[str, Any]:
    return dict(_row_value(row, "encrypted_auth") or {})


async def _mcp_tools_for_turn(
    db_mgr: Any,
    org_id: str,
    user_id: str,
    session_id: str | None = None,
    driver: SandboxDriverProtocol | None = None,
) -> McpToolProvider | None:
    """The turn's MCP tools: the org's servers plus the member's own, per the org's policy.

    stdio servers run in the session's sandbox, so they are offered once that sandbox is up.
    """
    policy = await effective_settings(db_mgr, org_id, "mcp")
    rows: list[Any]
    if db_mgr is not None:
        async with db_mgr.session(tenant_org_id=org_id) as session:
            result = await session.execute(
                select(McpServerEntityModel).where(
                    McpServerEntityModel.org_id == org_id,
                    or_(
                        McpServerEntityModel.scope == "org",
                        McpServerEntityModel.user_id == user_id,
                    ),
                )
            )
            rows = list(result.scalars().all())
            for row in rows:
                auth = dict(row.encrypted_auth or {})
                if oauth_token_needs_refresh(auth) and await refresh_oauth_tokens(auth):
                    row.encrypted_auth = auth
            await session.commit()
    else:
        rows = [
            s
            for s in _mcp_servers_store.values()
            if s.get("org_id") == org_id
            and (s.get("scope") == "org" or s.get("user_id") == user_id)
        ]
    rows = await _rows_with_member_tokens(db_mgr, org_id, user_id, rows)
    sandbox_ready = (
        driver is not None
        and session_id is not None
        and await driver.get_status(session_id) == SandboxStatus.RUNNING
    )
    targets = build_mcp_targets(
        rows,
        cipher=_cipher,
        allowed_org_transports=policy.get("allowed_transports") or [],
        allowed_user_transports=policy.get("user_allowed_transports") or [],
        stdio_available=sandbox_ready,
    )
    if not targets:
        return None

    def run_in_sandbox(target: McpServerTarget) -> AbstractAsyncContextManager[Client]:
        assert driver is not None and session_id is not None
        return Client(sandbox_stdio_transport(driver, session_id, target.url, target.headers))

    provider = (
        McpToolProvider(targets, stdio_connector=run_in_sandbox)
        if sandbox_ready
        else McpToolProvider(targets)
    )
    await provider.load()
    if provider.definitions():
        return provider
    await provider.aclose()
    return None


def _canonical_transport(transport: str) -> str:
    lowered = transport.strip().lower()
    return {"streamable_http": "http", "websocket": "ws"}.get(lowered, lowered)


def _member_token_store(request: Request) -> McpMemberTokenStore:
    store = getattr(request.app.state, "mcp_member_tokens", None)
    if store is None:
        store = McpMemberTokenStore(getattr(request.app.state, "db_manager", None))
        request.app.state.mcp_member_tokens = store
    return store


async def _load_mcp_auth(
    request: Request, org_id: str, server_id: str
) -> tuple[dict[str, Any], str]:
    db_mgr = getattr(request.app.state, "db_manager", None)
    if db_mgr:
        async with db_mgr.session(tenant_org_id=org_id) as session:
            entity = (
                await session.execute(
                    select(McpServerEntityModel).where(
                        McpServerEntityModel.id == server_id,
                        McpServerEntityModel.org_id == org_id,
                    )
                )
            ).scalar_one_or_none()
            if entity is None:
                raise LookupError("MCP server not found")
            return dict(entity.encrypted_auth or {}), str(entity.scope)
    record = _mcp_servers_store.get(server_id)
    if record is None or record.get("org_id") != org_id:
        raise LookupError("MCP server not found")
    return dict(record.get("encrypted_auth") or {}), str(record.get("scope", "org"))

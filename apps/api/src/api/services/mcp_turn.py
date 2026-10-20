"""MCP server rows -> the tools a turn may use (shared by the MCP routers and the turn engine)."""

import logging
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from config_engine.crypto import CredentialCipher
from fastapi import (
    Request,
)
from sqlalchemy import or_, select

from api.db.models import (
    McpServerEntityModel,
)
from api.services.mcp_engine import (
    OAUTH_TOKEN_KEYS,
    oauth_token_needs_refresh,
    refresh_oauth_tokens,
)
from api.services.mcp_member_env import apply_member_values, member_values, overridable_names
from api.services.mcp_member_tokens import McpMemberTokenStore, is_member_mode
from api.services.mcp_runner import connect_stdio, runner_config
from api.services.mcp_runtime import McpToolProvider, auth_headers
from api.services.mcp_runtime import build_targets as build_mcp_targets
from api.services.settings import (
    effective_settings,
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
    db_mgr: Any, org_id: str, user_id: str, rows: list[Any], needs_sign_in: list[str] | None = None
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
        if tokens and oauth_token_needs_refresh({**auth, **tokens}):
            merged = {**auth, **tokens}
            if await refresh_oauth_tokens(merged):
                tokens = {k: merged[k] for k in OAUTH_TOKEN_KEYS if k in merged}
                await store.put(org_id, row_id, user_id, tokens)
        if not tokens or not _signed_in(tokens):
            if needs_sign_in is not None:
                needs_sign_in.append(str(_row_value(row, "name")))
            continue
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


async def _rows_with_member_env(
    db_mgr: Any, org_id: str, user_id: str, rows: list[Any]
) -> list[Any]:
    """Run each command server with the caller's own values for the variables they may override.

    A server the caller must supply a value for, and has not, is dropped for them. The id is made
    member-specific whenever a value changes so that nothing cached for one member is served to
    another.
    """
    store = McpMemberTokenStore(db_mgr)
    resolved: list[Any] = []
    for row in rows:
        auth = _row_auth(row)
        if not overridable_names(auth) or _row_value(row, "scope") != "org":
            resolved.append(row)
            continue
        row_id = str(_row_value(row, "id"))
        own = await member_values(store, org_id, row_id, user_id)
        merged = apply_member_values(auth, own)
        if merged is None:
            continue
        resolved.append(
            {
                "id": f"{row_id}:{user_id}" if own else row_id,
                "name": _row_value(row, "name"),
                "transport": _row_value(row, "transport"),
                "scope": "org",
                "endpoint_or_command": _row_value(row, "endpoint_or_command"),
                "is_builtin": False,
                "updated_at": _row_value(row, "updated_at") or 0,
                "encrypted_auth": merged,
            }
        )
    return resolved


def _row_value(row: Any, name: str) -> Any:
    return row.get(name) if isinstance(row, dict) else getattr(row, name, None)


def _row_auth(row: Any) -> dict[str, Any]:
    return dict(_row_value(row, "encrypted_auth") or {})


# The status of an OAuth server whose account is missing or no longer accepted.
NEEDS_SIGN_IN = "needs_sign_in"


@dataclass
class McpTurnTools:
    """The MCP tools a turn may use, and the servers the member must sign in to first."""

    provider: McpToolProvider | None
    needs_sign_in: list[str] = field(default_factory=list)


def _is_oauth(row: Any) -> bool:
    return str(_row_value(row, "auth_type") or _row_auth(row).get("auth_type") or "") in (
        "oauth",
        "oauth2",
    )


def _signed_in(auth: dict[str, Any]) -> bool:
    """An account whose token is present and not past its expiry."""
    if not (auth.get("access_token_enc") or auth.get("access_token")):
        return False
    expires_at = auth.get("expires_at")
    return not expires_at or time.time() < float(expires_at)


def _usable_rows(rows: list[Any], needs_sign_in: list[str]) -> list[Any]:
    """Rows to connect to: an OAuth server without a working account is reported, not tried."""
    usable: list[Any] = []
    for row in rows:
        auth = _row_auth(row)
        if _is_oauth(row) and not is_member_mode(auth) and not _signed_in(auth):
            needs_sign_in.append(str(_row_value(row, "name")))
            if not isinstance(row, dict):
                row.status = NEEDS_SIGN_IN
            continue
        usable.append(row)
    return usable


async def _load_rows(db_mgr: Any, org_id: str, user_id: str, needs_sign_in: list[str]) -> list[Any]:
    """The org's servers and the member's own, with expiring accounts refreshed."""
    if db_mgr is None:
        return _usable_rows(
            [
                s
                for s in _mcp_servers_store.values()
                if s.get("org_id") == org_id
                and (s.get("scope") == "org" or s.get("user_id") == user_id)
            ],
            needs_sign_in,
        )
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
        usable = _usable_rows(rows, needs_sign_in)
        await session.commit()
    return usable


async def _refreshed_after_rejection(
    db_mgr: Any, org_id: str, user_id: str, server_id: str, rows: list[Any]
) -> dict[str, Any] | None:
    """The server's account refreshed after it refused the token (HTTP 401), else ``None``.

    A token can be refused before its expiry (revoked, rotated); one refresh is worth trying
    before asking the person to sign in again.
    """
    row = next((r for r in rows if str(_row_value(r, "id")) == server_id), None)
    if row is None:
        return None
    auth = _row_auth(row)
    if not await refresh_oauth_tokens(auth):
        return None
    if ":" in server_id and is_member_mode(auth):
        base_id = server_id.split(":", 1)[0]
        tokens = {k: auth[k] for k in OAUTH_TOKEN_KEYS if k in auth}
        await McpMemberTokenStore(db_mgr).put(org_id, base_id, user_id, tokens)
    else:
        await _save_server_auth(db_mgr, org_id, server_id, auth, status="connected")
    return auth


async def _save_server_auth(
    db_mgr: Any, org_id: str, server_id: str, auth: dict[str, Any] | None, status: str
) -> None:
    if db_mgr is None:
        record = _mcp_servers_store.get(server_id)
        if record is not None:
            if auth is not None:
                record["encrypted_auth"] = auth
            record["status"] = status
        return
    async with db_mgr.session(tenant_org_id=org_id) as session:
        entity = (
            await session.execute(
                select(McpServerEntityModel).where(
                    McpServerEntityModel.id == server_id, McpServerEntityModel.org_id == org_id
                )
            )
        ).scalar_one_or_none()
        if entity is not None:
            if auth is not None:
                entity.encrypted_auth = auth
            entity.status = status
            await session.commit()


async def _recover_rejected(
    provider: McpToolProvider,
    db_mgr: Any,
    org_id: str,
    user_id: str,
    rows: list[Any],
    needs_sign_in: list[str],
) -> None:
    """Refresh and retry each server that refused its token; report the ones that stay refused."""
    names = {str(_row_value(r, "id")): str(_row_value(r, "name")) for r in rows}
    for server_id in list(provider.unauthorized):
        auth = await _refreshed_after_rejection(db_mgr, org_id, user_id, server_id, rows)
        if auth is not None and await provider.retry(server_id, auth_headers(auth, _cipher)):
            continue
        needs_sign_in.append(names.get(server_id, server_id))
        if ":" not in server_id:
            await _save_server_auth(db_mgr, org_id, server_id, None, status=NEEDS_SIGN_IN)


async def _mcp_tools_for_turn(
    db_mgr: Any,
    org_id: str,
    user_id: str,
) -> McpTurnTools:
    """The turn's MCP tools: the org's servers plus the member's own, per the org's policy.

    Command (stdio) servers run on the MCP runner, so they are offered when one is deployed.
    A server the member must sign in to is named instead of silently missing, so the agent can
    say so rather than work without it.
    """
    policy = await effective_settings(db_mgr, org_id, "mcp")
    needs_sign_in: list[str] = []
    rows = await _load_rows(db_mgr, org_id, user_id, needs_sign_in)
    rows = await _rows_with_member_tokens(db_mgr, org_id, user_id, rows, needs_sign_in)
    rows = await _rows_with_member_env(db_mgr, org_id, user_id, rows)
    runner = runner_config()
    targets = build_mcp_targets(
        rows,
        cipher=_cipher,
        allowed_org_transports=policy.get("allowed_transports") or [],
        allowed_user_transports=policy.get("user_allowed_transports") or [],
        stdio_available=runner is not None,
    )
    if not targets:
        return McpTurnTools(None, needs_sign_in)

    provider = McpToolProvider(
        targets,
        stdio_connector=connect_stdio(runner, org_id) if runner is not None else None,
    )
    await provider.load()
    await _recover_rejected(provider, db_mgr, org_id, user_id, rows, needs_sign_in)
    if provider.definitions():
        return McpTurnTools(provider, needs_sign_in)
    await provider.aclose()
    return McpTurnTools(None, needs_sign_in)


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

"""MCP server registration, catalog, probing and policy."""

import logging
import time
import uuid
from typing import Any

from config_engine.crypto import CredentialCipher
from fastapi import (
    APIRouter,
    HTTPException,
    Request,
)
from mcp_runner.protocol import is_valid_env_name
from sqlalchemy import or_, select

from api.auth.access import _get_request_identity
from api.auth.origin import mcp_oauth_callback_url, public_origin
from api.db.models import (
    McpServerEntityModel,
)
from api.models import (
    CreateMcpServerRequest,
    McpAuthInput,
    McpRunnerStatus,
    McpServerResponseModel,
    McpToolItem,
    MemberEnvUpdate,
    MemberEnvVariable,
    MemberEnvView,
    ValidateMcpServerRequest,
    ValidateMcpServerResponse,
)
from api.services.audit import AuditService
from api.services.mcp_discovery import DiscoveryError as McpDiscoveryError
from api.services.mcp_discovery import discover as discover_mcp_oauth
from api.services.mcp_engine import (
    OAUTH_TOKEN_KEYS,
    probe_and_fetch_tools,
)
from api.services.mcp_member_env import (
    member_values,
    overridable_names,
    save_member_values,
)
from api.services.mcp_oauth_setup import MANUAL_CLIENT, needs_settling, settle_oauth
from api.services.mcp_runner import runner_status
from api.services.mcp_runtime import auth_headers as mcp_auth_headers
from api.services.mcp_turn import (
    _canonical_transport,
    _cipher,
    _mcp_servers_store,
    _member_token_store,
    _probe_timestamp,
)
from api.services.settings import (
    effective_settings,
)

logger = logging.getLogger(__name__)
# The server's own text is shown to the administrator in full; the guidance field takes less.
MAX_SERVER_INSTRUCTIONS_CHARS = 64_000

router = APIRouter(prefix="/v1", tags=["MCP"])


def _format_mcp_response(s: Any) -> McpServerResponseModel:
    if hasattr(s, "__table__"):
        tools_data = s.tools or []
        encrypted_auth = s.encrypted_auth or {}
        api_key_enc = encrypted_auth.get("api_key")
        headers_enc = encrypted_auth.get("headers", [])
        has_oauth = bool(
            encrypted_auth.get("access_token_enc")
            or encrypted_auth.get("access_token")
            or encrypted_auth.get("oauth_client_secret")
        )
        oauth_status = (
            "connected"
            if encrypted_auth.get("access_token_enc") or encrypted_auth.get("access_token")
            else ("pending" if s.auth_type in ("oauth", "oauth2") else "none")
        )
        s_id = s.id
        s_name = s.name
        s_desc = s.description or ""
        s_transport = s.transport
        s_endpoint = s.endpoint_or_command
        s_guidance = s.guidance or ""
        s_auth_type = s.auth_type
        s_builtin = s.is_builtin
        s_status = s.status
        s_last_probed = s.last_probed
        s_scope = s.scope
        s_user_id = s.user_id
    else:
        tools_data = s.get("tools", [])
        encrypted_auth = s.get("encrypted_auth", {})
        api_key_enc = s.get("api_key_encrypted") or encrypted_auth.get("api_key")
        headers_enc = s.get("headers_encrypted") or encrypted_auth.get("headers", [])
        has_oauth = bool(
            s.get("oauth_client_secret_encrypted")
            or encrypted_auth.get("oauth_client_secret")
            or encrypted_auth.get("access_token_enc")
            or encrypted_auth.get("access_token")
        )
        oauth_status = (
            "connected"
            if encrypted_auth.get("access_token_enc") or encrypted_auth.get("access_token")
            else ("pending" if s.get("auth_type") in ("oauth", "oauth2") else "none")
        )
        s_id = s["id"]
        s_name = s["name"]
        s_desc = s.get("description", "")
        s_transport = s.get("transport", "stdio")
        s_endpoint = s.get("endpoint_or_command", "")
        s_guidance = s.get("guidance", "")
        s_auth_type = s.get("auth_type", "none")
        s_builtin = s.get("is_builtin", False)
        s_status = s.get("status", "connected")
        s_last_probed = s.get("last_probed", "Just now")
        s_scope = s.get("scope", "org")
        s_user_id = s.get("user_id")

    tool_items = [
        McpToolItem(
            name=t["name"] if isinstance(t, dict) else getattr(t, "name", ""),
            description=(
                t.get("description") if isinstance(t, dict) else getattr(t, "description", "")
            )
            or "",
            parameters_summary=t.get("parameters_summary")
            if isinstance(t, dict)
            else getattr(t, "parameters_summary", None),
        )
        for t in tools_data
    ]

    headers_preview = []
    for h in headers_enc:
        headers_preview.append(
            {
                "name": h["name"],
                "value": CredentialCipher.fingerprint(h["value"])
                if h.get("is_secret") and h["value"]
                else h["value"],
                "is_secret": h.get("is_secret", False),
                "member_override": h.get("member_override", False),
            }
        )

    api_key_fp = None
    if api_key_enc:
        try:
            raw = _cipher.decrypt(api_key_enc)
            api_key_fp = CredentialCipher.fingerprint(raw)
        except Exception:
            api_key_fp = "***"

    return McpServerResponseModel(
        id=s_id,
        name=s_name,
        description=s_desc,
        transport=s_transport,
        endpoint_or_command=s_endpoint,
        guidance=s_guidance,
        auth_type=s_auth_type,
        scope=s_scope,
        user_id=s_user_id,
        is_builtin=s_builtin,
        status=s_status,
        tools_count=len(tool_items),
        last_probed=s_last_probed,
        tools=tool_items,
        has_api_key=bool(api_key_enc),
        api_key_fingerprint=api_key_fp,
        headers_preview=headers_preview,
        has_oauth_secret=has_oauth,
        oauth_status=oauth_status,
        oauth_mode=str(encrypted_auth.get("oauth_mode") or "org"),
        oauth_authorization_url=encrypted_auth.get("oauth_authorization_url"),
        header_name=encrypted_auth.get("header_name"),
        header_prefix=encrypted_auth.get("header_prefix"),
        oauth_token_url=encrypted_auth.get("oauth_token_url"),
        oauth_client_id=encrypted_auth.get("oauth_client_id"),
        oauth_scopes=[str(x) for x in encrypted_auth.get("oauth_scopes") or []],
        install_command=encrypted_auth.get("install_command"),
        overridable_env=overridable_names(encrypted_auth),
    )


@router.get("/mcp/servers", response_model=list[McpServerResponseModel])
async def list_mcp_servers(request: Request) -> list[McpServerResponseModel]:
    """List registered MCP servers scoped to tenant organization and current user."""
    org_id, user_id, _ = _get_request_identity(request)
    db_mgr = getattr(request.app.state, "db_manager", None)

    if db_mgr:
        async with db_mgr.session() as session:
            stmt = select(McpServerEntityModel).where(
                McpServerEntityModel.org_id == org_id,
                or_(McpServerEntityModel.scope == "org", McpServerEntityModel.user_id == user_id),
            )
            res = await session.execute(stmt)
            views = [_format_mcp_response(s) for s in res.scalars().all()]
    else:
        views = [
            _format_mcp_response(s)
            for s in _mcp_servers_store.values()
            if s.get("is_builtin")
            or (
                s.get("org_id") == org_id
                and (s.get("scope") == "org" or s.get("user_id") == user_id)
            )
        ]
    return await _with_member_connection_status(request, org_id, user_id, views)


async def _with_member_connection_status(
    request: Request, org_id: str, user_id: str, views: list[McpServerResponseModel]
) -> list[McpServerResponseModel]:
    """For servers where every member connects their own account, report *this* member's state."""
    member_mode = [v for v in views if v.oauth_mode == "member"]
    if member_mode:
        connected = await _member_token_store(request).connected_servers(org_id, user_id)
        for view in member_mode:
            view.oauth_status = "connected" if view.id in connected else "pending"
    store = _member_token_store(request)
    for view in views:
        if view.overridable_env:
            own = await member_values(store, org_id, view.id, user_id)
            view.member_env_set = [name for name in view.overridable_env if name in own]
    return views


@router.get("/mcp/runner", response_model=McpRunnerStatus)
async def mcp_runner_status(request: Request) -> McpRunnerStatus:
    """Whether command (stdio) MCP servers can run on this installation (the MCP runner pod)."""
    _get_request_identity(request)
    status = await runner_status()
    return McpRunnerStatus(
        configured=status.configured, available=status.available, detail=status.detail
    )


async def _org_server_auth(request: Request, org_id: str, server_id: str) -> dict[str, Any]:
    """The stored auth block of an organization server the caller can see, or 404."""
    db_mgr = getattr(request.app.state, "db_manager", None)
    if db_mgr:
        async with db_mgr.session(tenant_org_id=org_id) as session:
            entity = (
                await session.execute(
                    select(McpServerEntityModel).where(
                        McpServerEntityModel.id == server_id,
                        McpServerEntityModel.org_id == org_id,
                        McpServerEntityModel.scope == "org",
                    )
                )
            ).scalar_one_or_none()
        auth = dict(entity.encrypted_auth or {}) if entity else None
    else:
        record = _mcp_servers_store.get(server_id)
        found = (
            record is not None and record.get("org_id") == org_id and record.get("scope") == "org"
        )
        auth = dict((record or {}).get("encrypted_auth") or {}) if found else None
    if auth is None:
        raise HTTPException(status_code=404, detail="MCP server not found")
    return auth


@router.get("/mcp/servers/{server_id}/env", response_model=MemberEnvView)
async def get_member_env(server_id: str, request: Request) -> MemberEnvView:
    """The variables of an organization command server that the caller may set for themselves."""
    org_id, user_id, _ = _get_request_identity(request)
    auth = await _org_server_auth(request, org_id, server_id)
    own = await member_values(_member_token_store(request), org_id, server_id, user_id)
    defaults = {h["name"]: bool(h.get("value")) for h in auth.get("headers") or []}
    return MemberEnvView(
        variables=[
            MemberEnvVariable(name=n, has_default=defaults.get(n, False), overridden=n in own)
            for n in overridable_names(auth)
        ]
    )


@router.put("/mcp/servers/{server_id}/env", response_model=MemberEnvView)
async def put_member_env(server_id: str, body: MemberEnvUpdate, request: Request) -> MemberEnvView:
    """Set (or, with an empty value, clear) the caller's own values; they are stored encrypted."""
    org_id, user_id, _ = _get_request_identity(request)
    auth = await _org_server_auth(request, org_id, server_id)
    allowed = set(overridable_names(auth))
    unknown = sorted(set(body.values) - allowed)
    if unknown:
        raise HTTPException(
            status_code=422,
            detail=f"Not variables members may set for this server: {', '.join(unknown)}",
        )
    store = _member_token_store(request)
    encrypted = await member_values(store, org_id, server_id, user_id)
    for name, value in body.values.items():
        if value:
            encrypted[name] = _cipher.encrypt(value)
        else:
            encrypted.pop(name, None)
    await save_member_values(store, org_id, server_id, user_id, encrypted)
    await _audit_command_server(
        request, org_id, user_id, "stdio", "member values updated", server_id
    )
    return await get_member_env(server_id, request)


@router.post("/mcp/servers/validate", response_model=ValidateMcpServerResponse)
async def validate_mcp_server(
    req: ValidateMcpServerRequest,
    request: Request,
) -> ValidateMcpServerResponse:
    """Validate MCP connection and probe tools prior to saving."""
    org_id, user_id, roles = _get_request_identity(request)
    is_admin = "admin" in roles or "owner" in roles

    if req.id:
        try:
            stored = await _stored_server_for_update(request, org_id, user_id, is_admin, req.id)
        except HTTPException as err:
            return ValidateMcpServerResponse(
                valid=False, status="error", tools=[], error=str(err.detail)
            )
        if stored is not None:
            _keep_stored_secrets(req, stored)
    if req.transport == "stdio" and not is_admin:
        return ValidateMcpServerResponse(
            valid=False,
            status="error",
            tools=[],
            error="Only Organization Administrators are authorized to configure stdio MCP servers.",
        )

    try:
        _check_stdio_environment(req.transport, req.auth)
    except HTTPException as err:
        return ValidateMcpServerResponse(
            valid=False, status="error", tools=[], error=str(err.detail)
        )

    req_headers: dict[str, str] = {}
    if req.auth.api_key:
        hdr_name = req.auth.header_name or "Authorization"
        prefix = req.auth.header_prefix or "Bearer"
        req_headers[hdr_name] = f"{prefix} {req.auth.api_key}".strip()

    for h in req.auth.headers:
        req_headers[h.name] = h.value or ""

    status, tools_data, err_msg = await probe_and_fetch_tools(
        transport=req.transport,
        endpoint_or_command=req.endpoint_or_command,
        headers=req_headers,
        org_id=org_id,
        install_command=req.auth.install_command,
    )

    tools = [
        McpToolItem(
            name=t["name"],
            description=t.get("description", ""),
            parameters_summary=t.get("parameters_summary"),
        )
        for t in tools_data
    ]

    sign_in = None
    if status != "connected" and not req_headers:
        sign_in = await _sign_in_offered(req.transport, req.endpoint_or_command)
    return ValidateMcpServerResponse(
        valid=(status == "connected"),
        status=NEEDS_SIGN_IN if sign_in else status,
        tools=tools,
        instructions=getattr(tools_data, "instructions", "")[:MAX_SERVER_INSTRUCTIONS_CHARS],
        error=(
            "This server signs you in with your account: save it, then sign in."
            if sign_in == "automatic"
            else "This server signs you in with your account, but its provider needs an OAuth "
            "app created by hand: enter its client ID under Advanced."
            if sign_in == "needs_client"
            else err_msg
        ),
        sign_in=sign_in,
    )


async def _sign_in_offered(transport: str, endpoint: str) -> str | None:
    """Whether a remote server that refused us advertises OAuth, and if it needs setup."""
    if transport == "stdio" or not endpoint.startswith(("http://", "https://")):
        return None
    try:
        found = await discover_mcp_oauth(endpoint)
    except McpDiscoveryError:
        return None
    return (
        "automatic" if found.registration_url or found.client_metadata_supported else "needs_client"
    )


async def _enforce_mcp_policy(
    request: Request,
    org_id: str,
    user_id: str,
    scope: str,
    transport: str,
    replacing_id: str | None,
) -> None:
    """Apply the org's MCP policy (allowed transports, per-member server cap) or raise 400/403."""
    db_mgr = getattr(request.app.state, "db_manager", None)
    policy = await effective_settings(db_mgr, org_id, "mcp")
    key = "allowed_transports" if scope == "org" else "user_allowed_transports"
    allowed = {_canonical_transport(t) for t in policy.get(key) or []}
    if _canonical_transport(transport) not in allowed:
        raise HTTPException(
            status_code=400,
            detail=(
                f"The '{transport}' transport is not permitted for {scope}-scoped MCP servers "
                f"by this organization (mcp.{key}: {sorted(allowed)})."
            ),
        )
    if scope != "user":
        return

    limit = int(policy.get("max_user_servers") or 0)
    if db_mgr and getattr(db_mgr, "session_factory", None):
        async with db_mgr.session(tenant_org_id=org_id) as session:
            rows = await session.execute(
                select(McpServerEntityModel.id).where(
                    McpServerEntityModel.org_id == org_id,
                    McpServerEntityModel.user_id == user_id,
                    McpServerEntityModel.scope == "user",
                )
            )
            owned = {row[0] for row in rows}
    else:
        owned = {
            sid
            for sid, rec in _mcp_servers_store.items()
            if rec.get("scope") == "user" and rec.get("user_id") == user_id
        }
    if replacing_id not in owned and len(owned) >= limit:
        raise HTTPException(
            status_code=400,
            detail=f"You have reached the organization limit of {limit} personal MCP servers.",
        )


async def _audit_command_server(
    request: Request, org_id: str, user_id: str, transport: str, action: str, server_id: str
) -> None:
    """Command servers run programs on the platform, so changes to them are recorded."""
    if transport != "stdio":
        return
    await AuditService(getattr(request.app.state, "db_manager", None)).record(
        "settings_change",
        org_id=org_id,
        actor_user_id=user_id,
        result_summary=f"command MCP server {action}",
        metadata={"domain": "mcp_command_server", "server_id": server_id, "action": action},
    )


def _check_stdio_environment(transport: str, auth: McpAuthInput) -> None:
    """A command server is configured with environment variables, nothing else."""
    if transport != "stdio":
        if auth.install_command or any(h.member_override for h in auth.headers):
            raise HTTPException(
                status_code=422,
                detail="An install command and member overrides apply to command (stdio) servers only.",
            )
        return
    if auth.auth_type not in ("none", "custom_headers"):
        raise HTTPException(
            status_code=422,
            detail="A command (stdio) server takes environment variables, not request authentication.",
        )
    empty = [h.name for h in auth.headers if not h.value and not h.member_override]
    if empty:
        raise HTTPException(
            status_code=422,
            detail=f"No value for: {', '.join(empty)} (leave it empty only if members provide it).",
        )
    bad = [h.name for h in auth.headers if not is_valid_env_name(h.name)]
    if bad:
        raise HTTPException(
            status_code=422,
            detail=f"Not valid environment variable names (letters, digits, underscore): {', '.join(bad)}",
        )


def _authorise_registration(req: CreateMcpServerRequest, is_admin: bool) -> None:
    """Only administrators register organization-wide or stdio servers."""
    if req.scope == "org" and not is_admin:
        if "scope" not in req.model_fields_set:
            req.scope = "user"
        else:
            raise HTTPException(
                status_code=403,
                detail="Only Organization Administrators can register organization-wide MCP servers.",
            )

    if req.transport == "stdio" and not is_admin:
        raise HTTPException(
            status_code=403,
            detail="Only Organization Administrators can register stdio MCP servers for host security.",
        )


def _encrypted_auth_payload(req: CreateMcpServerRequest) -> dict[str, Any]:
    """The auth block as stored: every secret encrypted with AES-256-GCM."""
    auth = req.auth
    payload: dict[str, Any] = {
        "auth_type": auth.auth_type,
        "header_name": auth.header_name,
        "header_prefix": auth.header_prefix,
        "oauth_authorization_url": auth.oauth_authorization_url,
        "oauth_token_url": auth.oauth_token_url,
        "oauth_client_id": auth.oauth_client_id,
        "oauth_scopes": auth.oauth_scopes,
    }
    if auth.oauth_mode == "member":
        if req.scope != "org" or auth.auth_type not in ("oauth", "oauth2"):
            raise HTTPException(
                status_code=422,
                detail="Per-member accounts apply to organization servers that use OAuth.",
            )
        payload["oauth_mode"] = "member"

    if auth.api_key:
        payload["api_key"] = _cipher.encrypt(auth.api_key)
    payload["headers"] = [
        {
            "name": header.name,
            "value": _cipher.encrypt(header.value)
            if header.is_secret and header.value
            else header.value,
            "is_secret": header.is_secret,
            "member_override": header.member_override,
        }
        for header in auth.headers
    ]
    if auth.install_command and auth.install_command.strip():
        payload["install_command"] = auth.install_command.strip()
    if auth.oauth_client_secret:
        payload["oauth_client_secret"] = _cipher.encrypt(auth.oauth_client_secret)
    return payload


async def _stored_server_for_update(
    request: Request, org_id: str, user_id: str, is_admin: bool, server_id: str
) -> dict[str, Any] | None:
    """The stored record being replaced (``None`` for a new server), after the access check.

    Done before anything is probed or decrypted: a secret kept from the stored server must
    never be sent anywhere on behalf of a caller who may not modify that server.
    """
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
            return None
        _assert_may_modify(entity, user_id, is_admin)
        return {
            "endpoint_or_command": entity.endpoint_or_command,
            "transport": entity.transport,
            "encrypted_auth": dict(entity.encrypted_auth or {}),
        }
    record = _mcp_servers_store.get(server_id)
    if record is None or record.get("org_id") != org_id:
        return None
    if not is_admin and (
        record.get("scope") == "org"
        or (record.get("scope") == "user" and record.get("user_id") != user_id)
    ):
        raise HTTPException(status_code=403, detail="Forbidden")
    return record


def _open_secret(stored: str, is_secret: bool) -> str | None:
    if not is_secret:
        return stored
    try:
        return _cipher.decrypt(stored)
    except Exception:
        return None


def _keep_stored_secrets(
    req: CreateMcpServerRequest | ValidateMcpServerRequest, stored: dict[str, Any]
) -> None:
    """Fill a secret the edit left out (``null``) with the one already stored.

    Secrets are never sent back to the page, so an edit cannot show them; ``null`` means
    "unchanged" and an empty string means "none" (a variable members must provide). An HTTP server whose address changed keeps nothing: the old credential would
    otherwise be sent to a new host without anyone retyping it. A command server may keep its
    variables, since the administrator already chooses what runs.
    """
    previous = stored.get("encrypted_auth") or {}
    moved = req.transport != "stdio" and (
        req.endpoint_or_command != stored.get("endpoint_or_command")
        or req.transport != stored.get("transport")
    )
    if moved:
        return
    auth = req.auth
    if not auth.api_key and previous.get("api_key"):
        auth.api_key = _open_secret(previous["api_key"], True)
    if not auth.oauth_client_secret and previous.get("oauth_client_secret"):
        auth.oauth_client_secret = _open_secret(previous["oauth_client_secret"], True)
    before = {h["name"]: h for h in previous.get("headers") or []}
    for header in auth.headers:
        old = before.get(header.name)
        if header.value is None and old and old.get("value"):
            header.value = _open_secret(old["value"], bool(old.get("is_secret")))


# OAuth fields set up automatically; an edit that leaves the client unchanged keeps them.
_SETTLED_OAUTH_KEYS = (
    "oauth_client_method",
    "oauth_registered_redirect_uri",
    "oauth_resource",
)


def _carry_oauth_setup(
    auth_dict: dict[str, Any], req: CreateMcpServerRequest, stored: dict[str, Any] | None
) -> None:
    """Mark a client entered by hand as such, and keep an automatic setup across an edit."""
    previous = (stored or {}).get("encrypted_auth") or {}
    entered = req.auth.oauth_client_id
    if entered and entered == previous.get("oauth_client_id"):
        for key in _SETTLED_OAUTH_KEYS:
            if key in previous:
                auth_dict[key] = previous[key]
    elif entered:
        auth_dict["oauth_client_method"] = MANUAL_CLIENT


async def _discover_oauth_settings(
    req: CreateMcpServerRequest, auth_dict: dict[str, Any], request: Request
) -> None:
    """Fill in OAuth endpoints and a client when the server can describe itself.

    Nothing to type in: ask the server how to authorize (RFC 9728/8414) and set up a client
    for our public callback URL (RFC 7591 registration, or our metadata URL). A server that
    does neither keeps what was entered by hand; signing in then says what is missing.
    """
    redirect_uri = mcp_oauth_callback_url(request)
    if req.auth.auth_type not in ("oauth", "oauth2") or not needs_settling(auth_dict, redirect_uri):
        return
    try:
        settled = await settle_oauth(
            auth_dict, req.endpoint_or_command, redirect_uri, public_origin(request), _cipher
        )
    except McpDiscoveryError as err:
        logger.info("OAuth discovery for %s did not complete: %s", req.endpoint_or_command, err)
        return
    auth_dict.update(settled)


async def _tools_and_status(
    req: CreateMcpServerRequest, org_id: str, account: dict[str, Any] | None = None
) -> tuple[list[dict[str, Any]], str]:
    """The tools the caller supplied, or the ones the server reports when none were given."""
    if req.tools:
        return [tool.model_dump() for tool in req.tools], "connected"

    raw_headers: dict[str, str] = {}
    if req.auth.api_key:
        header_name = req.auth.header_name or "Authorization"
        prefix = req.auth.header_prefix or "Bearer"
        raw_headers[header_name] = f"{prefix} {req.auth.api_key}".strip()
    for header in req.auth.headers:
        raw_headers[header.name] = header.value or ""

    status, fetched_tools, _ = await probe_and_fetch_tools(
        transport=req.transport,
        endpoint_or_command=req.endpoint_or_command,
        headers=raw_headers,
        # The signed-in account, kept across the edit: without it every re-save of an OAuth
        # server probed anonymously and reported it broken.
        auth_data=account,
        org_id=org_id,
        install_command=req.auth.install_command,
    )
    return fetched_tools or [], status


# The status of an OAuth server nobody has signed in to yet (or whose account was disconnected).
NEEDS_SIGN_IN = "needs_sign_in"


def _awaits_sign_in(
    req: CreateMcpServerRequest,
    auth_dict: dict[str, Any],
    stored: dict[str, Any] | None,
    reconnected: bool,
    status: str,
) -> bool:
    """An OAuth server without an account yet: its probe was refused for want of a token."""
    if req.auth.auth_type not in ("oauth", "oauth2") or status == "connected":
        return False
    if auth_dict.get("oauth_mode") == "member":
        return True
    previous = (stored or {}).get("encrypted_auth") or {}
    return reconnected or not previous.get("access_token_enc")


def _connection_changed(req: CreateMcpServerRequest, stored: dict[str, Any] | None) -> bool:
    """Whether the edit points the server somewhere else, so tokens issued for the old one must go.

    An OAuth token belongs to one server and one client; keeping it after either changed would
    present it to a party that never issued it.
    """
    # A command server has no account to disconnect; its members' own values stay.
    if stored is None or req.transport == "stdio":
        return False
    previous = stored.get("encrypted_auth") or {}
    return (
        req.endpoint_or_command != stored.get("endpoint_or_command")
        or req.transport != stored.get("transport")
        or _differs(req.auth.oauth_client_id, previous.get("oauth_client_id"))
        or _differs(req.auth.oauth_token_url, previous.get("oauth_token_url"))
    )


def _differs(supplied: str | None, stored: str | None) -> bool:
    """A value the edit leaves out is unchanged; only one it sets differently counts."""
    return bool(supplied) and supplied != stored


def _assert_may_modify(entity: McpServerEntityModel, user_id: str, is_admin: bool) -> None:
    if is_admin:
        return
    if entity.scope == "org" or (entity.scope == "user" and entity.user_id != user_id):
        raise HTTPException(status_code=403, detail="Forbidden")


def _update_entity(
    entity: McpServerEntityModel,
    req: CreateMcpServerRequest,
    auth_dict: dict[str, Any],
    tools: list[dict[str, Any]],
    status: str,
    target_user_id: str | None,
    keep_oauth_tokens: bool = True,
) -> None:
    entity.name = req.name
    entity.description = req.description
    entity.transport = req.transport
    entity.endpoint_or_command = req.endpoint_or_command
    entity.guidance = req.guidance
    entity.auth_type = req.auth.auth_type
    # Re-saving the form must not log the connected account out.
    carried = {
        key: value
        for key, value in (entity.encrypted_auth or {}).items()
        if keep_oauth_tokens and key in OAUTH_TOKEN_KEYS and key not in auth_dict
    }
    entity.encrypted_auth = {**carried, **auth_dict}
    entity.tools = tools
    entity.status = status
    entity.last_probed = _probe_timestamp()
    entity.scope = req.scope
    entity.user_id = target_user_id
    entity.updated_at = int(time.time())


def _new_entity(
    server_id: str,
    org_id: str,
    req: CreateMcpServerRequest,
    auth_dict: dict[str, Any],
    tools: list[dict[str, Any]],
    status: str,
    target_user_id: str | None,
) -> McpServerEntityModel:
    now = int(time.time())
    return McpServerEntityModel(
        id=server_id,
        org_id=org_id,
        user_id=target_user_id,
        scope=req.scope,
        name=req.name,
        description=req.description,
        transport=req.transport,
        endpoint_or_command=req.endpoint_or_command,
        guidance=req.guidance,
        auth_type=req.auth.auth_type,
        encrypted_auth=auth_dict,
        tools=tools,
        status=status,
        last_probed=_probe_timestamp(),
        is_builtin=False,
        created_at=now,
        updated_at=now,
    )


@router.post("/mcp/servers", response_model=McpServerResponseModel)
async def register_or_update_mcp_server(
    req: CreateMcpServerRequest,
    request: Request,
) -> McpServerResponseModel:
    """Register or update an MCP server, securely encrypting secrets with AES-256-GCM."""
    org_id, user_id, roles = _get_request_identity(request)
    is_admin = "admin" in roles or "owner" in roles
    _authorise_registration(req, is_admin)
    stored = None
    if req.id:
        stored = await _stored_server_for_update(request, org_id, user_id, is_admin, req.id)
        if stored is not None:
            _keep_stored_secrets(req, stored)
    reconnected = _connection_changed(req, stored)
    for header in req.auth.headers:
        header.value = header.value or ""
    _check_stdio_environment(req.transport, req.auth)

    server_id = req.id or f"mcp-{uuid.uuid4().hex[:8]}"
    target_user_id = user_id if req.scope == "user" else None
    await _enforce_mcp_policy(request, org_id, user_id, req.scope, req.transport, req.id)

    auth_dict = _encrypted_auth_payload(req)
    _carry_oauth_setup(auth_dict, req, stored)
    await _discover_oauth_settings(req, auth_dict, request)
    account = None
    previous_auth = (stored or {}).get("encrypted_auth") or {}
    if not reconnected and previous_auth.get("access_token_enc"):
        account = {k: previous_auth[k] for k in OAUTH_TOKEN_KEYS if k in previous_auth}
    tools, status = await _tools_and_status(req, org_id, account)
    if _awaits_sign_in(req, auth_dict, stored, reconnected, status):
        status = NEEDS_SIGN_IN

    db_mgr = getattr(request.app.state, "db_manager", None)
    if not db_mgr:
        response = _format_mcp_response(
            _store_in_memory(server_id, org_id, req, auth_dict, tools, status, target_user_id)
        )
        if reconnected:
            await _member_token_store(request).delete_for_server(org_id, server_id)
        await _audit_command_server(
            request, org_id, user_id, req.transport, "registered", server_id
        )
        return response

    async with db_mgr.session() as session:
        result = await session.execute(
            select(McpServerEntityModel).where(
                McpServerEntityModel.id == server_id,
                McpServerEntityModel.org_id == org_id,
            )
        )
        entity = result.scalar_one_or_none()
        if entity:
            _assert_may_modify(entity, user_id, is_admin)
            _update_entity(
                entity,
                req,
                auth_dict,
                tools,
                status,
                target_user_id,
                keep_oauth_tokens=not reconnected,
            )
        else:
            entity = _new_entity(server_id, org_id, req, auth_dict, tools, status, target_user_id)
            session.add(entity)
        await session.commit()
        response = _format_mcp_response(entity)
    if reconnected:
        await _member_token_store(request).delete_for_server(org_id, server_id)
    await _audit_command_server(request, org_id, user_id, req.transport, "registered", server_id)
    return response


def _store_in_memory(
    server_id: str,
    org_id: str,
    req: CreateMcpServerRequest,
    auth_dict: dict[str, Any],
    tools: list[dict[str, Any]],
    status: str,
    target_user_id: str | None,
) -> dict[str, Any]:
    """The no-database fallback used by tests and single-process development."""
    record: dict[str, Any] = {
        "id": server_id,
        "org_id": org_id,
        "name": req.name,
        "description": req.description,
        "transport": req.transport,
        "endpoint_or_command": req.endpoint_or_command,
        "guidance": req.guidance,
        "auth_type": req.auth.auth_type,
        "encrypted_auth": auth_dict,
        "scope": req.scope,
        "user_id": target_user_id,
        "is_builtin": False,
        "status": status,
        "last_probed": _probe_timestamp(),
        "tools": tools,
    }
    _mcp_servers_store[server_id] = record
    return record


@router.delete("/mcp/servers/{server_id}")
async def delete_mcp_server(server_id: str, request: Request) -> dict[str, bool]:
    """Delete a registered MCP server."""
    org_id, user_id, roles = _get_request_identity(request)
    is_admin = "admin" in roles or "owner" in roles
    db_mgr = getattr(request.app.state, "db_manager", None)

    if db_mgr:
        async with db_mgr.session() as session:
            stmt = select(McpServerEntityModel).where(
                McpServerEntityModel.id == server_id,
                McpServerEntityModel.org_id == org_id,
            )
            res = await session.execute(stmt)
            entity = res.scalar_one_or_none()
            if not entity:
                raise HTTPException(status_code=404, detail="MCP server not found")
            if entity.is_builtin:
                raise HTTPException(status_code=400, detail="Cannot delete built-in MCP server")
            if entity.scope == "org" and not is_admin:
                raise HTTPException(status_code=403, detail="Forbidden")
            if entity.scope == "user" and entity.user_id != user_id and not is_admin:
                raise HTTPException(status_code=403, detail="Forbidden")

            await session.delete(entity)
            await session.commit()
            _mcp_servers_store.pop(server_id, None)
            return {"deleted": True}

    if server_id in _mcp_servers_store:
        del _mcp_servers_store[server_id]
        return {"deleted": True}
    raise HTTPException(status_code=404, detail="MCP server not found")


def _probed_status(
    status: str, error: str | None, auth_type: str | None, auth: dict[str, Any]
) -> str:
    """A refused OAuth server waits for a sign-in rather than being reported broken."""
    if status == "connected" or auth_type not in ("oauth", "oauth2"):
        return status
    signed_in = bool(auth.get("access_token_enc")) and auth.get("oauth_mode") != "member"
    if not signed_in or "401" in (error or ""):
        return NEEDS_SIGN_IN
    return status


@router.post("/mcp/servers/{server_id}/probe", response_model=McpServerResponseModel)
async def probe_mcp_server(server_id: str, request: Request) -> McpServerResponseModel:
    """Probe an MCP server and refresh its discovered tools."""
    org_id, _, _ = _get_request_identity(request)
    db_mgr = getattr(request.app.state, "db_manager", None)

    if db_mgr:
        async with db_mgr.session() as session:
            stmt = select(McpServerEntityModel).where(
                McpServerEntityModel.id == server_id,
                McpServerEntityModel.org_id == org_id,
            )
            res = await session.execute(stmt)
            entity = res.scalar_one_or_none()
            if not entity:
                if server_id in _mcp_servers_store:
                    record = _mcp_servers_store[server_id]
                    status, tools, _ = await probe_and_fetch_tools(
                        transport=record.get("transport", "stdio"),
                        endpoint_or_command=record.get("endpoint_or_command", ""),
                    )
                    record["status"] = status
                    record["last_probed"] = _probe_timestamp()
                    if tools:
                        record["tools"] = tools
                    return _format_mcp_response(record)
                raise HTTPException(status_code=404, detail="MCP server not found")

            auth_data = entity.encrypted_auth or {}
            req_headers = mcp_auth_headers(auth_data, _cipher)

            status, tools, error = await probe_and_fetch_tools(
                transport=entity.transport,
                endpoint_or_command=entity.endpoint_or_command,
                headers=req_headers,
                auth_data=auth_data,
                org_id=org_id,
                install_command=auth_data.get("install_command"),
            )
            entity.status = _probed_status(status, error, entity.auth_type, auth_data)
            entity.last_probed = _probe_timestamp()
            if tools:
                entity.tools = tools
            await session.commit()
            return _format_mcp_response(entity)

    if server_id not in _mcp_servers_store:
        raise HTTPException(status_code=404, detail="MCP server not found")
    record = _mcp_servers_store[server_id]
    status, tools, error = await probe_and_fetch_tools(
        transport=record.get("transport", "stdio"),
        endpoint_or_command=record.get("endpoint_or_command", ""),
        auth_data=record.get("encrypted_auth") or None,
    )
    record["status"] = _probed_status(
        status, error, record.get("auth_type"), record.get("encrypted_auth") or {}
    )
    record["last_probed"] = _probe_timestamp()
    if tools:
        record["tools"] = tools
    return _format_mcp_response(record)

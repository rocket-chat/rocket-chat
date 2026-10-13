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
from api.services.mcp_discovery import DiscoveryError as McpDiscoveryError
from api.services.mcp_discovery import discover as discover_mcp_oauth
from api.services.mcp_discovery import register_client as register_mcp_oauth_client
from api.services.mcp_engine import (
    OAUTH_TOKEN_KEYS,
    probe_and_fetch_tools,
)
from api.services.mcp_member_env import (
    member_values,
    overridable_names,
    save_member_values,
)
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
            guidance=t.get("guidance") if isinstance(t, dict) else getattr(t, "guidance", None),
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
    return await get_member_env(server_id, request)


@router.post("/mcp/servers/validate", response_model=ValidateMcpServerResponse)
async def validate_mcp_server(
    req: ValidateMcpServerRequest,
    request: Request,
) -> ValidateMcpServerResponse:
    """Validate MCP connection and probe tools prior to saving."""
    org_id, _, roles = _get_request_identity(request)
    is_admin = "admin" in roles or "owner" in roles

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
        req_headers[h.name] = h.value

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

    return ValidateMcpServerResponse(
        valid=(status == "connected"),
        status=status,
        tools=tools,
        error=err_msg,
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


async def _discover_oauth_settings(
    req: CreateMcpServerRequest, auth_dict: dict[str, Any], request: Request
) -> None:
    """Fill in OAuth endpoints and a client when the server can describe itself.

    Nothing to type in: ask the server how to authorize (RFC 9728/8414) and register a
    client (RFC 7591) when it allows it. A server that does not is configured by hand.
    """
    needs_discovery = (
        req.auth.auth_type in ("oauth", "oauth2")
        and req.endpoint_or_command.startswith(("http://", "https://"))
        and not (auth_dict.get("oauth_authorization_url") and auth_dict.get("oauth_token_url"))
    )
    if not needs_discovery:
        return
    redirect_uri = f"{str(request.base_url).rstrip('/')}/v1/mcp/oauth/callback"
    try:
        found = await discover_mcp_oauth(req.endpoint_or_command)
        auth_dict["oauth_authorization_url"] = found.authorization_url
        auth_dict["oauth_token_url"] = found.token_url
        if not auth_dict.get("oauth_scopes"):
            auth_dict["oauth_scopes"] = found.scopes or ""
        if not auth_dict.get("oauth_client_id"):
            registered = await register_mcp_oauth_client(found, redirect_uri)
            auth_dict["oauth_client_id"] = registered.client_id
            if registered.client_secret:
                auth_dict["oauth_client_secret"] = _cipher.encrypt(registered.client_secret)
    except McpDiscoveryError as err:
        logger.info("OAuth discovery for %s did not complete: %s", req.endpoint_or_command, err)


async def _tools_and_status(
    req: CreateMcpServerRequest, org_id: str
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
        raw_headers[header.name] = header.value

    status, fetched_tools, _ = await probe_and_fetch_tools(
        transport=req.transport,
        endpoint_or_command=req.endpoint_or_command,
        headers=raw_headers,
        org_id=org_id,
        install_command=req.auth.install_command,
    )
    return fetched_tools or [], status


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
        if key in OAUTH_TOKEN_KEYS and key not in auth_dict
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
    _check_stdio_environment(req.transport, req.auth)

    server_id = req.id or f"mcp-{uuid.uuid4().hex[:8]}"
    target_user_id = user_id if req.scope == "user" else None
    await _enforce_mcp_policy(request, org_id, user_id, req.scope, req.transport, req.id)

    auth_dict = _encrypted_auth_payload(req)
    await _discover_oauth_settings(req, auth_dict, request)
    tools, status = await _tools_and_status(req, org_id)

    db_mgr = getattr(request.app.state, "db_manager", None)
    if not db_mgr:
        return _format_mcp_response(
            _store_in_memory(server_id, org_id, req, auth_dict, tools, status, target_user_id)
        )

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
            _update_entity(entity, req, auth_dict, tools, status, target_user_id)
        else:
            entity = _new_entity(server_id, org_id, req, auth_dict, tools, status, target_user_id)
            session.add(entity)
        await session.commit()
        return _format_mcp_response(entity)


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

            status, tools, _ = await probe_and_fetch_tools(
                transport=entity.transport,
                endpoint_or_command=entity.endpoint_or_command,
                headers=req_headers,
                auth_data=auth_data,
                org_id=org_id,
                install_command=auth_data.get("install_command"),
            )
            entity.status = status
            entity.last_probed = _probe_timestamp()
            if tools:
                entity.tools = tools
            await session.commit()
            return _format_mcp_response(entity)

    if server_id not in _mcp_servers_store:
        raise HTTPException(status_code=404, detail="MCP server not found")
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

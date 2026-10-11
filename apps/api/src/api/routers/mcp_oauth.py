"""OAuth for MCP servers: authorize, callback, per-member tokens."""

import logging
from typing import Any
from urllib.parse import urlencode

from fastapi import (
    APIRouter,
    HTTPException,
    Query,
    Request,
    Response,
)
from fastapi.responses import RedirectResponse
from sqlalchemy import select

from api.auth.access import _get_request_identity
from api.auth.identity import is_production
from api.db.models import (
    McpServerEntityModel,
)
from api.services.mcp_discovery import DiscoveryError as McpDiscoveryError
from api.services.mcp_discovery import discover as discover_mcp_oauth
from api.services.mcp_discovery import register_client as register_mcp_oauth_client
from api.services.mcp_engine import (
    MCP_OAUTH_COOKIE,
    OAUTH_FLOW_TTL_SECONDS,
    OAuthAuthorization,
    create_oauth_authorization_url,
    exchange_oauth_code,
    probe_and_fetch_tools,
    store_oauth_tokens,
    take_oauth_state,
)
from api.services.mcp_member_tokens import is_member_mode
from api.services.mcp_turn import (
    _cipher,
    _load_mcp_auth,
    _mcp_servers_store,
    _member_token_store,
    _probe_timestamp,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1", tags=["MCP"])


def _may_authorize_mcp(
    scope: str,
    owner_id: str | None,
    user_id: str,
    roles: list[str],
    member_mode: bool = False,
) -> bool:
    """Who may connect an account to a server.

    The token becomes that server's credential for everyone who can use it, so an organization
    server is authorized by administrators only, and a personal one by its owner. An
    organization server in member mode is the exception: every member connects their own
    account, whose tokens are kept per member and never shared.
    """
    if scope == "org" and member_mode:
        return "readonly" not in roles
    if scope == "org":
        return "admin" in roles or "owner" in roles
    return owner_id == user_id


async def _build_mcp_authorization_url(server_id: str, request: Request) -> OAuthAuthorization:
    """Build the RFC 9728 / PKCE authorization redirect URL for an MCP server."""
    org_id, user_id, roles = _get_request_identity(request)
    redirect_uri = f"{str(request.base_url).rstrip('/')}/v1/mcp/oauth/callback"

    db_mgr = getattr(request.app.state, "db_manager", None)
    auth_config: dict[str, Any] = {}
    scope, owner_id = "org", None

    if db_mgr:
        async with db_mgr.session(tenant_org_id=org_id) as session:
            res = await session.execute(
                select(McpServerEntityModel).where(
                    McpServerEntityModel.id == server_id,
                    McpServerEntityModel.org_id == org_id,
                )
            )
            entity = res.scalar_one_or_none()
            if entity:
                auth_config = dict(entity.encrypted_auth or {})
                auth_config["endpoint_or_command"] = entity.endpoint_or_command
                scope, owner_id = entity.scope, entity.user_id
    else:
        rec = _mcp_servers_store.get(server_id)
        if rec and rec.get("org_id") == org_id:
            auth_config = dict(rec.get("encrypted_auth") or {})
            auth_config["endpoint_or_command"] = rec.get("endpoint_or_command", "")
            scope, owner_id = rec.get("scope", "org"), rec.get("user_id")

    if not auth_config:
        raise HTTPException(status_code=404, detail="MCP server not found")
    member_mode = scope == "org" and is_member_mode(auth_config)
    if not _may_authorize_mcp(scope, owner_id, user_id, roles, member_mode):
        raise HTTPException(
            status_code=403, detail="You are not allowed to connect an account to this server."
        )
    if not auth_config.get("oauth_authorization_url") or not auth_config.get("oauth_token_url"):
        # The server may advertise its own OAuth (RFC 9728/8414/7591); use that rather than
        # guessing paths. A member cannot change a shared connector, so in member mode this
        # must already have been settled by an administrator.
        if member_mode:
            raise HTTPException(
                status_code=409,
                detail="This connector is not fully set up yet; ask an administrator to open it once.",
            )
        auth_config = await _discover_and_persist_mcp_oauth(
            request, server_id, org_id, auth_config, redirect_uri
        )
    return create_oauth_authorization_url(
        server_id=server_id,
        user_id=user_id,
        auth_config=auth_config,
        redirect_uri=redirect_uri,
        org_id=org_id,
        member_mode=member_mode,
    )


async def _discover_and_persist_mcp_oauth(
    request: Request,
    server_id: str,
    org_id: str,
    auth_config: dict[str, Any],
    redirect_uri: str,
) -> dict[str, Any]:
    """Fill in a connector's OAuth endpoints and client from the server's own metadata."""
    endpoint = str(auth_config.get("endpoint_or_command", ""))
    if not endpoint.startswith(("http://", "https://")):
        raise HTTPException(
            status_code=400, detail="No OAuth authorization URL configured or discoverable."
        )
    try:
        found = await discover_mcp_oauth(endpoint)
        updated = dict(auth_config)
        updated["oauth_authorization_url"] = found.authorization_url
        updated["oauth_token_url"] = found.token_url
        if not updated.get("oauth_scopes"):
            updated["oauth_scopes"] = found.scopes or ""
        if not updated.get("oauth_client_id"):
            registered = await register_mcp_oauth_client(found, redirect_uri)
            updated["oauth_client_id"] = registered.client_id
            if registered.client_secret:
                updated["oauth_client_secret"] = _cipher.encrypt(registered.client_secret)
    except McpDiscoveryError as err:
        raise HTTPException(status_code=400, detail=str(err)) from err

    db_mgr = getattr(request.app.state, "db_manager", None)
    persisted = {k: v for k, v in updated.items() if k != "endpoint_or_command"}
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
            if entity is not None:
                entity.encrypted_auth = {**(entity.encrypted_auth or {}), **persisted}
    else:
        record = _mcp_servers_store.get(server_id)
        if record is not None:
            record["encrypted_auth"] = {**(record.get("encrypted_auth") or {}), **persisted}
    return updated


def _set_mcp_oauth_cookie(response: Response, authorization: OAuthAuthorization) -> None:
    response.set_cookie(
        key=MCP_OAUTH_COOKIE,
        value=authorization.cookie_value,
        max_age=OAUTH_FLOW_TTL_SECONDS,
        httponly=True,
        secure=is_production(),
        samesite="lax",
        path="/",
    )


@router.post("/mcp/servers/{server_id}/oauth/authorize")
async def authorize_mcp_server_oauth(
    server_id: str,
    request: Request,
    response: Response,
) -> dict[str, str]:
    """Generate RFC 9728 / PKCE authorization redirect URL for an MCP server."""
    try:
        authorization = await _build_mcp_authorization_url(server_id, request)
    except HTTPException:
        raise
    except Exception as err:
        raise HTTPException(status_code=400, detail=str(err)) from err
    _set_mcp_oauth_cookie(response, authorization)
    return {"authorization_url": authorization.url}


@router.get("/mcp/servers/{server_id}/oauth/authorize")
async def authorize_mcp_server_oauth_redirect(
    server_id: str,
    request: Request,
) -> RedirectResponse:
    """Browser-friendly GET that redirects the user to the provider's consent screen."""
    try:
        authorization = await _build_mcp_authorization_url(server_id, request)
    except HTTPException:
        raise
    except Exception as err:
        raise HTTPException(status_code=400, detail=str(err)) from err
    redirect = RedirectResponse(url=authorization.url)
    _set_mcp_oauth_cookie(redirect, authorization)
    return redirect


async def _finish_member_oauth(
    request: Request,
    server_id: str,
    org_id: str,
    user_id: str,
    roles: list[str],
    code: str,
    state_data: dict[str, Any],
) -> None:
    """Store the returning member's own tokens for an organization server (member mode)."""
    auth_dict, scope = await _load_mcp_auth(request, org_id, server_id)
    if scope != "org" or not is_member_mode(auth_dict):
        raise PermissionError("This server is not in per-member mode.")
    if not _may_authorize_mcp(scope, None, user_id, roles, member_mode=True):
        raise PermissionError("You may not connect an account to this server.")
    secret = (
        _cipher.decrypt(auth_dict["oauth_client_secret"])
        if auth_dict.get("oauth_client_secret")
        else None
    )
    tokens: dict[str, Any] = {}
    store_oauth_tokens(tokens, await exchange_oauth_code(code, state_data, secret))
    await _member_token_store(request).put(org_id, server_id, user_id, tokens)


@router.get("/mcp/oauth/callback")
async def mcp_oauth_callback(
    request: Request,
    code: str = Query(...),
    state: str = Query(...),
) -> RedirectResponse:
    """Exchange the PKCE code for tokens and store them encrypted on the authorized server.

    The state was minted for one authenticated member of one organization; the callback
    must come from that same member, or a forwarded link could bind the attacker's account
    to the victim's server (or the reverse).
    """
    org_id, user_id, roles = _get_request_identity(request)
    server_id = ""
    try:
        state_data = take_oauth_state(state, request.cookies.get(MCP_OAUTH_COOKIE))
        server_id = state_data["server_id"]
        if state_data.get("user_id") != user_id or state_data.get("org_id") != org_id:
            raise PermissionError("This authorization was started by a different account.")

        db_mgr = getattr(request.app.state, "db_manager", None)
        if state_data.get("member_mode"):
            await _finish_member_oauth(request, server_id, org_id, user_id, roles, code, state_data)
        elif db_mgr:
            async with db_mgr.session(tenant_org_id=org_id) as session:
                res = await session.execute(
                    select(McpServerEntityModel).where(
                        McpServerEntityModel.id == server_id,
                        McpServerEntityModel.org_id == org_id,
                    )
                )
                entity = res.scalar_one_or_none()
                if entity is None:
                    raise LookupError("MCP server not found")
                if not _may_authorize_mcp(entity.scope, entity.user_id, user_id, roles):
                    raise PermissionError("You may not connect an account to this server.")
                auth_dict = dict(entity.encrypted_auth or {})
                secret = (
                    _cipher.decrypt(auth_dict["oauth_client_secret"])
                    if auth_dict.get("oauth_client_secret")
                    else None
                )
                tokens = await exchange_oauth_code(code, state_data, secret)
                store_oauth_tokens(auth_dict, tokens)
                entity.encrypted_auth = auth_dict
                status, tools, _ = await probe_and_fetch_tools(
                    transport=entity.transport,
                    endpoint_or_command=entity.endpoint_or_command,
                    auth_data=auth_dict,
                )
                entity.status = status
                entity.last_probed = _probe_timestamp()
                if tools:
                    entity.tools = tools
                await session.commit()
        else:
            record = _mcp_servers_store.get(server_id)
            if record is None or record.get("org_id") != org_id:
                raise LookupError("MCP server not found")
            if not _may_authorize_mcp(
                record.get("scope", "org"), record.get("user_id"), user_id, roles
            ):
                raise PermissionError("You may not connect an account to this server.")
            auth_dict = dict(record.get("encrypted_auth") or {})
            secret = (
                _cipher.decrypt(auth_dict["oauth_client_secret"])
                if auth_dict.get("oauth_client_secret")
                else None
            )
            store_oauth_tokens(auth_dict, await exchange_oauth_code(code, state_data, secret))
            record["encrypted_auth"] = auth_dict
            record["status"] = "connected"

        done = RedirectResponse(url=f"/settings/mcp?status=connected&server_id={server_id}")
        done.delete_cookie(MCP_OAUTH_COOKIE, path="/")
        return done
    except Exception as err:
        logger.error("MCP OAuth callback failed: %s", err)
        # The reason is shown to the member; it never contains token material.
        return RedirectResponse(
            url=f"/settings/mcp?{urlencode({'status': 'error', 'error': str(err)})}"
        )

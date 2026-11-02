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
from api.auth.origin import mcp_oauth_callback_url, public_origin
from api.auth.roles import can_write
from api.db.models import (
    McpServerEntityModel,
)
from api.services.mcp_discovery import DiscoveryError as McpDiscoveryError
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
from api.services.mcp_oauth_setup import (
    CLIENT_METADATA_PATH,
    client_metadata_document,
    needs_settling,
    settle_oauth,
)
from api.services.mcp_turn import (
    _cipher,
    _load_mcp_auth,
    _mcp_servers_store,
    _member_token_store,
    _probe_timestamp,
)
from api.services.ownership import owner_of
from api.services.teams import viewer_for
from specifications.interfaces.ownership import (
    Owner,
    Viewer,
    can_manage,
    can_see,
    is_shared,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1", tags=["MCP"])


def _may_authorize_mcp(
    owner: Owner, viewer: Viewer, roles: list[str], member_mode: bool = False
) -> bool:
    """Who may connect an account to a server.

    The token becomes that server's credential for everyone who can use it, so an organization
    or team server is authorized by those who manage it only, and a personal one by its owner.
    A shared server in member mode is the exception: everyone who can use it connects their
    own account, whose tokens are kept per member and never shared.
    """
    if is_shared(owner.scope) and member_mode:
        return can_write(roles) and can_see(viewer, owner)
    if is_shared(owner.scope):
        return can_manage(viewer, owner)
    return owner.user_id == viewer.user_id


async def _build_mcp_authorization_url(server_id: str, request: Request) -> OAuthAuthorization:
    """Build the RFC 9728 / PKCE authorization redirect URL for an MCP server."""
    org_id, user_id, roles = _get_request_identity(request)
    redirect_uri = mcp_oauth_callback_url(request)

    db_mgr = getattr(request.app.state, "db_manager", None)
    auth_config: dict[str, Any] = {}
    owner = Owner("org")

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
                owner = owner_of(entity)
    else:
        rec = _mcp_servers_store.get(server_id)
        if rec and rec.get("org_id") == org_id:
            auth_config = dict(rec.get("encrypted_auth") or {})
            auth_config["endpoint_or_command"] = rec.get("endpoint_or_command", "")
            owner = owner_of(rec)

    if not auth_config:
        raise HTTPException(status_code=404, detail="MCP server not found")
    member_mode = is_shared(owner.scope) and is_member_mode(auth_config)
    viewer = await viewer_for(db_mgr, org_id, user_id, roles)
    if not _may_authorize_mcp(owner, viewer, roles, member_mode):
        raise HTTPException(
            status_code=403, detail="You are not allowed to connect an account to this server."
        )
    if needs_settling(auth_config, redirect_uri):
        # The server's own OAuth description (RFC 9728/8414) and a client set up for this
        # callback URL (RFC 7591, or our metadata URL); nothing is typed in on either side.
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
    try:
        updated = await settle_oauth(
            auth_config, endpoint, redirect_uri, public_origin(request), _cipher
        )
    except McpDiscoveryError as err:
        if auth_config.get("oauth_authorization_url") and auth_config.get("oauth_client_id"):
            # Set up before the callback URL was recorded and not rediscoverable: keep it.
            logger.info("Keeping the existing OAuth setup of %s: %s", server_id, err)
            return auth_config
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


@router.get(CLIENT_METADATA_PATH.removeprefix("/v1"))
async def mcp_oauth_client_metadata(request: Request) -> dict[str, Any]:
    """Rocket Chat's OAuth client metadata, for authorization servers that accept a URL as the
    client ID (no registration on the provider's side). Public: it holds no secret."""
    return client_metadata_document(public_origin(request), mcp_oauth_callback_url(request))


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
    """Store the returning member's own tokens for a shared server (member mode)."""
    auth_dict, owner = await _load_mcp_auth(request, org_id, server_id)
    if not is_shared(owner.scope) or not is_member_mode(auth_dict):
        raise PermissionError("This server is not in per-member mode.")
    viewer = await viewer_for(
        getattr(request.app.state, "db_manager", None), org_id, user_id, roles
    )
    if not _may_authorize_mcp(owner, viewer, roles, member_mode=True):
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
                viewer = await viewer_for(db_mgr, org_id, user_id, roles)
                if not _may_authorize_mcp(owner_of(entity), viewer, roles):
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
                    org_id=entity.org_id,
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
            viewer = await viewer_for(db_mgr, org_id, user_id, roles)
            if not _may_authorize_mcp(owner_of(record), viewer, roles):
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

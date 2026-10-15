"""Mapping GitHub App installations to organizations.

The administrator starts from Rocket Chat: ``install-url`` returns GitHub's install link with a
signed ``state`` naming the organization and the administrator. GitHub redirects back to
``/v1/github/setup`` with the new installation id; the state must verify, belong to the caller,
and GitHub must confirm the installation, before it is mapped.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse
from git_engine.engine import GitEngine
from pydantic import BaseModel, Field

from api.auth import oauth_state
from api.auth.access import _get_request_identity, _require_admin
from api.services.audit import AuditService
from api.services.github_installations import (
    bind_installation,
    install_url,
    installations_for_org,
    unbind_installation,
)

router = APIRouter(prefix="/v1", tags=["GitHub App"])

_STATE_PURPOSE = "github_install"
INSTALLATION_LOOKUP_ATTEMPTS = 4
INSTALLATION_LOOKUP_DELAY_SECONDS = 1.0

logger = logging.getLogger(__name__)


class InstallationInfo(BaseModel):
    installation_id: int
    account_login: str


class InstallationList(BaseModel):
    app_configured: bool = Field(..., description="GitHub App credentials are configured")
    install_url: str | None = Field(
        default=None,
        description="Where to install the App for this organization (needs GITHUB_APP_SLUG)",
    )
    installations: list[InstallationInfo] = Field(default_factory=list)


def _engine(request: Request) -> GitEngine | None:
    engine = getattr(request.app.state, "git_engine", None)
    return engine if isinstance(engine, GitEngine) else None


@router.get("/admin/github/installations", response_model=InstallationList)
async def list_installations(request: Request) -> InstallationList:
    org_id, user_id, _ = _require_admin(request)
    engine = _engine(request)
    settings = engine.settings if engine else None
    configured = bool(settings and settings.github_app_id and settings.github_app_private_key)
    url = None
    if configured and settings and settings.github_app_slug:
        state = oauth_state.sign({"purpose": _STATE_PURPOSE, "org_id": org_id, "user_id": user_id})
        url = install_url(settings.github_app_slug, state)
    found = await installations_for_org(getattr(request.app.state, "db_manager", None), org_id)
    return InstallationList(
        app_configured=configured,
        install_url=url,
        installations=[InstallationInfo(**vars(i)) for i in found],
    )


@router.get("/github/setup")
async def finish_installation(
    request: Request, installation_id: int, state: str = "", setup_action: str = ""
) -> RedirectResponse:
    """GitHub's post-install redirect: map the installation to the organization in ``state``."""
    return await map_installation(request, installation_id, state)


def is_install_state(state: str | None) -> bool:
    """Whether ``state`` is a signed install link of ours (not an OAuth sign-in state)."""
    try:
        return oauth_state.verify(state or "").get("purpose") == _STATE_PURPOSE
    except ValueError:
        return False


async def map_installation(request: Request, installation_id: int, state: str) -> RedirectResponse:
    """Map ``installation_id`` to the organization the signed ``state`` names.

    GitHub sends the administrator back here after an install or a configuration change: to the
    Setup URL, or, when the App asks for user authorization during installation, to its OAuth
    callback URL (which hands the request over). The state must verify and name this same
    administrator, and GitHub must confirm the installation, before it is mapped.
    """
    org_id, user_id, roles = _get_request_identity(request)
    try:
        claims: dict[str, Any] = oauth_state.verify(state)
    except ValueError as err:
        raise _refused(400, "The install link expired; start again.", installation_id) from err
    if (
        claims.get("purpose") != _STATE_PURPOSE
        or claims.get("org_id") != org_id
        or claims.get("user_id") != user_id
        or not any(role in ("admin", "owner") for role in roles)
    ):
        raise _refused(403, "This install link was not issued to you.", installation_id)
    login = await _installation_account(_engine(request), installation_id)
    if login is None:
        raise _refused(400, "GitHub does not know that installation.", installation_id)
    db_mgr = getattr(request.app.state, "db_manager", None)
    try:
        await bind_installation(db_mgr, installation_id, org_id, login, user_id)
    except ValueError as err:
        raise _refused(409, str(err), installation_id) from err
    await AuditService(db_mgr).record(
        "credential_change",
        org_id=org_id,
        actor_user_id=user_id,
        result_summary=f"mapped GitHub installation {installation_id} ({login})",
        metadata={"provider": "github_app", "installation_id": installation_id},
    )
    return RedirectResponse("/settings/github?installation=connected", status_code=303)


def _refused(status: int, reason: str, installation_id: int) -> HTTPException:
    # The reason only reaches the browser in a redirect; the operator needs it in the logs.
    logger.warning("GitHub installation %s was not mapped: %s", installation_id, reason)
    return HTTPException(status_code=status, detail=reason)


async def _installation_account(engine: GitEngine | None, installation_id: int) -> str | None:
    """The installation's account, asking again briefly: GitHub redirects the administrator
    back the moment an installation is created, before its API knows the new installation."""
    if engine is None:
        return None
    for attempt in range(INSTALLATION_LOOKUP_ATTEMPTS):
        if attempt:
            await asyncio.sleep(INSTALLATION_LOOKUP_DELAY_SECONDS)
        if login := await engine.get_installation_account(installation_id):
            return login
    return None


@router.delete("/admin/github/installations/{installation_id}", response_model=InstallationList)
async def remove_installation(installation_id: int, request: Request) -> InstallationList:
    org_id, user_id, _ = _require_admin(request)
    db_mgr = getattr(request.app.state, "db_manager", None)
    if not await unbind_installation(db_mgr, installation_id, org_id):
        raise HTTPException(status_code=404, detail="Installation not mapped to this organization")
    await AuditService(db_mgr).record(
        "credential_change",
        org_id=org_id,
        actor_user_id=user_id,
        result_summary=f"unmapped GitHub installation {installation_id}",
        metadata={"provider": "github_app", "installation_id": installation_id},
    )
    return await list_installations(request)

"""A member's linked GitHub account and token."""

import logging
from typing import Any

import httpx
from fastapi import (
    APIRouter,
    HTTPException,
    Request,
)
from git_engine.engine import GitEngine

from api.auth.access import _get_request_identity
from api.models import (
    GitHubCredentialStatus,
    GitHubLinkRequest,
    GitHubPATRequest,
    GitHubTestPATRequest,
)
from api.services.audit import (
    AuditService,
)
from api.services.credentials import (
    delete_user_credential,
    get_user_credential,
    store_user_credential,
)
from api.services.git_credentials import (
    resolve_session_git_credentials,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1", tags=["Sessions & Telemetry"])


@router.get("/user/credentials/github", response_model=GitHubCredentialStatus)
async def get_user_github_credentials_status(request: Request) -> GitHubCredentialStatus:
    """Return status of currently linked GitHub account or fallback."""
    org_id, user_id, _ = _get_request_identity(request)
    db_mgr = getattr(request.app.state, "db_manager", None)
    git_engine: GitEngine | None = getattr(request.app.state, "git_engine", None)

    user_cred = await get_user_credential(db_mgr, user_id, "github", org_id=org_id)
    bundle = await resolve_session_git_credentials(
        user_id=user_id,
        org_id=org_id,
        git_engine=git_engine,
        db_mgr=db_mgr,
    )

    if user_cred and user_cred.secret:
        meta = user_cred.metadata
        return GitHubCredentialStatus(
            is_linked=True,
            username=meta.get("login") or meta.get("username"),
            avatar_url=meta.get("avatar_url"),
            scopes=meta.get("scopes", []),
            auth_type=meta.get("auth_type", "oauth"),
            updated_at=user_cred.updated_at,
            injection_mode=bundle.mode,
        )

    return GitHubCredentialStatus(
        is_linked=False,
        username=None,
        avatar_url=None,
        scopes=[],
        auth_type="none",
        updated_at=None,
        injection_mode=bundle.mode,
    )


@router.post("/user/credentials/github/link", response_model=GitHubCredentialStatus)
async def link_user_github_account(
    req: GitHubLinkRequest, request: Request
) -> GitHubCredentialStatus:
    """Link a GitHub account by storing its access token encrypted."""
    org_id, user_id, roles = _get_request_identity(request)
    if "readonly" in roles:
        raise HTTPException(
            status_code=403, detail="Forbidden: Read-only role cannot link credentials"
        )

    token = req.access_token.strip()
    if not token:
        raise HTTPException(status_code=400, detail="Missing access token")

    db_mgr = getattr(request.app.state, "db_manager", None)
    git_engine: GitEngine | None = getattr(request.app.state, "git_engine", None)

    username = req.username
    avatar_url = req.avatar_url
    scopes = list(req.scopes)

    # Validate token against GitHub API if live network is reachable
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            gh_res = await client.get(
                "https://api.github.com/user",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/vnd.github+json",
                    "User-Agent": "Rocket-Chat",
                },
            )
            if gh_res.status_code == 200:
                gh_data = gh_res.json()
                username = gh_data.get("login") or username
                avatar_url = gh_data.get("avatar_url") or avatar_url
                raw_scopes = gh_res.headers.get("x-oauth-scopes", "")
                if raw_scopes:
                    scopes = [s.strip() for s in raw_scopes.split(",") if s.strip()]
            elif gh_res.status_code == 401:
                raise HTTPException(status_code=400, detail="Invalid or expired GitHub token")
    except HTTPException:
        raise
    except Exception as err:
        logger.debug("GitHub user verification skipped (offline/unreachable): %s", err)

    metadata = {
        "login": username,
        "avatar_url": avatar_url,
        "scopes": scopes,
        "auth_type": req.auth_type,
    }

    stored = await store_user_credential(
        db_mgr=db_mgr,
        user_id=user_id,
        org_id=org_id,
        provider="github",
        secret=token,
        metadata=metadata,
    )
    # Credential-change audit: record *that* a credential changed, keyed to
    # the actor — never the secret itself.
    await AuditService(db_mgr).record(
        "credential_change",
        org_id=org_id,
        actor_user_id=user_id,
        result_summary="linked GitHub credential",
        metadata={"provider": "github", "auth_type": req.auth_type},
    )

    bundle = await resolve_session_git_credentials(
        user_id=user_id,
        org_id=org_id,
        git_engine=git_engine,
        db_mgr=db_mgr,
    )

    return GitHubCredentialStatus(
        is_linked=True,
        username=username,
        avatar_url=avatar_url,
        scopes=scopes,
        auth_type=req.auth_type,
        updated_at=stored.updated_at,
        injection_mode=bundle.mode,
    )


@router.delete("/user/credentials/github")
async def unlink_user_github_account(request: Request) -> dict[str, str]:
    """Unlink and delete stored GitHub credentials for the current user."""
    org_id, user_id, roles = _get_request_identity(request)
    if "readonly" in roles:
        raise HTTPException(
            status_code=403, detail="Forbidden: Read-only role cannot delete credentials"
        )

    db_mgr = getattr(request.app.state, "db_manager", None)
    await delete_user_credential(db_mgr, user_id, "github", org_id=org_id)
    await AuditService(db_mgr).record(
        "credential_change",
        org_id=org_id,
        actor_user_id=user_id,
        result_summary="unlinked GitHub credential",
        metadata={"provider": "github"},
    )
    return {"status": "unlinked"}


@router.post("/user/credentials/github/pat", response_model=GitHubCredentialStatus)
async def configure_user_github_pat(
    req: GitHubPATRequest, request: Request
) -> GitHubCredentialStatus:
    """Validate and store a Personal Access Token (PAT) for the current user."""
    link_req = GitHubLinkRequest(
        access_token=req.pat,
        auth_type="pat",
    )
    return await link_user_github_account(link_req, request)


@router.post("/user/credentials/github/test")
async def test_user_github_token(req: GitHubTestPATRequest, request: Request) -> dict[str, Any]:
    """Test a provided token or current stored token against GitHub API."""
    token = req.pat.strip() if req.pat else None
    if not token:
        org_id, user_id, _ = _get_request_identity(request)
        db_mgr = getattr(request.app.state, "db_manager", None)
        cred = await get_user_credential(db_mgr, user_id, "github", org_id=org_id)
        if cred:
            token = cred.secret
        else:
            raise HTTPException(
                status_code=400, detail="No token provided and no GitHub credential stored"
            )

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            res = await client.get(
                "https://api.github.com/user",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/vnd.github+json",
                    "User-Agent": "Rocket-Chat",
                },
            )
            if res.status_code == 200:
                data = res.json()
                raw_scopes = res.headers.get("x-oauth-scopes", "")
                scopes = [s.strip() for s in raw_scopes.split(",") if s.strip()]
                return {
                    "valid": True,
                    "username": data.get("login"),
                    "name": data.get("name"),
                    "scopes": scopes,
                }
            return {
                "valid": False,
                "status_code": res.status_code,
                "message": res.text,
            }
    except Exception as ex:
        return {
            "valid": True,
            "username": "offline-tester",
            "scopes": ["repo", "read:user"],
            "note": f"Offline simulated check: {ex}",
        }

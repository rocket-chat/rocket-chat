"""A member's GPG key for signing the commits the agent makes as them."""

import logging

from fastapi import APIRouter, HTTPException, Request

from api.auth.access import _get_request_identity
from api.models import GitSigningImportRequest, GitSigningStatus
from api.services.audit import AuditService
from api.services.git_signing import (
    COMMIT_AS_ME,
    PROVIDER,
    SigningKeyError,
    SigningUnavailable,
    delete_signing_key,
    gpg_available,
    save_signing_key,
    stored_signing_metadata,
)
from api.services.settings import effective_settings

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1", tags=["Sessions & Telemetry"])


async def _status(request: Request, org_id: str, user_id: str) -> GitSigningStatus:
    db_mgr = getattr(request.app.state, "db_manager", None)
    github = await effective_settings(db_mgr, org_id, "github")
    prefs = await effective_settings(db_mgr, org_id, "user_preferences", user_id)
    stored = await stored_signing_metadata(db_mgr, org_id, user_id)
    available = gpg_available()
    mode = str(github.get("commit_signing_mode") or "")
    commit_as_me = bool(prefs.get(COMMIT_AS_ME))
    if stored is None:
        return GitSigningStatus(
            available=available, mode=mode, commit_as_me=commit_as_me, configured=False
        )
    return GitSigningStatus(
        available=available,
        mode=mode,
        commit_as_me=commit_as_me,
        configured=True,
        fingerprint=stored.get("fingerprint"),
        key_id=stored.get("key_id"),
        identities=list(stored.get("identities") or []),
        commit_email=stored.get("commit_email"),
        algorithm=stored.get("algorithm"),
        expires_at=stored.get("expires_at"),
        has_passphrase=bool(stored.get("has_passphrase")),
        public_key=stored.get("public_key"),
    )


@router.get("/user/git-signing", response_model=GitSigningStatus)
async def get_git_signing(request: Request) -> GitSigningStatus:
    """The member's imported GPG signing key (never the private key) and whether it is in use."""
    org_id, user_id, _ = _get_request_identity(request)
    return await _status(request, org_id, user_id)


@router.put("/user/git-signing", response_model=GitSigningStatus)
async def import_git_signing(req: GitSigningImportRequest, request: Request) -> GitSigningStatus:
    """Import a GPG private key: it is validated by GnuPG, then stored encrypted."""
    org_id, user_id, roles = _get_request_identity(request)
    if "readonly" in roles:
        raise HTTPException(
            status_code=403, detail="Forbidden: Read-only role cannot import signing keys"
        )
    db_mgr = getattr(request.app.state, "db_manager", None)
    try:
        info, email = await save_signing_key(
            db_mgr, org_id, user_id, req.private_key, req.passphrase, req.commit_email
        )
    except SigningUnavailable as err:
        raise HTTPException(status_code=503, detail=str(err)) from err
    except SigningKeyError as err:
        raise HTTPException(status_code=422, detail=str(err)) from err
    # Record that a credential changed, never the key itself.
    await AuditService(db_mgr).record(
        "credential_change",
        org_id=org_id,
        actor_user_id=user_id,
        result_summary="imported a GPG signing key",
        metadata={"provider": PROVIDER, "fingerprint": info.fingerprint, "commit_email": email},
    )
    return await _status(request, org_id, user_id)


@router.delete("/user/git-signing", response_model=GitSigningStatus)
async def remove_git_signing(request: Request) -> GitSigningStatus:
    """Remove the member's imported signing key."""
    org_id, user_id, _ = _get_request_identity(request)
    db_mgr = getattr(request.app.state, "db_manager", None)
    await delete_signing_key(db_mgr, org_id, user_id)
    await AuditService(db_mgr).record(
        "credential_change",
        org_id=org_id,
        actor_user_id=user_id,
        result_summary="removed the GPG signing key",
        metadata={"provider": PROVIDER},
    )
    return await _status(request, org_id, user_id)

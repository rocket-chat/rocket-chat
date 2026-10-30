"""A member's GitLab token: cloning (through the git gateway) and publishing to GitLab
repositories use it. It is stored sealed and checked against GitLab before it is kept."""

from __future__ import annotations

import httpx
from fastapi import APIRouter, HTTPException, Request
from git_engine.gitlab import GitLabSettings
from pydantic import BaseModel, Field

from api.auth.access import _get_request_identity
from api.services.credentials import (
    delete_user_credential,
    get_user_credential,
    store_user_credential,
)

router = APIRouter(prefix="/v1", tags=["GitLab"])

GITLAB_CHECK_TIMEOUT_SECONDS = 10.0


class GitLabTokenRequest(BaseModel):
    token: str = Field(..., min_length=8, max_length=500)


class GitLabCredentialStatus(BaseModel):
    linked: bool
    username: str = ""
    # The GitLab the token is for (the deployment's ``GITLAB_URL``).
    url: str


@router.get("/user/credentials/gitlab", response_model=GitLabCredentialStatus)
async def gitlab_status(request: Request) -> GitLabCredentialStatus:
    """Whether the caller added a GitLab token, and as whom."""
    org_id, user_id, _ = _get_request_identity(request)
    credential = await get_user_credential(
        getattr(request.app.state, "db_manager", None), user_id, "gitlab", org_id=org_id
    )
    return GitLabCredentialStatus(
        linked=credential is not None,
        username=str((credential.metadata if credential else {}).get("username") or ""),
        url=GitLabSettings.from_env().url,
    )


@router.put("/user/credentials/gitlab", response_model=GitLabCredentialStatus)
async def save_gitlab_token(body: GitLabTokenRequest, request: Request) -> GitLabCredentialStatus:
    """Check a personal (or project) access token with GitLab, then keep it sealed."""
    org_id, user_id, _ = _get_request_identity(request)
    settings = GitLabSettings.from_env()
    try:
        async with httpx.AsyncClient(timeout=GITLAB_CHECK_TIMEOUT_SECONDS) as client:
            response = await client.get(
                f"{settings.url}/api/v4/user", headers={"PRIVATE-TOKEN": body.token.strip()}
            )
    except httpx.HTTPError as err:
        raise HTTPException(status_code=502, detail="GitLab could not be reached") from err
    if response.status_code != 200:
        raise HTTPException(status_code=422, detail="GitLab did not accept this token")
    username = str(response.json().get("username") or "")
    await store_user_credential(
        getattr(request.app.state, "db_manager", None),
        user_id,
        org_id,
        "gitlab",
        body.token.strip(),
        {"username": username},
    )
    return GitLabCredentialStatus(linked=True, username=username, url=settings.url)


@router.delete("/user/credentials/gitlab", status_code=204)
async def remove_gitlab_token(request: Request) -> None:
    """Forget the caller's GitLab token."""
    org_id, user_id, _ = _get_request_identity(request)
    await delete_user_credential(
        getattr(request.app.state, "db_manager", None), user_id, "gitlab", org_id=org_id
    )

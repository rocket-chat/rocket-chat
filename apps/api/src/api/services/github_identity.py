"""Who a member's commits are attributed to when their GitHub account is linked.

The account's own username and e-mail are the default; the member can override either in the
git panel. GitHub's public profile e-mail is often empty, so the primary verified address (when
the token may read it) and finally the account's ``id+login@users.noreply.github.com`` address
are used: that one always belongs to the account, so GitHub attributes the commit to it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import httpx

from api.db.session import DatabaseSessionManager
from api.services.credentials import UserCredentialData, store_user_credential

logger = logging.getLogger(__name__)

GITHUB_API = "https://api.github.com"
IDENTITY_TIMEOUT_SECONDS = 5.0
DEFAULT_AUTHOR_NAME = "Rocket Developer"


def noreply_email(github_id: int | str, login: str) -> str:
    return f"{github_id}+{login}@users.noreply.github.com"


@dataclass(frozen=True)
class CommitIdentity:
    """The name and e-mail commits carry, and where each came from (for the git panel)."""

    name: str
    email: str
    name_source: str  # "git_panel" | "github" | "profile" | "default"
    email_source: str  # "git_panel" | "github" | "github_noreply" | "default"


def commit_identity(
    overrides: dict[str, Any], github: dict[str, Any] | None, user_id: str
) -> CommitIdentity:
    """Panel override first, then the linked GitHub account, then the member's profile."""
    github = github or {}
    if override := str(overrides.get("git_author_name") or "").strip():
        name, name_source = override, "git_panel"
    elif login := str(github.get("login") or "").strip():
        name, name_source = login, "github"
    elif full_name := str(overrides.get("full_name") or "").strip():
        name, name_source = full_name, "profile"
    else:
        name, name_source = DEFAULT_AUTHOR_NAME, "default"

    if override := str(overrides.get("git_author_email") or "").strip():
        email, email_source = override, "git_panel"
    elif primary := str(github.get("email") or "").strip():
        email, email_source = primary, "github"
    elif noreply := str(github.get("noreply_email") or "").strip():
        email, email_source = noreply, "github_noreply"
    else:
        email, email_source = f"{user_id}@rocket-chat.internal", "default"
    return CommitIdentity(name, email, name_source, email_source)


async def fetch_github_identity(token: str, api_url: str = GITHUB_API) -> dict[str, Any]:
    """The account's id, login, name and best e-mail; empty when GitHub cannot be reached."""
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "Rocket-Chat",
    }
    try:
        async with httpx.AsyncClient(timeout=IDENTITY_TIMEOUT_SECONDS) as client:
            user = await client.get(f"{api_url}/user", headers=headers)
            if user.status_code != 200:
                return {}
            profile = user.json()
            login = str(profile.get("login") or "")
            github_id = profile.get("id")
            if not login or github_id is None:
                return {}
            email = await _primary_email(client, api_url, headers) or str(
                profile.get("email") or ""
            )
    except (httpx.HTTPError, ValueError) as err:
        logger.debug("GitHub identity lookup failed: %s", err)
        return {}
    return {
        "github_id": github_id,
        "login": login,
        "name": profile.get("name") or login,
        "email": email.lower() if email else "",
        "noreply_email": noreply_email(github_id, login),
    }


async def _primary_email(client: httpx.AsyncClient, api_url: str, headers: dict[str, str]) -> str:
    """The primary verified address; needs the ``user:email`` scope, so absent is normal."""
    response = await client.get(f"{api_url}/user/emails", headers=headers)
    if response.status_code != 200:
        return ""
    for entry in response.json():
        if entry.get("primary") and entry.get("verified") and entry.get("email"):
            return str(entry["email"])
    return ""


async def with_github_identity(
    db_mgr: DatabaseSessionManager | None, credential: UserCredentialData, api_url: str
) -> UserCredentialData:
    """The credential with its identity filled in, fetching and remembering it once.

    Accounts linked before this existed have no id or e-mail on record. Best effort: without
    GitHub the credential is returned as it was and the login alone is used.
    """
    if credential.metadata.get("noreply_email"):
        return credential
    identity = await fetch_github_identity(credential.secret, api_url)
    if not identity:
        return credential
    merged = {**credential.metadata, **identity}
    return await store_user_credential(
        db_mgr,
        credential.user_id,
        credential.org_id,
        credential.provider,
        credential.secret,
        merged,
    )

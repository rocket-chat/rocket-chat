"""The credential the platform reads GitHub with for a session, inside or outside a turn.

The organization's App installation for the repository when it may act on it, else the
member's linked account, else the read credential the session's git bundle carries. The token
stays on the platform: it is used for the API calls and never returned to a client.
"""

from __future__ import annotations

from typing import Any

from git_engine.engine import pick_installation

from api.services.git_credentials import GitCredentialBundle, app_may_act_on
from api.services.git_gateway_env import github_api_url_of
from api.services.github_installations import installations_for_org
from api.services.settings import effective_settings
from specifications.interfaces.agent import GithubAccess

APP_ACTOR = "the organization's GitHub App"


def clean_repository(repository: str | None) -> str:
    return (
        (repository or "")
        .strip()
        .removeprefix("https://github.com/")
        .removesuffix(".git")
        .strip("/")
    )


def _member_access(git_bundle: GitCredentialBundle, api_url: str) -> GithubAccess:
    login = str(git_bundle.source_details.get("username") or "")
    return GithubAccess(
        git_bundle.user_token, api_url, f"@{login}" if login else "your linked GitHub account"
    )


def _bundle_actor(git_bundle: GitCredentialBundle, member: GithubAccess) -> str:
    if git_bundle.mode == "app":
        return APP_ACTOR
    return member.actor if git_bundle.mode == "member" else ""


async def _app_access(
    engine: Any, db_mgr: Any, org_id: str, clean: str, api_url: str
) -> GithubAccess | None:
    github_settings = await effective_settings(db_mgr, org_id, "github")
    if clean and not app_may_act_on(github_settings, clean):
        return None
    mapped = await installations_for_org(db_mgr, org_id)
    installation = pick_installation(
        [(i.installation_id, i.account_login) for i in mapped], clean or None
    )
    if installation is None:
        return None
    token = await engine.get_github_access_token(
        repo=clean or None,
        repositories=[clean.rsplit("/", 1)[-1]] if "/" in clean else None,
        installation_id=installation,
        inherit_permissions=True,
    )
    return GithubAccess(token, api_url, APP_ACTOR) if token else None


async def github_read_access(
    engine: Any,
    db_mgr: Any,
    org_id: str,
    git_bundle: GitCredentialBundle,
    repository: str | None = None,
) -> GithubAccess:
    """How to read ``repository`` (``owner/name``) on GitHub for this session."""
    api_url = github_api_url_of(engine)
    if engine is not None:
        app = await _app_access(engine, db_mgr, org_id, clean_repository(repository), api_url)
        if app is not None:
            return app
    member = _member_access(git_bundle, api_url)
    if git_bundle.user_token:
        return member
    if git_bundle.token:
        return GithubAccess(git_bundle.token, api_url, _bundle_actor(git_bundle, member))
    return GithubAccess(None, api_url, "")

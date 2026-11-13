"""Who the platform changes things on GitHub as, for a session, inside or outside a turn.

The member when the organization's pull request policy says so, else the organization's App
installation for the repository, else the member's own account. The token stays on the
platform: it is used for the API calls and never handed to the sandbox.
"""

from __future__ import annotations

from typing import Any

from git_engine.engine import pick_installation

from api.services.git_credentials import GitCredentialBundle, app_may_act_on
from api.services.git_gateway_env import github_api_url_of
from api.services.github_installations import installations_for_org
from api.services.settings import effective_settings
from specifications.interfaces.agent import GithubAccess


async def github_write_access(
    repository: str,
    *,
    engine: Any,
    db_mgr: Any,
    org: str,
    publishes_as_member: bool,
    git_bundle: GitCredentialBundle,
) -> GithubAccess:
    """The credential a GitHub write tool acts with for ``repository`` (see the module)."""
    api_url = github_api_url_of(engine)
    member = git_bundle.user_token
    login = str(git_bundle.source_details.get("username") or "")
    as_member = GithubAccess(member, api_url, f"@{login}" if login else "your GitHub account")
    if member and publishes_as_member:
        return as_member
    if engine is not None and app_may_act_on(
        await effective_settings(db_mgr, org, "github"), repository
    ):
        mapped = await installations_for_org(db_mgr, org)
        token = await engine.get_github_access_token(
            repo=repository,
            repositories=[repository.rsplit("/", 1)[-1]],
            installation_id=pick_installation(
                [(i.installation_id, i.account_login) for i in mapped], repository
            ),
            inherit_permissions=True,
        )
        if token:
            return GithubAccess(token, api_url, "the organization's GitHub App")
    return as_member if member else GithubAccess(None, api_url, "")

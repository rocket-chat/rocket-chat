"""The credential an external source is read with, and the one a change to it is proposed with.

An ``app`` source is read with a read-only token of the organization's GitHub App installation
(within ``github.app_token_target_repositories``); a ``member`` source with the linked GitHub
account of the member who owns it. A change is always proposed with the linked account of the
member who asks for it, never the App: the pull request is theirs, under their rights on the
repository. Tokens stay on the platform and are never returned or logged.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

from git_engine.config import github_web_url
from git_engine.engine import pick_installation
from git_engine.source_reader import GitHubSourceReader, GitHubSourceWriter

from api.services.credentials import get_user_credential
from api.services.git_credentials import app_may_act_on, refresh_github_user_token
from api.services.git_gateway_env import github_api_url_of
from api.services.github_installations import installations_for_org
from api.services.settings import effective_settings
from specifications.interfaces.external_sources import (
    ExternalSource,
    SourceReaderProtocol,
    SourceReadError,
    SourceWriterProtocol,
)

_REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_SCP = re.compile(r"^git@[^:]+:(?P<path>.+)$")
NO_LINKED_ACCOUNT = (
    "Link your GitHub account (Settings → Git identity) to propose a change: it opens a pull "
    "request as you."
)


def normalise_repository(raw: str) -> str:
    """``owner/name`` from what a person pastes (``owner/name``, an https or ssh URL)."""
    text = raw.strip()
    scp = _SCP.match(text)
    path = scp.group("path") if scp else (urlparse(text).path if "://" in text else text)
    repository = path.strip("/").removesuffix(".git").strip("/")
    if not _REPOSITORY.match(repository):
        raise ValueError(f"'{raw}' is not a GitHub repository: give owner/name or its URL.")
    return repository


class SourceAccess:
    """Builds readers and writers for sources; tests replace it on ``app.state.source_access``."""

    def __init__(self, engine: Any, db_mgr: Any) -> None:
        self._engine, self._db = engine, db_mgr
        self._api = github_api_url_of(engine)
        self._web = github_web_url(self._api)

    async def reader(self, source: ExternalSource) -> SourceReaderProtocol:
        if source.auth == "member":
            token = await self._member_token(source.org_id, source.user_id or "")
            if not token:
                raise SourceReadError(
                    "The linked GitHub account of this source's owner is missing or expired: "
                    "reconnect it in Settings → Git identity."
                )
        else:
            token = await self._app_token(source.org_id, source.repository)
        return GitHubSourceReader(self._api, self._web, source.repository, token)

    async def writer(
        self, org_id: str, user_id: str, source: ExternalSource
    ) -> SourceWriterProtocol:
        token = await self._member_token(org_id, user_id)
        if not token:
            raise SourceReadError(NO_LINKED_ACCOUNT)
        return GitHubSourceWriter(self._api, self._web, source.repository, token)

    async def _member_token(self, org_id: str, user_id: str) -> str | None:
        if not user_id:
            return None
        credential = await get_user_credential(self._db, user_id, "github", org_id=org_id)
        if credential is None or not credential.secret:
            return None
        refreshed = await refresh_github_user_token(self._db, credential)
        return refreshed.secret or None

    async def _app_token(self, org_id: str, repository: str) -> str:
        cannot = (
            f"The organization's GitHub App cannot read {repository}: install it on that "
            "repository (Settings → Git & GitHub), or connect the source with your own account."
        )
        if self._engine is None:
            raise SourceReadError(cannot)
        if not app_may_act_on(await effective_settings(self._db, org_id, "github"), repository):
            raise SourceReadError(
                f"{repository} is outside the repositories the organization lets its GitHub App "
                "reach (Settings → Git & GitHub)."
            )
        installations = [
            (i.installation_id, i.account_login)
            for i in await installations_for_org(self._db, org_id)
        ]
        token = await self._engine.get_github_access_token(
            repo=repository,
            permissions={"contents": "read", "metadata": "read"},
            repositories=[repository.rsplit("/", 1)[-1]],
            installation_id=pick_installation(installations, repository),
        )
        if not token:
            raise SourceReadError(cannot)
        return str(token)


def source_access(state: Any) -> SourceAccess:
    """The application's ``SourceAccess`` (built on first use from its git engine)."""
    found = getattr(state, "source_access", None)
    if found is None:
        found = SourceAccess(getattr(state, "git_engine", None), getattr(state, "db_manager", None))
        state.source_access = found
    return found

"""GitHub App installations mapped to organizations.

An installation grants access to one GitHub account's repositories. The mapping is made by an
administrator through GitHub's own install flow: the install link carries a signed ``state``
naming the organization, GitHub redirects back with the installation id, and only then is the
installation bound. Guessing an installation id, or a repository name, can never obtain a token
for someone else's installation.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

from sqlalchemy import select

from api.db.models import GithubInstallationModel


@dataclass(frozen=True)
class Installation:
    installation_id: int
    account_login: str


# Development without a database keeps the mapping in memory, like the Slack bindings.
_memory: dict[int, tuple[str, str]] = {}


def _has_database(db_mgr: Any) -> bool:
    return db_mgr is not None and getattr(db_mgr, "session_factory", None) is not None


def install_url(app_slug: str, state: str) -> str:
    return f"https://github.com/apps/{app_slug}/installations/new?" + urlencode({"state": state})


async def installations_for_org(db_mgr: Any, org_id: str) -> list[Installation]:
    if not _has_database(db_mgr):
        return [Installation(i, login) for i, (org, login) in _memory.items() if org == org_id]
    async with db_mgr.session() as session:
        rows = (
            await session.execute(
                select(GithubInstallationModel).where(GithubInstallationModel.org_id == org_id)
            )
        ).scalars()
        return [Installation(r.installation_id, r.account_login) for r in rows]


async def installation_org(db_mgr: Any, installation_id: int) -> str | None:
    if not _has_database(db_mgr):
        entry = _memory.get(installation_id)
        return entry[0] if entry else None
    async with db_mgr.session() as session:
        row = await session.get(GithubInstallationModel, installation_id)
        return row.org_id if row is not None else None


async def bind_installation(
    db_mgr: Any, installation_id: int, org_id: str, account_login: str, bound_by: str
) -> None:
    """Map the installation to ``org_id``; ``ValueError`` if another organization holds it."""
    current = await installation_org(db_mgr, installation_id)
    if current is not None and current != org_id:
        raise ValueError("This GitHub installation is already mapped to another organization")
    if not _has_database(db_mgr):
        _memory[installation_id] = (org_id, account_login)
        return
    async with db_mgr.session() as session:
        if current is None:
            session.add(
                GithubInstallationModel(
                    installation_id=installation_id,
                    org_id=org_id,
                    account_login=account_login,
                    bound_by=bound_by,
                    created_at=int(time.time()),
                )
            )


async def unbind_installation(db_mgr: Any, installation_id: int, org_id: str) -> bool:
    if await installation_org(db_mgr, installation_id) != org_id:
        return False
    if not _has_database(db_mgr):
        _memory.pop(installation_id, None)
        return True
    async with db_mgr.session() as session:
        row = await session.get(GithubInstallationModel, installation_id)
        if row is not None:
            await session.delete(row)
    return True

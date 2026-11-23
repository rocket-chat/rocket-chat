"""Connecting, changing and removing external sources, under ownership rules.

A source is owned like any shared object: the organization's, a team's or a member's. Whoever
may see it may import from it; whoever may manage it may change, synchronise or remove it. A
``member`` source (read with the owner's own GitHub account) is always personal, and its skills
only go to that member's personal skills. Removing a source keeps its skills, no longer linked:
they become ordinary skills that can be edited here.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, replace
from typing import Any

from api.services.audit import AuditService
from api.services.external_sources import external_source_store
from api.services.ownership import OwnershipError, requested_owner, require_manage
from api.services.source_access import normalise_repository
from api.services.source_sync import SyncOutcome, synchronise
from api.services.teams import note_memory_owned
from specifications.interfaces.external_sources import SOURCE_AUTHS, ExternalSource
from specifications.interfaces.ownership import Owner, Viewer, can_manage, can_see

# The periodic synchronisation's pace; a push webhook brings a source forward at once.
SYNC_INTERVAL_SECONDS = 900
MAX_NAME_CHARS = 120


@dataclass
class SourceError(Exception):
    status_code: int
    detail: str


def owner_of_source(source: ExternalSource) -> Owner:
    if source.scope == "user":
        return Owner("user", user_id=source.user_id)
    if source.scope == "team":
        return Owner("team", team_id=source.team_id)
    return Owner("org")


def may_manage(viewer: Viewer, source: ExternalSource) -> bool:
    return can_manage(viewer, owner_of_source(source))


async def visible_sources(db_mgr: Any, org_id: str, viewer: Viewer) -> list[ExternalSource]:
    sources = await external_source_store(db_mgr).list_sources(org_id)
    return [s for s in sources if can_see(viewer, owner_of_source(s))]


async def visible_source(
    db_mgr: Any, org_id: str, viewer: Viewer, source_id: str
) -> ExternalSource:
    source = await external_source_store(db_mgr).get_source(org_id, source_id)
    if source is None or not can_see(viewer, owner_of_source(source)):
        raise SourceError(404, "No such source.")
    return source


async def managed_source(
    db_mgr: Any, org_id: str, viewer: Viewer, source_id: str
) -> ExternalSource:
    source = await visible_source(db_mgr, org_id, viewer, source_id)
    if not may_manage(viewer, source):
        raise SourceError(403, "Only whoever manages this source can change it.")
    return source


async def _owner(
    db_mgr: Any, org_id: str, viewer: Viewer, scope: str, team_id: str | None, auth: str
) -> Owner:
    if auth not in SOURCE_AUTHS:
        raise SourceError(422, f"auth must be one of {', '.join(SOURCE_AUTHS)}.")
    if auth == "member" and scope != "user":
        raise SourceError(
            422, "A source read with your own GitHub account is personal: its scope is 'user'."
        )
    try:
        owner = await requested_owner(db_mgr, org_id, viewer, scope, team_id)
        require_manage(viewer, owner, "sources")
    except OwnershipError as err:
        raise SourceError(err.status_code, err.detail) from err
    return owner


def _clean_ref(ref: str) -> str:
    cleaned = ref.strip().removeprefix("refs/heads/")
    if not cleaned or any(c.isspace() for c in cleaned) or ".." in cleaned:
        raise SourceError(422, "Give the branch to follow, e.g. 'main'.")
    return cleaned


async def create_source(
    state: Any,
    org_id: str,
    viewer: Viewer,
    *,
    name: str,
    repository: str,
    ref: str,
    auth: str,
    scope: str,
    team_id: str | None,
) -> tuple[ExternalSource, SyncOutcome]:
    """Connect a repository and read it at once (a failure is recorded on the source)."""
    db_mgr = getattr(state, "db_manager", None)
    owner = await _owner(db_mgr, org_id, viewer, scope, team_id, auth)
    try:
        repo = normalise_repository(repository)
    except ValueError as err:
        raise SourceError(422, str(err)) from err
    branch = _clean_ref(ref)
    store = external_source_store(db_mgr)
    for existing in await store.sources_following(org_id, repo, branch):
        if owner_of_source(existing) == owner:
            raise SourceError(
                409, f"{repo} ({branch}) is already connected here as '{existing.name}'."
            )
    now = int(time.time())
    source = ExternalSource(
        id=f"src_{uuid.uuid4().hex[:12]}",
        org_id=org_id,
        name=(name.strip() or repo)[:MAX_NAME_CHARS],
        repository=repo,
        ref=branch,
        auth="member" if auth == "member" else "app",
        scope=owner.scope,
        user_id=owner.user_id,
        team_id=owner.team_id,
        next_sync_at=now + SYNC_INTERVAL_SECONDS,
        created_by=viewer.user_id,
        created_at=now,
        updated_at=now,
    )
    await store.save_source(source)
    if db_mgr is None and owner.team_id:
        note_memory_owned(org_id, owner.team_id, "external_sources", 1)
    await _audit(db_mgr, "external_source.created", source, viewer)
    outcome = await synchronise(state, source, force=True)
    stored = await store.get_source(org_id, source.id)
    return stored or source, outcome


async def update_source(
    state: Any, org_id: str, viewer: Viewer, source_id: str, *, name: str | None, ref: str | None
) -> ExternalSource:
    """Rename a source or change the branch it follows (read again at once)."""
    db_mgr = getattr(state, "db_manager", None)
    source = await managed_source(db_mgr, org_id, viewer, source_id)
    changed = replace(
        source,
        name=(name.strip()[:MAX_NAME_CHARS] if name and name.strip() else source.name),
        ref=_clean_ref(ref) if ref else source.ref,
        updated_at=int(time.time()),
    )
    store = external_source_store(db_mgr)
    await store.save_source(changed)
    await _audit(db_mgr, "external_source.updated", changed, viewer)
    if changed.ref != source.ref:
        await synchronise(state, changed, force=True)
    return await store.get_source(org_id, source_id) or changed


async def delete_source(state: Any, org_id: str, viewer: Viewer, source_id: str) -> None:
    db_mgr = getattr(state, "db_manager", None)
    source = await managed_source(db_mgr, org_id, viewer, source_id)
    await external_source_store(db_mgr).delete_source(org_id, source_id)
    if db_mgr is None and source.team_id:
        note_memory_owned(org_id, source.team_id, "external_sources", -1)
    await _audit(db_mgr, "external_source.deleted", source, viewer)


async def import_owner(
    db_mgr: Any,
    org_id: str,
    viewer: Viewer,
    source: ExternalSource,
    scope: str,
    team_id: str | None,
) -> Owner:
    """The owner imported skills go to, once ``viewer`` may manage it (any owner for an App
    source, only the member themself for a source read with their own account)."""
    if source.auth == "member" and (scope != "user" or source.user_id != viewer.user_id):
        raise SourceError(
            422,
            "Skills of a source read with your own GitHub account only go to your personal skills.",
        )
    try:
        owner = await requested_owner(db_mgr, org_id, viewer, scope, team_id)
        require_manage(viewer, owner, "skills")
    except OwnershipError as err:
        raise SourceError(err.status_code, err.detail) from err
    return owner


async def bring_forward(db_mgr: Any, org_id: str, repository: str, branch: str) -> list[str]:
    """A push moved ``branch``: the sources following it are read at the next sweep."""
    store = external_source_store(db_mgr)
    following = await store.sources_following(org_id, repository, branch)
    for source in following:
        await store.schedule(org_id, source.id, 0)
    return [s.id for s in following]


async def _audit(db_mgr: Any, event: str, source: ExternalSource, viewer: Viewer) -> None:
    await AuditService(db_mgr).record(
        event,
        org_id=source.org_id,
        actor_user_id=viewer.user_id,
        result_summary=f"{source.name}: {source.repository} ({source.ref})",
        metadata={"source_id": source.id, "auth": source.auth, "scope": source.scope},
    )

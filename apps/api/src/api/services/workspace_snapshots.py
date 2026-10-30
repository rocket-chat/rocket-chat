"""Saving and restoring a session's whole workspace, through its driver (Docker: an archive of
the volume; Kubernetes: a ``VolumeSnapshot``).

Snapshots are listed on the session (``workspace_snapshots``, newest last); past
``sandboxes.workspace_snapshots_kept`` the oldest is deleted. An organization can have one taken
before the idle reaper pauses a sandbox (``sandboxes.snapshot_before_hibernate``), so a workspace
lost with its node can be restored.
"""

from __future__ import annotations

import logging
from dataclasses import asdict
from typing import Any

from api.db.session import current_tenant_org_id
from api.services.settings import effective_settings
from specifications.interfaces.sandbox import (
    SandboxDriverProtocol,
    WorkspaceSnapshot,
    WorkspaceSnapshotter,
)
from specifications.interfaces.session import SessionRecord, SessionStoreProtocol

logger = logging.getLogger(__name__)

SNAPSHOTS_KEY = "workspace_snapshots"


class SnapshotsUnavailable(RuntimeError):
    """The session's driver cannot save workspaces."""


def snapshots_of(record: SessionRecord | None) -> list[WorkspaceSnapshot]:
    metadata = record.metadata if record is not None and isinstance(record.metadata, dict) else {}
    found = []
    for raw in metadata.get(SNAPSHOTS_KEY) or []:
        if isinstance(raw, dict):
            try:
                found.append(WorkspaceSnapshot(**raw))
            except TypeError:
                continue
    return found


def _snapshotter(driver: SandboxDriverProtocol | None) -> WorkspaceSnapshotter:
    if not isinstance(driver, WorkspaceSnapshotter):
        raise SnapshotsUnavailable("This deployment's sandboxes cannot save whole workspaces.")
    return driver


async def take_snapshot(
    driver: SandboxDriverProtocol | None,
    store: SessionStoreProtocol,
    db_mgr: Any,
    record: SessionRecord,
    label: str,
) -> WorkspaceSnapshot:
    """Save the workspace and record it on the session (dropping the oldest past the limit)."""
    snapshot = await _snapshotter(driver).snapshot_workspace(record.session_id, label[:120])
    settings = await effective_settings(db_mgr, record.tenant_org_id, "sandboxes")
    kept = max(1, int(settings.get("workspace_snapshots_kept") or 3))
    dropped: list[WorkspaceSnapshot] = []

    def remember(stored: SessionRecord) -> None:
        nonlocal dropped
        current = [*snapshots_of(stored), snapshot]
        dropped, current = current[:-kept], current[-kept:]
        known = stored.metadata if isinstance(stored.metadata, dict) else {}
        stored.metadata = {**known, SNAPSHOTS_KEY: [asdict(s) for s in current]}

    await store.mutate_session(record.session_id, record.tenant_org_id, remember)
    for old in dropped:
        try:
            await _snapshotter(driver).delete_snapshot(old)
        except Exception as err:
            logger.warning("Could not delete the old snapshot %s: %s", old.location, err)
    return snapshot


async def restore_snapshot(
    driver: SandboxDriverProtocol | None, record: SessionRecord, snapshot_id: str
) -> WorkspaceSnapshot:
    snapshot = next((s for s in snapshots_of(record) if s.id == snapshot_id), None)
    if snapshot is None:
        raise KeyError(snapshot_id)
    await _snapshotter(driver).restore_workspace(record.session_id, snapshot)
    return snapshot


async def delete_snapshot(
    driver: SandboxDriverProtocol | None,
    store: SessionStoreProtocol,
    record: SessionRecord,
    snapshot_id: str,
) -> None:
    snapshot = next((s for s in snapshots_of(record) if s.id == snapshot_id), None)
    if snapshot is None:
        raise KeyError(snapshot_id)
    await _snapshotter(driver).delete_snapshot(snapshot)

    def forget(stored: SessionRecord) -> None:
        known = stored.metadata if isinstance(stored.metadata, dict) else {}
        kept = [asdict(s) for s in snapshots_of(stored) if s.id != snapshot_id]
        stored.metadata = {**known, SNAPSHOTS_KEY: kept}

    await store.mutate_session(record.session_id, record.tenant_org_id, forget)


def before_pause_hook(
    driver: SandboxDriverProtocol | None, store: SessionStoreProtocol, db_mgr: Any
) -> Any:
    """The driver's ``before_idle_pause``: a snapshot when the session's organization asks for
    one. The reaper runs outside any request, so the session's tenant is bound here."""

    async def snapshot_before_pause(session_id: str) -> None:
        record = await store.get_session(session_id, include_history=False)
        if record is None:
            return
        token = current_tenant_org_id.set(record.tenant_org_id)
        try:
            settings = await effective_settings(db_mgr, record.tenant_org_id, "sandboxes")
            if settings.get("snapshot_before_hibernate"):
                await take_snapshot(driver, store, db_mgr, record, "Before pausing (idle)")
        finally:
            current_tenant_org_id.reset(token)

    return snapshot_before_pause

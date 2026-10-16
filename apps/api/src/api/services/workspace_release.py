"""Releasing the workspace volume of sessions left idle for days.

Hibernation deletes a session's compute and keeps its workspace volume, so the next turn finds
the repositories it cloned. A volume costs storage for as long as it exists, so after
``sandboxes.workspace_release_days`` without activity (or the sandbox type's own delay) it is
deleted too. The session stays; its next turn starts with a blank sandbox and the agent is told
why (see ``released_notice``).

Idleness is the session's last update in the database, not the driver's in-memory clock: the
delay is days long, and the API restarts far more often than that.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select

from api.db.models import SessionModel
from api.db.session import DatabaseSessionManager
from api.services.sandbox_catalogue import SandboxCatalogue
from api.services.settings import effective_settings
from specifications.interfaces.sandbox import SandboxDriverProtocol, SandboxStatus
from specifications.interfaces.session import SessionRecord, SessionStoreProtocol

logger = logging.getLogger(__name__)

DAY_SECONDS = 86_400
RELEASED_AT = "workspace_released_at"
RELEASED_AFTER_DAYS = "workspace_released_after_days"


@dataclass(frozen=True)
class IdleWorkspace:
    session_id: str
    org_id: str
    idle_since: int
    sandbox_id: str | None
    image: str | None


async def release_idle_workspaces(
    candidates: list[IdleWorkspace],
    driver: SandboxDriverProtocol,
    store: SessionStoreProtocol,
    delay_days: Callable[[IdleWorkspace], Awaitable[int]],
    now: float | None = None,
) -> list[str]:
    """Release each candidate idle for longer than its delay; returns the sessions released."""
    moment = now if now is not None else time.time()
    released: list[str] = []
    for workspace in candidates:
        days = await delay_days(workspace)
        if days <= 0 or moment - workspace.idle_since < days * DAY_SECONDS:
            continue
        # A sandbox in use is never released, whatever the record says.
        if await driver.get_status(workspace.session_id) == SandboxStatus.RUNNING:
            continue
        try:
            await driver.destroy_workspace(workspace.session_id)
        except Exception as err:
            logger.warning("Could not release the workspace of %s: %s", workspace.session_id, err)
            continue
        await store.mutate_session(workspace.session_id, workspace.org_id, _mark(moment, days))
        logger.info("Released the workspace of %s after %d idle day(s)", workspace.session_id, days)
        released.append(workspace.session_id)
    return released


def _mark(moment: float, days: int) -> Callable[[SessionRecord], None]:
    def mark(record: SessionRecord) -> None:
        record.sandbox_status = SandboxStatus.NON_EXISTENT
        known = record.metadata if isinstance(record.metadata, dict) else {}
        record.metadata = {**known, RELEASED_AT: int(moment), RELEASED_AFTER_DAYS: days}

    return mark


async def idle_candidates(owner_db: DatabaseSessionManager) -> list[IdleWorkspace]:
    """Sessions that still have a workspace, across every organization (schema-owner read)."""
    async with owner_db.session() as session:
        rows = (
            await session.execute(
                select(SessionModel).where(
                    SessionModel.sandbox_status != SandboxStatus.NON_EXISTENT.value
                )
            )
        ).scalars()
        return [
            IdleWorkspace(
                session_id=row.id,
                org_id=row.org_id,
                idle_since=int(row.updated_at),
                sandbox_id=(row.metadata_json or {}).get("sandbox_id"),
                image=row.container_image,
            )
            for row in rows
        ]


def delay_resolver(db_mgr: Any) -> Callable[[IdleWorkspace], Awaitable[int]]:
    """The sandbox type's delay, else the organization's ``workspace_release_days``."""
    org_days: dict[str, int] = {}

    async def delay_days(workspace: IdleWorkspace) -> int:
        catalogue = SandboxCatalogue(db_mgr)
        entry = await catalogue.resolve(
            workspace.org_id, sandbox_id=workspace.sandbox_id, image=workspace.image
        )
        if entry is not None and entry.release_after_days is not None:
            return entry.release_after_days
        if workspace.org_id not in org_days:
            settings = await effective_settings(db_mgr, workspace.org_id, "sandboxes")
            org_days[workspace.org_id] = int(settings["workspace_release_days"])
        return org_days[workspace.org_id]

    return delay_days


def released_notice(metadata: dict[str, Any]) -> str:
    """What the next turn tells the agent about a released workspace (empty when none was)."""
    if not metadata.get(RELEASED_AT):
        return ""
    days = metadata.get(RELEASED_AFTER_DAYS) or "several"
    return (
        f"This session's workspace was released after {days} idle day(s): the sandbox is blank. "
        "Clone the repositories you need again; work that was not pushed is gone."
    )

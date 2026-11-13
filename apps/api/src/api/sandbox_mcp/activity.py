"""What the idle reaper learns about sandbox MCP calls, whichever replica made them.

The reaper runs on one replica and knows a turn's activity from ``turn_traces``. MCP calls write
no trace; a call touches its session instead (``calls._kept_awake``), and this reads it back.
"""

from __future__ import annotations

import logging

from sqlalchemy import select

from api.db.models import SessionModel
from api.db.session import DatabaseSessionManager
from specifications.interfaces.sandbox import SandboxStatus
from specifications.interfaces.session import SessionSource

logger = logging.getLogger(__name__)


async def sandbox_mcp_activity(owner_db: DatabaseSessionManager) -> dict[str, float]:
    """The last activity of every MCP sandbox that still has a workspace (schema-owner read).

    A failed read is logged and reads as no activity, like the turns' own: a sweep is never
    lost to it, and the driver's in-process holds still protect calls running here.
    """
    try:
        async with owner_db.session() as session:
            rows = await session.execute(
                select(SessionModel.id, SessionModel.updated_at).where(
                    SessionModel.source == SessionSource.MCP.value,
                    SessionModel.sandbox_status != SandboxStatus.NON_EXISTENT.value,
                )
            )
            return {row[0]: float(row[1]) for row in rows.all() if row[1] is not None}
    except Exception:
        logger.exception("Failed to read sandbox MCP activity")
        return {}


def latest_activity(*sources: dict[str, float] | None) -> dict[str, float] | None:
    """Each session's most recent activity across ``sources`` (None when none was read)."""
    present = [source for source in sources if source is not None]
    if not present:
        return None
    merged: dict[str, float] = {}
    for source in present:
        for session_id, moment in source.items():
            merged[session_id] = max(moment, merged.get(session_id, moment))
    return merged

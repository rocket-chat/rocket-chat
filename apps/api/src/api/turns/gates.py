"""Keeping a turn's pending question with the turn (survives a restart)."""

import logging
from typing import Any

from api.db.session import DatabaseSessionManager
from api.db.store import TurnTraceStore
from api.services.turn_trace import (
    TurnTraceAccumulator,
)

logger = logging.getLogger(__name__)


async def _remember_pending_decision(
    db_mgr: DatabaseSessionManager, trace: TurnTraceAccumulator, decision: dict[str, Any]
) -> None:
    """Keep the question with the turn so a restart cannot lose it. Never fails the turn."""
    try:
        await TurnTraceStore(db_mgr).set_pending_decision(trace.trace_id, trace.org_id, decision)
    except Exception as err:
        logger.warning("Could not persist the pending question of %s: %s", trace.trace_id, err)


async def take_stored_question(
    db_mgr: DatabaseSessionManager | None, session_id: str, org_id: str, question_id: str
) -> dict[str, Any] | None:
    """The stored question, if it is the one still pending, cleared so it is answered once.

    The turn that asked it died with its server; answering it starts a continuation instead.
    """
    if db_mgr is None:
        return None
    try:
        return await TurnTraceStore(db_mgr).take_pending_decision(session_id, org_id, question_id)
    except Exception as err:
        logger.warning("Could not take stored question %s: %s", question_id, err)
        return None


async def _forget_pending_decisions(
    db_mgr: DatabaseSessionManager | None, session_id: str, org_id: str
) -> None:
    """The question was answered or abandoned. Never fails the caller."""
    if db_mgr is None:
        return
    try:
        await TurnTraceStore(db_mgr).clear_pending_decisions(session_id, org_id)
    except Exception as err:
        logger.warning("Could not clear the stored question of %s: %s", session_id, err)

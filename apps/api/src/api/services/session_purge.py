"""Deleting a session with everything derived from it: its workspace, transcript and caches."""

from __future__ import annotations

import logging
from typing import Any

from agent_core.tools.offload import forget_session_offloads

from specifications.interfaces.sandbox import SandboxDriverProtocol
from specifications.interfaces.session import SessionRecord, SessionStoreProtocol

logger = logging.getLogger(__name__)


async def purge_session(state: Any, record: SessionRecord) -> bool:
    """Delete one session with its sandbox workspace, transcript and cached derivatives.

    ``state`` is the API application's state (``app.state``), which holds the collaborators.
    """
    store: SessionStoreProtocol = state.session_store
    driver: SandboxDriverProtocol | None = getattr(state, "sandbox_driver", None)
    session_id = record.session_id
    if driver:
        try:
            await driver.destroy_workspace(session_id)
        except Exception as err:
            logger.warning("Error destroying workspace for deleted session %s: %s", session_id, err)

    # Release the session's cached semantic index: the workspace it described is gone,
    # and holding it would keep derived data alive for a session that no longer exists.
    code_index_cache = getattr(state, "code_index_cache", None)
    if code_index_cache is not None:
        code_index_cache.invalidate(session_id)
    forget_session_offloads(session_id)

    # Drop the replay history and sequence counter for the deleted session.
    event_bus = getattr(state, "event_bus", None)
    if event_bus is not None and hasattr(event_bus, "evict"):
        event_bus.evict(session_id)

    transcripts = getattr(state, "transcript_store", None)
    if transcripts is not None:
        await transcripts.delete(session_id, record.tenant_org_id)

    return await store.delete_session(session_id, tenant_org_id=record.tenant_org_id)

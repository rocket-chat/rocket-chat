"""Session forking.

A fork branches a conversation without touching the original: it copies history up to an
optional turn, the task checklist and the bound agent, records its provenance in
``metadata.forked_from``, and gives the child its own workspace seeded from the parent
(never a shared volume). Later turns on either branch stay isolated because each has its
own conversation history and sandbox.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from agent_core.tools import git_tools
from config_engine.registry import DEFAULT_SANDBOX_IMAGE
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from sandbox_driver.seeding import seed_workspace_from
from sandbox_driver.workspace import resolve_repository_dir

from api.auth.access import _check_session_access, _get_request_identity
from api.routers.sessions_list import (
    SessionListItem,
    _publish_session_updated,
    session_list_item,
)
from api.services.session_support import active_repository_of
from specifications.interfaces.sandbox import SandboxStatus, WorkspaceSpec
from specifications.interfaces.session import SessionRecord, SessionStoreProtocol

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1", tags=["Sessions"])


class ForkSessionRequest(BaseModel):
    """Payload for branching a session."""

    up_to_turn: int | None = Field(
        default=None,
        ge=1,
        description=(
            "Copy history through the Nth user turn, inclusive of its assistant response. "
            "Omit to copy the entire conversation."
        ),
    )
    title: str | None = Field(default=None, description="Optional title for the fork")


class RewindSessionRequest(BaseModel):
    """Payload for rewinding a session to an earlier turn."""

    target_turn: int = Field(
        ...,
        ge=1,
        description="The turn index (1-indexed user turn) to rewind back to.",
    )


def truncate_history(history: list[Any], up_to_turn: int | None) -> list[Any]:
    """Return the leading history that belongs to the first ``up_to_turn`` user turns.

    The cut is made *before* the next user message, so a fork never starts mid-turn with
    an assistant answer whose question was left behind.
    """
    if up_to_turn is None:
        return list(history)
    copied: list[Any] = []
    seen_user_turns = 0
    for message in history:
        if getattr(message, "role", "") == "user":
            seen_user_turns += 1
            if seen_user_turns > up_to_turn:
                break
        copied.append(message)
    return copied


@router.post("/sessions/{session_id}/fork", response_model=SessionListItem)
async def fork_session(
    session_id: str,
    req: ForkSessionRequest,
    request: Request,
) -> SessionListItem:
    """Branch a session into a new one with its own history and workspace."""
    store: SessionStoreProtocol = request.app.state.session_store
    driver = getattr(request.app.state, "sandbox_driver", None)
    _, user_id, roles = _get_request_identity(request)

    parent = await store.get_session(session_id)
    if parent is None:
        raise HTTPException(status_code=404, detail="Session not found")
    # A fork creates a new owned session, so it is a write action: the read-only role
    # must not be able to branch a session it can only view.
    _check_session_access(parent, user_id, roles, require_write=True)

    child_id = f"session_{uuid.uuid4().hex[:10]}"
    metadata: dict[str, Any] = dict(parent.metadata) if isinstance(parent.metadata, dict) else {}
    # Provenance is rewritten, never inherited: a fork of a fork points at its direct
    # parent, and sharing/collaboration never silently carries over to the new owner.
    metadata["forked_from"] = {"session_id": parent.session_id, "turn": req.up_to_turn}
    metadata["is_shared"] = False
    metadata["collaborators"] = []

    record = SessionRecord(
        session_id=child_id,
        tenant_org_id=parent.tenant_org_id,
        tenant_user_id=user_id or parent.tenant_user_id,
        title=req.title or f"{parent.title} (fork)",
        source=parent.source,
        sandbox_status=SandboxStatus.NON_EXISTENT,
        container_image=parent.container_image,
        git_repo=parent.git_repo,
        git_branch=parent.git_branch,
        conversation_history=truncate_history(parent.conversation_history, req.up_to_turn),
        metadata=metadata,
        checklist=list(parent.checklist or []),
    )

    await _seed_child_workspace(driver, parent, record, metadata)
    await store.create_session(record)
    # The agent's own memory of the conversation (tool calls and results) follows the fork,
    # so the child's next model request carries the parent's context.
    transcripts = getattr(request.app.state, "transcript_store", None)
    if transcripts is not None:
        await transcripts.copy_prefix(
            parent.session_id, record.session_id, record.tenant_org_id, req.up_to_turn
        )
    await _publish_session_updated(request, record)
    return session_list_item(record)


async def _seed_child_workspace(
    driver: Any,
    parent: SessionRecord,
    record: SessionRecord,
    metadata: dict[str, Any],
) -> None:
    """Seed the fork's own workspace and record how it was produced.

    A seeding failure must not lose the fork — the conversation is the valuable part — so
    it degrades to an unseeded workspace with an explicit origin marker rather than
    aborting the request.
    """
    if driver is None:
        metadata["workspace_origin"] = "none"
        return
    spec = WorkspaceSpec(
        session_id=record.session_id,
        tenant_org_id=record.tenant_org_id,
        tenant_user_id=record.tenant_user_id,
        container_image=parent.container_image or DEFAULT_SANDBOX_IMAGE,
    )
    try:
        seed = await seed_workspace_from(driver, parent.session_id, record.session_id, spec)
    except Exception:
        logger.exception("Fork %s: workspace seeding failed", record.session_id)
        metadata["workspace_origin"] = "none"
        return
    metadata["workspace_origin"] = seed.origin
    if seed.file_count > 0:
        # The child volume exists but no compute is running for it yet.
        record.sandbox_status = SandboxStatus.HIBERNATED


@router.post("/sessions/{session_id}/rewind", response_model=SessionListItem)
async def rewind_session(
    session_id: str,
    req: RewindSessionRequest,
    request: Request,
) -> SessionListItem:
    """Time-travel rewind: roll conversation history, transcript, and sandbox back to turn N."""
    store: SessionStoreProtocol = request.app.state.session_store
    driver = getattr(request.app.state, "sandbox_driver", None)
    _, user_id, roles = _get_request_identity(request)

    session = await store.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    _check_session_access(session, user_id, roles, require_write=True)

    def _mutate(record: SessionRecord) -> None:
        record.conversation_history = truncate_history(record.conversation_history, req.target_turn)

    updated = await store.mutate_session(session_id, session.tenant_org_id, _mutate)
    if updated is None:
        raise HTTPException(status_code=500, detail="Failed to update session")

    transcripts = getattr(request.app.state, "transcript_store", None)
    if transcripts is not None and hasattr(transcripts, "truncate_to_turn"):
        try:
            await transcripts.truncate_to_turn(session_id, session.tenant_org_id, req.target_turn)
        except Exception:
            logger.exception("Failed to truncate transcript to turn %s", req.target_turn)

    if driver is not None and updated.sandbox_status == SandboxStatus.RUNNING:
        folder = active_repository_of(updated)
        if folder:
            try:
                resolved_folder = await resolve_repository_dir(driver, session_id, folder)
                checkpoints = await git_tools.list_checkpoints(driver, session_id, resolved_folder)
                matched = next(
                    (
                        c
                        for c in checkpoints
                        if c.name == f"rocket-checkpoint-{req.target_turn}"
                        or c.name.startswith(f"rocket-checkpoint-{req.target_turn}-")
                    ),
                    None,
                )
                if matched:
                    await git_tools.git_revert(
                        driver, session_id, matched.name, "hard", resolved_folder
                    )
            except Exception:
                logger.exception(
                    "Rewind: failed to revert workspace git to turn %s", req.target_turn
                )

    await _publish_session_updated(request, updated)
    return session_list_item(updated)

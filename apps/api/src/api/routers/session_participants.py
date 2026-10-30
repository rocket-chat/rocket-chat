"""Who wrote in a session, by name: the web app shows each author above their message when
more than one person took part."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel
from sqlalchemy import select

from api.auth.session_lookup import session_for_request
from api.db.models import UserModel
from specifications.interfaces.llm import ChatMessage

router = APIRouter(prefix="/v1", tags=["Sessions"])

# Guest members a chat platform creates for people who are not linked to an account.
_GUEST_PLATFORMS = ("slack", "teams", "mattermost")
MAX_PARTICIPANTS = 100
# How chat platforms name each message's speaker for the agent: "[Speaker: Ada (<@U1>)] text".
SPEAKER_PREFIX = "[Speaker:"


class Participant(BaseModel):
    user_id: str
    name: str
    # The chat platform of a guest (not linked to a member account); None for a member.
    guest_of: str | None = None


class ParticipantList(BaseModel):
    participants: list[Participant]


def guest_platform(user_id: str) -> str | None:
    prefix = user_id.split(":", 1)[0]
    return prefix if ":" in user_id and prefix in _GUEST_PLATFORMS else None


def _author_of(message: ChatMessage, owner: str) -> str | None:
    """Who wrote ``message``: stored without an author (older history, the session's first
    prompt), it is the owner's, unless a chat platform named its speaker."""
    if message.author_user_id:
        return message.author_user_id
    return None if (message.content or "").lstrip().startswith(SPEAKER_PREFIX) else owner or None


async def _names(db_mgr: Any, org_id: str, user_ids: list[str]) -> dict[str, str]:
    if db_mgr is None or not getattr(db_mgr, "session_factory", None) or not user_ids:
        return {}
    async with db_mgr.session(tenant_org_id=org_id) as session:
        rows = await session.execute(
            select(UserModel.id, UserModel.name, UserModel.email).where(UserModel.id.in_(user_ids))
        )
        return {row.id: row.name or row.email for row in rows}


@router.get("/sessions/{session_id}/participants", response_model=ParticipantList)
async def session_participants(session_id: str, request: Request) -> ParticipantList:
    """The authors of the session's messages with their names (a guest's name is its platform's
    placeholder: the web app shows the name the platform gave with each message instead)."""
    caller = await session_for_request(request, session_id, require_write=False)
    authors = list(
        dict.fromkeys(
            author
            for m in caller.record.conversation_history
            if m.role == "user" and (author := _author_of(m, caller.record.tenant_user_id))
        )
    )[:MAX_PARTICIPANTS]
    names = await _names(getattr(request.app.state, "db_manager", None), caller.org_id, authors)
    return ParticipantList(
        participants=[
            Participant(user_id=uid, name=names.get(uid) or uid, guest_of=guest_platform(uid))
            for uid in authors
        ]
    )

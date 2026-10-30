"""Thumbs up or down on an answer, with a comment: one row per member and answered turn.

The prompt and answer are copied as excerpts when the rating is given, so a rating stays
readable after the turn is rewound or the session deleted from view; administrators export the
rows to build evaluation sets.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select

from api.db.models import AnswerFeedbackModel
from specifications.interfaces.llm import ChatMessage
from specifications.interfaces.session import SessionRecord

EXCERPT_CHARS = 4_000
MAX_EXPORTED = 5_000
RATINGS = ("up", "down")


@dataclass(frozen=True)
class Feedback:
    session_id: str
    user_id: str
    turn_number: int
    rating: str
    comment: str
    prompt_excerpt: str
    answer_excerpt: str
    model: str | None
    agent_id: str | None
    updated_at: int


class FeedbackError(ValueError):
    pass


def _text(message: ChatMessage) -> str:
    content = message.content
    if isinstance(content, list):
        return " ".join(str(part.get("text") or "") for part in content if isinstance(part, dict))
    return str(content or "")


def turn_excerpts(history: list[ChatMessage], turn_number: int) -> tuple[str, str]:
    """The ``turn_number``-th person message (1-indexed) and the answer that followed it."""
    seen = 0
    for index, message in enumerate(history):
        if message.role != "user":
            continue
        seen += 1
        if seen != turn_number:
            continue
        answer = ""
        for later in history[index + 1 :]:
            if later.role == "user":
                break
            if later.role == "assistant" and _text(later).strip():
                answer = _text(later)
        return _text(message)[:EXCERPT_CHARS], answer[:EXCERPT_CHARS]
    raise FeedbackError(f"The session has no turn {turn_number}.")


def _view(row: AnswerFeedbackModel) -> Feedback:
    return Feedback(
        session_id=row.session_id,
        user_id=row.user_id,
        turn_number=row.turn_number,
        rating=row.rating,
        comment=row.comment,
        prompt_excerpt=row.prompt_excerpt,
        answer_excerpt=row.answer_excerpt,
        model=row.model,
        agent_id=row.agent_id,
        updated_at=row.updated_at,
    )


_memory: dict[tuple[str, str, str, int], Feedback] = {}


class FeedbackStore:
    def __init__(self, db_mgr: Any) -> None:
        self._db = db_mgr

    async def rate(
        self, record: SessionRecord, user_id: str, turn_number: int, rating: str, comment: str
    ) -> Feedback:
        if rating not in RATINGS:
            raise FeedbackError("A rating is up or down.")
        prompt, answer = turn_excerpts(record.conversation_history, turn_number)
        metadata = record.metadata if isinstance(record.metadata, dict) else {}
        feedback = Feedback(
            session_id=record.session_id,
            user_id=user_id,
            turn_number=turn_number,
            rating=rating,
            comment=comment.strip(),
            prompt_excerpt=prompt,
            answer_excerpt=answer,
            model=getattr(record, "selected_model", None),
            agent_id=metadata.get("agent_id"),
            updated_at=int(time.time()),
        )
        if self._db is None:
            _memory[(record.tenant_org_id, record.session_id, user_id, turn_number)] = feedback
            return feedback
        await self._upsert(record.tenant_org_id, feedback)
        return feedback

    async def _upsert(self, org_id: str, feedback: Feedback) -> None:
        async with self._db.session(tenant_org_id=org_id) as session:
            row = (
                await session.scalars(
                    select(AnswerFeedbackModel).where(
                        AnswerFeedbackModel.session_id == feedback.session_id,
                        AnswerFeedbackModel.user_id == feedback.user_id,
                        AnswerFeedbackModel.turn_number == feedback.turn_number,
                    )
                )
            ).first()
            if row is None:
                row = AnswerFeedbackModel(
                    id=f"fb_{uuid.uuid4().hex[:16]}",
                    org_id=org_id,
                    created_at=feedback.updated_at,
                    session_id=feedback.session_id,
                    user_id=feedback.user_id,
                    turn_number=feedback.turn_number,
                )
                session.add(row)
            for name in (
                "rating",
                "comment",
                "prompt_excerpt",
                "answer_excerpt",
                "model",
                "agent_id",
            ):
                setattr(row, name, getattr(feedback, name))
            row.updated_at = feedback.updated_at

    async def mine(self, org_id: str, session_id: str, user_id: str) -> list[Feedback]:
        if self._db is None:
            return [
                f
                for (o, s, u, _), f in _memory.items()
                if (o, s, u) == (org_id, session_id, user_id)
            ]
        async with self._db.session(tenant_org_id=org_id) as session:
            rows = await session.scalars(
                select(AnswerFeedbackModel).where(
                    AnswerFeedbackModel.session_id == session_id,
                    AnswerFeedbackModel.user_id == user_id,
                )
            )
            return [_view(r) for r in rows.all()]

    async def export(self, org_id: str, since: int = 0) -> list[Feedback]:
        """The organization's ratings, newest first (at most ``MAX_EXPORTED``)."""
        if self._db is None:
            found = [f for (o, *_), f in _memory.items() if o == org_id and f.updated_at >= since]
            return sorted(found, key=lambda f: -f.updated_at)[:MAX_EXPORTED]
        async with self._db.session(tenant_org_id=org_id) as session:
            rows = await session.scalars(
                select(AnswerFeedbackModel)
                .where(
                    AnswerFeedbackModel.org_id == org_id, AnswerFeedbackModel.updated_at >= since
                )
                .order_by(AnswerFeedbackModel.updated_at.desc())
                .limit(MAX_EXPORTED)
            )
            return [_view(r) for r in rows.all()]


def reset_memory_feedback() -> None:
    _memory.clear()

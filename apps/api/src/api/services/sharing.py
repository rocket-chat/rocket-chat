"""Who a session is shared with, and what a public link shows."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select

from api.db.models import UserModel
from specifications.interfaces.session import SessionRecord

PUBLIC_LINK_KEY = "public_link"


@dataclass(frozen=True)
class Member:
    user_id: str
    email: str
    name: str


def parse_emails(raw: list[str]) -> list[str]:
    """Distinct, lower-cased addresses; a comma- or space-separated entry is split."""
    seen: dict[str, None] = {}
    for entry in raw:
        for part in entry.replace(";", ",").replace("\n", ",").replace(" ", ",").split(","):
            if address := part.strip().lower():
                seen.setdefault(address)
    return list(seen)


async def members_by_email(db_mgr: Any, org_id: str, emails: list[str]) -> list[Member]:
    """The organization's members among ``emails`` (without a database, nobody can be found)."""
    if db_mgr is None or not getattr(db_mgr, "session_factory", None) or not emails:
        return []
    async with db_mgr.session(tenant_org_id=org_id) as session:
        query = select(UserModel).where(
            UserModel.org_id == org_id, func.lower(UserModel.email).in_(emails)
        )
        return [
            Member(u.id, u.email, u.name) for u in (await session.execute(query)).scalars().all()
        ]


async def members_by_id(db_mgr: Any, org_id: str, user_ids: list[str]) -> list[Member]:
    if db_mgr is None or not getattr(db_mgr, "session_factory", None) or not user_ids:
        return []
    async with db_mgr.session(tenant_org_id=org_id) as session:
        query = select(UserModel).where(UserModel.org_id == org_id, UserModel.id.in_(user_ids))
        return [
            Member(u.id, u.email, u.name) for u in (await session.execute(query)).scalars().all()
        ]


def public_link_of(record: SessionRecord) -> dict[str, Any] | None:
    link = record.metadata.get(PUBLIC_LINK_KEY) if isinstance(record.metadata, dict) else None
    return link if isinstance(link, dict) and link.get("id") else None


def public_transcript(record: SessionRecord) -> list[dict[str, str]]:
    """What a stranger holding the link may read: what was asked and what was answered.

    Tool calls, reasoning and system messages stay private; they can hold file contents,
    commands and credentials that the owner never meant to publish.
    """
    return [
        {"role": m.role, "content": m.content}
        for m in record.conversation_history
        if m.role in ("user", "assistant") and m.content and m.content.strip()
    ]

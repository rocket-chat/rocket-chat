"""Who a session is shared with, and what a public link shows."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select

from api.db.models import UserModel
from api.services.memberships import belongs_to
from specifications.interfaces.llm import ChatMessage
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
        query = select(UserModel).where(belongs_to(org_id), func.lower(UserModel.email).in_(emails))
        return [
            Member(u.id, u.email, u.name) for u in (await session.execute(query)).scalars().all()
        ]


async def members_by_id(db_mgr: Any, org_id: str, user_ids: list[str]) -> list[Member]:
    if db_mgr is None or not getattr(db_mgr, "session_factory", None) or not user_ids:
        return []
    async with db_mgr.session(tenant_org_id=org_id) as session:
        query = select(UserModel).where(belongs_to(org_id), UserModel.id.in_(user_ids))
        return [
            Member(u.id, u.email, u.name) for u in (await session.execute(query)).scalars().all()
        ]


def public_link_of(record: SessionRecord) -> dict[str, Any] | None:
    link = record.metadata.get(PUBLIC_LINK_KEY) if isinstance(record.metadata, dict) else None
    return link if isinstance(link, dict) and link.get("id") else None


# What a public link shows of the work behind each answer: bounded, and redacted with the
# organization's rules (the link is read by anyone holding it).
MAX_PUBLIC_STEPS = 60
PUBLIC_ARGUMENTS_CHARS = 1_500
MAX_PUBLIC_ARTIFACTS = 20
PUBLIC_ARTIFACT_CHARS = 20_000
_EDIT_TOOLS = frozenset({"file_write", "file_edit", "apply_patch", "apply_diff_file"})

Redact = Callable[[str], str]


def _clamped(text: str, limit: int) -> str:
    return (
        text if len(text) <= limit else f"{text[:limit]}\n[... {len(text) - limit} characters cut]"
    )


def _text(content: Any) -> str:
    if isinstance(content, list):
        return " ".join(str(p.get("text") or "") for p in content if isinstance(p, dict))
    return str(content or "")


def _steps_of(message: ChatMessage, redact: Redact) -> list[dict[str, str]]:
    """The calls behind an answer: name and arguments (an edit's arguments are its diff).
    Never their output: a command's output or a file's content can hold anything, and a
    redaction rule cannot be trusted to catch everything a stranger must not read."""
    steps = []
    for call in message.tool_calls or []:
        function = call.get("function") or {}
        name = str(function.get("name") or "tool")
        arguments = _clamped(str(function.get("arguments") or ""), PUBLIC_ARGUMENTS_CHARS)
        steps.append(
            {
                "name": name,
                "kind": "edit" if name in _EDIT_TOOLS else "step",
                "arguments": redact(arguments),
            }
        )
    return steps


def public_transcript(record: SessionRecord, redact: Redact) -> list[dict[str, Any]]:
    """What a stranger holding the link may read: what was asked, what was answered, and the
    steps behind each answer (collapsed on the page), redacted and bounded.

    Reasoning, tool output and system messages stay private.
    """
    history = record.conversation_history
    shown: list[dict[str, Any]] = []
    pending: list[dict[str, str]] = []
    for message in history:
        if message.role == "assistant" and message.tool_calls:
            pending.extend(_steps_of(message, redact))
        text = _text(message.content).strip()
        if message.role not in ("user", "assistant") or not text:
            continue
        entry: dict[str, Any] = {"role": message.role, "content": redact(text)}
        if message.role == "assistant" and pending:
            entry["steps"] = pending[-MAX_PUBLIC_STEPS:]
            entry["steps_cut"] = max(0, len(pending) - MAX_PUBLIC_STEPS)
            pending = []
        shown.append(entry)
    return shown


def public_artifacts(artifacts: list[Any], redact: Redact) -> list[dict[str, str]]:
    """The session's artifacts (latest version of each), redacted and bounded."""
    return [
        {
            "title": str(a.title),
            "filename": str(a.filename),
            "artifact_type": str(a.artifact_type),
            "content": redact(_clamped(str(a.content or ""), PUBLIC_ARTIFACT_CHARS)),
        }
        for a in artifacts[:MAX_PUBLIC_ARTIFACTS]
    ]

"""Slack account linking and workspace binding.

* **Workspace binding** maps a Slack workspace (team) to the organization that owns it, so
  Slack events are processed under the right tenant and policy instead of ``default_org``.
* **Account pairing** links a Slack user to a Rocket member. The Slack side only ever
  *starts* the flow (``/rocket-pair`` issues a one-time code); the link is completed by the
  member from their authenticated Rocket session, so typing someone's e-mail address in
  Slack can never claim their account.
"""

from __future__ import annotations

import hashlib
import secrets
import time
from dataclasses import dataclass
from typing import Any

from sqlalchemy import delete, select

from api.db.models import SlackPairingCodeModel, SlackWorkspaceModel, UserModel

PAIRING_CODE_TTL_SECONDS = 600
_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O/1/I lookalikes
_MAX_OUTSTANDING_CODES = 10_000


@dataclass(frozen=True)
class _PendingPairing:
    slack_user_id: str
    team_id: str
    expires_at: float


def _hash(code: str) -> str:
    return hashlib.sha256(code.strip().upper().encode()).hexdigest()


class PairingCodes:
    """Single-use, short-lived pairing codes.

    With a database they are shared by every replica (only a hash is stored); without one
    (development) they live in this process.
    """

    def __init__(
        self,
        db_mgr: Any = None,
        ttl_seconds: int = PAIRING_CODE_TTL_SECONDS,
        clock: Any = time.time,
    ) -> None:
        self._db = db_mgr if _has_database(db_mgr) else None
        self._ttl = ttl_seconds
        self._clock = clock
        self._pending: dict[str, _PendingPairing] = {}

    def _purge_memory(self) -> None:
        now = self._clock()
        for code in [c for c, p in self._pending.items() if p.expires_at <= now]:
            del self._pending[code]
        while len(self._pending) > _MAX_OUTSTANDING_CODES:
            self._pending.pop(next(iter(self._pending)))

    async def begin(self, slack_user_id: str, team_id: str) -> str:
        """Issue a code for ``slack_user_id`` (any earlier code of that user is revoked)."""
        code = "".join(secrets.choice(_CODE_ALPHABET) for _ in range(8))
        expires_at = self._clock() + self._ttl
        if self._db is None:
            self._purge_memory()
            for old in [c for c, p in self._pending.items() if p.slack_user_id == slack_user_id]:
                del self._pending[old]
            self._pending[code] = _PendingPairing(slack_user_id, team_id, expires_at)
            return code
        async with self._db.session() as session:
            await session.execute(
                delete(SlackPairingCodeModel).where(
                    (SlackPairingCodeModel.slack_user_id == slack_user_id)
                    | (SlackPairingCodeModel.expires_at <= int(self._clock()))
                )
            )
            session.add(
                SlackPairingCodeModel(
                    code_hash=_hash(code),
                    slack_user_id=slack_user_id,
                    team_id=team_id,
                    expires_at=int(expires_at),
                )
            )
        return code

    async def complete(self, code: str) -> tuple[str, str] | None:
        """Consume ``code`` and return ``(slack_user_id, team_id)``, or ``None`` if invalid."""
        if self._db is None:
            self._purge_memory()
            pending = self._pending.pop(code.strip().upper(), None)
            return (pending.slack_user_id, pending.team_id) if pending is not None else None
        async with self._db.session() as session:
            # DELETE ... RETURNING makes consumption atomic: two replicas racing on one code
            # cannot both win it.
            row = (
                await session.execute(
                    delete(SlackPairingCodeModel)
                    .where(
                        SlackPairingCodeModel.code_hash == _hash(code),
                        SlackPairingCodeModel.expires_at > int(self._clock()),
                    )
                    .returning(SlackPairingCodeModel.slack_user_id, SlackPairingCodeModel.team_id)
                )
            ).first()
        return (str(row[0]), str(row[1])) if row is not None else None


_memory_workspaces: dict[str, str] = {}


def reset_memory_workspaces() -> None:
    _memory_workspaces.clear()


def _has_database(db_mgr: Any) -> bool:
    return db_mgr is not None and getattr(db_mgr, "session_factory", None) is not None


async def workspace_org(db_mgr: Any, team_id: str) -> str | None:
    """The organization bound to ``team_id``, if any."""
    if not team_id:
        return None
    if not _has_database(db_mgr):
        return _memory_workspaces.get(team_id)
    async with db_mgr.session() as session:
        row = (
            await session.execute(
                select(SlackWorkspaceModel.org_id).where(SlackWorkspaceModel.team_id == team_id)
            )
        ).scalar_one_or_none()
        return str(row) if row is not None else None


async def bind_workspace(db_mgr: Any, team_id: str, org_id: str, bound_by: str) -> None:
    """Bind ``team_id`` to ``org_id``; raises ``ValueError`` if another org already owns it."""
    current = await workspace_org(db_mgr, team_id)
    if current is not None and current != org_id:
        raise ValueError(f"Slack workspace {team_id} is already bound to another organization")
    if not _has_database(db_mgr):
        _memory_workspaces[team_id] = org_id
        return
    async with db_mgr.session() as session:
        if current is None:
            session.add(
                SlackWorkspaceModel(
                    team_id=team_id, org_id=org_id, bound_by=bound_by, created_at=int(time.time())
                )
            )


async def unbind_workspace(db_mgr: Any, team_id: str, org_id: str) -> bool:
    """Remove the binding if ``org_id`` owns it."""
    if await workspace_org(db_mgr, team_id) != org_id:
        return False
    if not _has_database(db_mgr):
        _memory_workspaces.pop(team_id, None)
        return True
    async with db_mgr.session() as session:
        row = await session.get(SlackWorkspaceModel, team_id)
        if row is not None:
            await session.delete(row)
    return True


async def find_member(
    db_mgr: Any, slack_user_id: str, org_id: str, email: str | None = None
) -> tuple[str, str] | None:
    """The Rocket member linked to ``slack_user_id`` *inside* ``org_id``.

    Matching by e-mail is only used when the caller explicitly allows it, and never
    crosses organizations: another tenant's member with the same address is not a match.
    """
    if not _has_database(db_mgr):
        return None
    async with db_mgr.session(tenant_org_id=org_id) as session:
        members = (
            (await session.execute(select(UserModel).where(UserModel.org_id == org_id)))
            .scalars()
            .all()
        )
        for member in members:
            settings = member.settings if isinstance(member.settings, dict) else {}
            if settings.get("slack_user_id") == slack_user_id:
                return member.id, member.org_id
            if email and member.email.lower() == email.lower():
                return member.id, member.org_id
    return None


async def link_member(db_mgr: Any, member_id: str, org_id: str, slack_user_id: str) -> bool:
    """Record ``slack_user_id`` on the member (the confirmed half of the pairing)."""
    if not _has_database(db_mgr):
        return False
    async with db_mgr.session(tenant_org_id=org_id) as session:
        member = await session.get(UserModel, member_id)
        if member is None or member.org_id != org_id:
            return False
        updated = dict(member.settings) if isinstance(member.settings, dict) else {}
        updated["slack_user_id"] = slack_user_id
        member.settings = updated
    return True

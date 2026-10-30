"""Who belongs to which organization, with which role, and the invitations that add people.

``users`` is a person's global identity; ``memberships`` (forced RLS) says which organizations
they belong to and their role in each. A person signs in to one organization at a time (the
session token's ``org_id``) and switches with ``POST /v1/auth/switch-org``.

For one release a person's *home* organization (``users.org_id``) still counts as a
membership with ``users.role`` when no membership row exists, so an identity created by code
that predates memberships keeps its rights.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth.roles import ADMIN, OWNER, canonical_role
from api.db.models import MembershipModel, OrganizationModel, OrgInviteModel, UserModel

INVITE_PREFIX = "rcinv_"
INVITE_LIFETIME_DAYS = 7
MAX_INVITE_LIFETIME_DAYS = 30
_ADMIN_ROLES = (OWNER, ADMIN)


@dataclass(frozen=True)
class Membership:
    org_id: str
    org_name: str
    role: str


@dataclass(frozen=True)
class Member:
    user_id: str
    email: str
    name: str
    role: str
    created_at: int


@dataclass(frozen=True)
class Invite:
    id: str
    org_id: str
    email: str
    role: str
    expires_at: int
    created_by: str
    created_at: int
    accepted_at: int | None


class InviteInvalid(Exception):
    """The invitation does not exist, was used, expired, or names another e-mail address."""


def _has_database(db_mgr: Any) -> bool:
    return db_mgr is not None and getattr(db_mgr, "session_factory", None) is not None


async def role_in(session: AsyncSession, user_id: str, org_id: str) -> str | None:
    """The person's role in ``org_id``, or ``None`` when they are not a member."""
    row = (
        await session.execute(
            select(MembershipModel.role).where(
                MembershipModel.user_id == user_id, MembershipModel.org_id == org_id
            )
        )
    ).scalar_one_or_none()
    if row is not None:
        return canonical_role(row)
    user = await session.get(UserModel, user_id)
    if user is not None and user.org_id == org_id:
        return canonical_role(user.role)
    return None


def belongs_to(org_id: str) -> Any:
    """A ``users`` condition: the person is a member of ``org_id`` (a membership, or home)."""
    return or_(
        UserModel.org_id == org_id,
        UserModel.id.in_(select(MembershipModel.user_id).where(MembershipModel.org_id == org_id)),
    )


async def ensure_membership(
    session: AsyncSession, user_id: str, org_id: str, role: str
) -> MembershipModel:
    """The person's membership in ``org_id``, created with ``role`` when there is none."""
    existing = (
        await session.execute(
            select(MembershipModel).where(
                MembershipModel.user_id == user_id, MembershipModel.org_id == org_id
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    membership = MembershipModel(
        id=f"mbr_{uuid.uuid4().hex[:16]}",
        user_id=user_id,
        org_id=org_id,
        role=canonical_role(role) if role != "guest" else "guest",
        created_at=int(time.time()),
    )
    session.add(membership)
    await session.flush()
    return membership


async def member_count(session: AsyncSession, org_id: str, *, exclude: tuple[str, ...]) -> int:
    """How many people belong to ``org_id`` (``exclude``: identities that are not people)."""
    return int(
        (
            await session.execute(
                select(func.count(MembershipModel.id)).where(
                    MembershipModel.org_id == org_id, MembershipModel.user_id.not_in(exclude)
                )
            )
        ).scalar_one()
    )


async def admin_count(session: AsyncSession, org_id: str) -> int:
    return int(
        (
            await session.execute(
                select(func.count(MembershipModel.id)).where(
                    MembershipModel.org_id == org_id, MembershipModel.role.in_(_ADMIN_ROLES)
                )
            )
        ).scalar_one()
    )


async def member_display_name(db_mgr: Any, org_id: str, user_id: str) -> str:
    """A member's name as people know it (their e-mail when they have none), or ""."""
    if db_mgr is None:
        return ""
    async with db_mgr.session(tenant_org_id=org_id) as session:
        user = await session.get(UserModel, user_id)
    return (user.name or user.email) if user is not None else ""


async def memberships_of(db_mgr: Any, user_id: str) -> list[Membership]:
    """Every organization the person belongs to (their own rows, across organizations)."""
    if not _has_database(db_mgr):
        return []
    async with db_mgr.session(tenant_user_id=user_id) as session:
        rows = (
            await session.execute(
                select(MembershipModel.org_id, MembershipModel.role)
                .where(MembershipModel.user_id == user_id)
                .order_by(MembershipModel.created_at)
            )
        ).all()
    memberships: list[Membership] = []
    for org_id, role in rows:
        async with db_mgr.session(tenant_org_id=org_id) as session:
            org = await session.get(OrganizationModel, org_id)
        memberships.append(Membership(org_id, org.name if org else org_id, canonical_role(role)))
    return memberships


async def members(
    session: AsyncSession, org_id: str, *, q: str | None, role: str | None, limit: int
) -> tuple[list[Member], int]:
    """The organization's members (people with a membership row), filtered."""
    conditions: list[Any] = [MembershipModel.org_id == org_id]
    if q:
        like = f"%{q.strip()}%"
        conditions.append(UserModel.email.ilike(like) | UserModel.name.ilike(like))
    if role:
        conditions.append(MembershipModel.role == canonical_role(role))
    joined = select(UserModel, MembershipModel).join(
        MembershipModel, MembershipModel.user_id == UserModel.id
    )
    rows = (
        await session.execute(
            joined.where(*conditions)
            .order_by(MembershipModel.created_at.asc(), UserModel.id.asc())
            .limit(limit)
        )
    ).all()
    total = int(
        (
            await session.execute(
                select(func.count())
                .select_from(MembershipModel)
                .join(UserModel, MembershipModel.user_id == UserModel.id)
                .where(*conditions)
            )
        ).scalar_one()
    )
    return [
        Member(user.id, user.email, user.name, canonical_role(m.role), m.created_at)
        for user, m in rows
    ], total


async def membership_row(
    session: AsyncSession, user_id: str, org_id: str
) -> MembershipModel | None:
    return (
        await session.execute(
            select(MembershipModel).where(
                MembershipModel.user_id == user_id, MembershipModel.org_id == org_id
            )
        )
    ).scalar_one_or_none()


def _hash(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


def _invite(row: OrgInviteModel) -> Invite:
    return Invite(
        row.id,
        row.org_id,
        row.email,
        row.role,
        row.expires_at,
        row.created_by,
        row.created_at,
        row.accepted_at,
    )


def parse_invite_token(token: str) -> tuple[str, str, str] | None:
    """``(org_id, invite_id, secret)`` from ``rcinv_<org>.<id>.<secret>``."""
    if not token.startswith(INVITE_PREFIX):
        return None
    parts = token[len(INVITE_PREFIX) :].split(".")
    if len(parts) != 3 or not all(parts):
        return None
    return parts[0], parts[1], parts[2]


async def create_invite(
    db_mgr: Any, org_id: str, email: str, role: str, created_by: str, lifetime_days: int
) -> tuple[Invite, str]:
    """A new invitation and its token (shown once; only its hash is stored)."""
    now = int(time.time())
    invite_id = secrets.token_hex(8)
    secret = secrets.token_urlsafe(24)
    row = OrgInviteModel(
        id=invite_id,
        org_id=org_id,
        email=email.strip().lower(),
        role=canonical_role(role),
        token_hash=_hash(secret),
        expires_at=now + lifetime_days * 86_400,
        created_by=created_by,
        created_at=now,
    )
    async with db_mgr.session(tenant_org_id=org_id) as session:
        session.add(row)
    return _invite(row), f"{INVITE_PREFIX}{org_id}.{invite_id}.{secret}"


async def list_invites(db_mgr: Any, org_id: str) -> list[Invite]:
    async with db_mgr.session(tenant_org_id=org_id) as session:
        rows = (
            await session.execute(
                select(OrgInviteModel)
                .where(OrgInviteModel.org_id == org_id)
                .order_by(OrgInviteModel.created_at.desc())
            )
        ).scalars()
        return [_invite(row) for row in rows]


async def revoke_invite(db_mgr: Any, org_id: str, invite_id: str) -> bool:
    async with db_mgr.session(tenant_org_id=org_id) as session:
        row = await session.get(OrgInviteModel, invite_id)
        if row is None or row.org_id != org_id:
            return False
        await session.delete(row)
        return True


async def accept_invite(db_mgr: Any, token: str, user_id: str, email: str) -> Membership:
    """Make the signed-in person a member of the invitation's organization.

    The invitation names an e-mail address; only the person signed in with it may use it, once.
    """
    parsed = parse_invite_token(token)
    if parsed is None:
        raise InviteInvalid("This invitation link is not valid.")
    org_id, invite_id, secret = parsed
    now = int(time.time())
    async with db_mgr.session(tenant_org_id=org_id) as session:
        row = await session.get(OrgInviteModel, invite_id)
        if (
            row is None
            or row.org_id != org_id
            or not hmac.compare_digest(row.token_hash, _hash(secret))
        ):
            raise InviteInvalid("This invitation link is not valid.")
        if row.accepted_at is not None:
            raise InviteInvalid("This invitation was already used.")
        if row.expires_at <= now:
            raise InviteInvalid("This invitation has expired; ask for a new one.")
        if row.email != email.strip().lower():
            raise InviteInvalid("This invitation is for another e-mail address.")
        membership = await ensure_membership(session, user_id, org_id, row.role)
        row.accepted_at, row.accepted_by = now, user_id
        org = await session.get(OrganizationModel, org_id)
        return Membership(org_id, org.name if org else org_id, canonical_role(membership.role))

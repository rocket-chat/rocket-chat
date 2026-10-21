"""Personal API tokens: how scripts and other machine clients authenticate.

A member creates a token while signed in and sends it as ``Authorization: Bearer <token>``
(or ``?token=`` on a WebSocket). The token acts as that member, with the role the member holds
when it is used, until it expires or is revoked.

An administrator can also create a *guest* token, for a client that has no Rocket account, as
Slack users who never linked theirs. It acts as its own guest member (``apitoken:<token id>``),
which cannot sign in and owns the sessions the token starts. A guest token can only hold
conversations: create and read its sessions, run turns, answer questions and approvals.

Format: ``rcpat_<organization, base64url>.<token id>.<secret>``. The organization is part of
the token because the table is under row-level security: the lookup has to run in that
organization's context before anyone is known. Only a SHA-256 hash of the secret is stored;
the secret is 256 random bits, so a fast hash is enough and a slow one would only slow every
request down.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import re
import secrets
import time
from dataclasses import dataclass

from sqlalchemy import delete, func, select

from api.auth.context import RequestContext
from api.auth.roles import roles_for
from api.db.models import ApiTokenModel, UserModel
from api.db.session import DatabaseSessionManager

TOKEN_PREFIX = "rcpat_"
MAX_TOKENS_PER_MEMBER = 20
MAX_LIFETIME_DAYS = 365
DEFAULT_LIFETIME_DAYS = 90
# ``last_used_at`` is for people deciding which token to revoke, not an audit trail: writing it
# at most once a minute keeps a busy script from turning every request into a write.
LAST_USED_RESOLUTION_SECONDS = 60

GUEST_ID_PREFIX = "apitoken:"
GUEST_ROLE = "guest"
GUEST_SETTINGS_KEY = "api_token_guest"
GUEST_TOKEN_SOURCE = "guest_token"
# What a guest token may call: its conversations, nothing about the organization or itself.
_GUEST_ROUTES: tuple[tuple[frozenset[str], re.Pattern[str]], ...] = (
    (frozenset({"GET"}), re.compile(r"/v1/agents")),
    (frozenset({"GET", "POST"}), re.compile(r"/v1/sessions")),
    (frozenset({"GET"}), re.compile(r"/v1/sessions/[^/]+")),
    (frozenset({"GET", "POST", "DELETE"}), re.compile(r"/v1/sessions/[^/]+/turns")),
    (frozenset({"POST"}), re.compile(r"/v1/sessions/[^/]+/(questions|approvals)/[^/]+")),
    (frozenset({"GET", "POST"}), re.compile(r"/v1/sessions/[^/]+/attachments")),
    (
        frozenset({"GET"}),
        re.compile(r"/v1/sessions/[^/]+/(artifacts|attachments|subagents)/[^/]+(/raw)?"),
    ),
    (frozenset({"GET"}), re.compile(r"/v1/sessions/[^/]+/(artifacts|subagents)")),
)


class TooManyTokens(Exception):
    """The member already holds ``MAX_TOKENS_PER_MEMBER`` tokens."""


@dataclass(frozen=True)
class StoredToken:
    id: str
    user_id: str
    name: str
    created_at: int
    expires_at: int
    last_used_at: int | None

    @property
    def guest(self) -> bool:
        return self.user_id.startswith(GUEST_ID_PREFIX)


@dataclass(frozen=True)
class ParsedToken:
    org_id: str
    token_id: str
    secret: str


def is_api_token(raw: str) -> bool:
    return raw.startswith(TOKEN_PREFIX)


def parse_token(raw: str) -> ParsedToken | None:
    """The parts of a well-formed token, or ``None``."""
    if not is_api_token(raw):
        return None
    parts = raw[len(TOKEN_PREFIX) :].split(".")
    if len(parts) != 3 or not all(parts):
        return None
    encoded_org, token_id, secret = parts
    try:
        org_id = base64.urlsafe_b64decode(encoded_org + "=" * (-len(encoded_org) % 4)).decode()
    except (binascii.Error, UnicodeDecodeError):
        return None
    return ParsedToken(org_id=org_id, token_id=token_id, secret=secret) if org_id else None


def format_token(org_id: str, token_id: str, secret: str) -> str:
    encoded_org = base64.urlsafe_b64encode(org_id.encode()).decode().rstrip("=")
    return f"{TOKEN_PREFIX}{encoded_org}.{token_id}.{secret}"


def hash_secret(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


def guest_may_call(method: str, path: str) -> bool:
    """Whether a guest token may make this HTTP request."""
    return any(
        method in methods and pattern.fullmatch(path.rstrip("/") or "/")
        for methods, pattern in _GUEST_ROUTES
    )


def _new_token(
    org_id: str, user_id: str | None, name: str, lifetime_days: int
) -> tuple[ApiTokenModel, str]:
    """A token row and the token; without ``user_id`` it acts as its own guest member."""
    now = int(time.time())
    token_id = secrets.token_hex(8)
    secret = secrets.token_urlsafe(32)
    row = ApiTokenModel(
        id=token_id,
        org_id=org_id,
        user_id=user_id or f"{GUEST_ID_PREFIX}{token_id}",
        name=name,
        secret_hash=hash_secret(secret),
        created_at=now,
        expires_at=now + lifetime_days * 86_400,
    )
    return row, format_token(org_id, token_id, secret)


def _stored(row: ApiTokenModel) -> StoredToken:
    return StoredToken(
        id=row.id,
        user_id=row.user_id,
        name=row.name,
        created_at=row.created_at,
        expires_at=row.expires_at,
        last_used_at=row.last_used_at,
    )


class ApiTokens:
    """Create, list, revoke and verify personal API tokens (PostgreSQL only)."""

    def __init__(self, db_mgr: DatabaseSessionManager | None) -> None:
        self._db = db_mgr if db_mgr is not None and db_mgr.session_factory else None

    @property
    def available(self) -> bool:
        return self._db is not None

    def _database(self) -> DatabaseSessionManager:
        if self._db is None:
            raise RuntimeError("API tokens need the PostgreSQL store")
        return self._db

    async def create(
        self, org_id: str, user_id: str, name: str, lifetime_days: int
    ) -> tuple[StoredToken, str]:
        """A new token for the member: its record and the token, which is never shown again."""
        row, token = _new_token(org_id, user_id, name, lifetime_days)
        async with self._database().session(tenant_org_id=org_id) as session:
            held = await session.scalar(
                select(func.count())
                .select_from(ApiTokenModel)
                .where(ApiTokenModel.org_id == org_id, ApiTokenModel.user_id == user_id)
            )
            if (held or 0) >= MAX_TOKENS_PER_MEMBER:
                raise TooManyTokens
            session.add(row)
        return _stored(row), token

    async def create_guest(
        self, org_id: str, name: str, lifetime_days: int, created_by: str
    ) -> tuple[StoredToken, str]:
        """A new guest token, with the guest member it acts as, and the token (never shown again)."""
        row, token = _new_token(org_id, None, name, lifetime_days)
        async with self._database().session(tenant_org_id=org_id) as session:
            session.add(
                UserModel(
                    id=row.user_id,
                    org_id=org_id,
                    email=f"apitoken.{row.id}@api-token-guest.invalid",
                    name=name,
                    role=GUEST_ROLE,
                    settings={GUEST_SETTINGS_KEY: {"created_by": created_by}},
                    created_at=row.created_at,
                )
            )
            await session.flush()
            session.add(row)
        return _stored(row), token

    async def list(self, org_id: str, user_id: str | None) -> list[StoredToken]:
        """The member's tokens, or the whole organization's when ``user_id`` is ``None``."""
        statement = select(ApiTokenModel).where(ApiTokenModel.org_id == org_id)
        if user_id is not None:
            statement = statement.where(ApiTokenModel.user_id == user_id)
        async with self._database().session(tenant_org_id=org_id) as session:
            rows = (await session.execute(statement.order_by(ApiTokenModel.created_at))).scalars()
            return [_stored(row) for row in rows]

    async def revoke(self, org_id: str, token_id: str, user_id: str | None) -> StoredToken | None:
        """Delete a token (only the member's own unless ``user_id`` is ``None``)."""
        async with self._database().session(tenant_org_id=org_id) as session:
            row = await session.get(ApiTokenModel, token_id)
            if row is None or row.org_id != org_id or user_id not in (None, row.user_id):
                return None
            revoked = _stored(row)
            await session.execute(delete(ApiTokenModel).where(ApiTokenModel.id == token_id))
            return revoked

    async def authenticate(self, raw: str) -> RequestContext | None:
        """The member a token acts as, with the role they hold now (a guest token, always the
        guest role); ``None`` if it is not valid."""
        parsed = parse_token(raw)
        if parsed is None or self._db is None:
            return None
        now = int(time.time())
        async with self._db.session(tenant_org_id=parsed.org_id) as session:
            row = await session.get(ApiTokenModel, parsed.token_id)
            if (
                row is None
                or row.org_id != parsed.org_id
                or row.expires_at <= now
                or not hmac.compare_digest(row.secret_hash, hash_secret(parsed.secret))
            ):
                return None
            user = await session.get(UserModel, row.user_id)
            if user is None or user.org_id != row.org_id:
                return None
            if row.last_used_at is None or now - row.last_used_at >= LAST_USED_RESOLUTION_SECONDS:
                row.last_used_at = now
            guest = isinstance(user.settings, dict) and GUEST_SETTINGS_KEY in user.settings
            return RequestContext(
                user_id=user.id,
                org_id=user.org_id,
                email=user.email,
                team_id=user.team_id,
                name=user.name,
                roles=[GUEST_ROLE] if guest else roles_for(user.role),
                source=GUEST_TOKEN_SOURCE if guest else "api_token",
            )

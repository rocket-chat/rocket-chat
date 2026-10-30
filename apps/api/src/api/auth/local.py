"""Local (email + password) accounts for self-hosted installs without an identity provider.

Disabled unless ``LOCAL_AUTH_ENABLED`` is set. Passwords are stored as argon2id hashes only.
Online guessing is bounded two ways: an account locks for a while after repeated failures
(the counter lives in the database, so every replica agrees), and a per-address throttle
slows a single source spraying many accounts. Failures never reveal whether the account
exists: an unknown e-mail still pays for a hash verification.
"""

from __future__ import annotations

import logging
import os
import secrets
import time
from collections import defaultdict, deque
from dataclasses import dataclass

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from sqlalchemy import select

from api.db.models import UserModel
from api.db.session import DatabaseSessionManager
from api.services.memberships import ensure_membership

logger = logging.getLogger(__name__)

MIN_PASSWORD_LENGTH = 12
MAX_PASSWORD_LENGTH = 128
MAX_FAILED_ATTEMPTS = 5
LOCKOUT_SECONDS = 15 * 60
IP_ATTEMPTS_PER_MINUTE = 20

_hasher = PasswordHasher()
# Verified when the e-mail is unknown, so "no such user" costs the same as "wrong password".
_DUMMY_HASH = _hasher.hash("rocket-chat-timing-equalizer")
_TRUTHY = ("true", "1", "yes")


def local_auth_enabled() -> bool:
    return os.getenv("LOCAL_AUTH_ENABLED", "").strip().lower() in _TRUTHY


class LoginFailed(Exception):
    """Wrong e-mail or password (deliberately indistinguishable)."""


class AccountLocked(Exception):
    def __init__(self, retry_after_seconds: int) -> None:
        super().__init__("Too many failed attempts")
        self.retry_after_seconds = retry_after_seconds


class WeakPassword(ValueError):
    """The password does not meet the policy; the message says why."""


@dataclass(frozen=True)
class LocalIdentity:
    user_id: str
    org_id: str
    email: str
    name: str
    role: str
    must_change_password: bool


def check_password_policy(password: str, email: str = "") -> None:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise WeakPassword(f"Use at least {MIN_PASSWORD_LENGTH} characters.")
    if len(password) > MAX_PASSWORD_LENGTH:
        raise WeakPassword(f"Use at most {MAX_PASSWORD_LENGTH} characters.")
    local_part = email.split("@", 1)[0].lower()
    if password.lower() in {email.lower(), local_part} or (
        len(local_part) >= 4 and local_part in password.lower()
    ):
        raise WeakPassword("The password must not contain your e-mail address.")
    if len(set(password)) < 5:
        raise WeakPassword("The password is too repetitive.")


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(stored_hash: str | None, password: str) -> bool:
    try:
        return _hasher.verify(stored_hash or _DUMMY_HASH, password) and stored_hash is not None
    except (VerificationError, InvalidHashError):
        return False


def generate_temporary_password() -> str:
    """A random password for an administrator to hand over; the member must change it."""
    return secrets.token_urlsafe(18)


class AddressThrottle:
    """Sliding-window attempt limiter per source address (per replica)."""

    def __init__(self, limit: int = IP_ATTEMPTS_PER_MINUTE, window_seconds: float = 60.0) -> None:
        self._limit = limit
        self._window = window_seconds
        self._attempts: dict[str, deque[float]] = defaultdict(deque)

    def hit(self, address: str, now: float | None = None) -> bool:
        """Count an attempt; ``False`` if the address is over its limit."""
        moment = now if now is not None else time.monotonic()
        attempts = self._attempts[address]
        while attempts and moment - attempts[0] > self._window:
            attempts.popleft()
        if len(attempts) >= self._limit:
            return False
        attempts.append(moment)
        if len(self._attempts) > 10_000:  # bound memory under a distributed spray
            self._attempts = defaultdict(deque, {address: attempts})
        return True


class LocalAccounts:
    """Password login, change and administrator-issued resets."""

    def __init__(self, db_mgr: DatabaseSessionManager | None) -> None:
        self._db = db_mgr if db_mgr is not None and db_mgr.session_factory else None

    @property
    def available(self) -> bool:
        return self._db is not None and local_auth_enabled()

    async def authenticate(self, email: str, password: str) -> LocalIdentity:
        """Verify credentials, applying lock-out; raises ``LoginFailed`` / ``AccountLocked``."""
        if self._db is None:
            raise LoginFailed
        normalized = email.strip().lower()
        now = int(time.time())
        locked_for: int | None = None
        identity: LocalIdentity | None = None
        # The outcome is decided inside the transaction but raised after it commits: raising
        # inside would roll the failed-attempt counter back and defeat the lock-out.
        async with self._db.session() as session:
            candidates = (
                (
                    await session.execute(
                        select(UserModel).where(
                            UserModel.email == normalized, UserModel.password_hash.is_not(None)
                        )
                    )
                )
                .scalars()
                .all()
            )
            user = candidates[0] if len(candidates) == 1 else None
            if user is not None and user.locked_until and user.locked_until > now:
                # Still pay for a verification so a lock is not a timing oracle.
                verify_password(user.password_hash, password)
                locked_for = user.locked_until - now
            elif not verify_password(user.password_hash if user else None, password):
                if user is not None:
                    user.failed_login_count += 1
                    if user.failed_login_count >= MAX_FAILED_ATTEMPTS:
                        user.locked_until = now + LOCKOUT_SECONDS
                        user.failed_login_count = 0
            elif user is not None:
                user.failed_login_count = 0
                user.locked_until = None
                if _hasher.check_needs_rehash(user.password_hash or ""):
                    user.password_hash = hash_password(password)
                identity = LocalIdentity(
                    user_id=user.id,
                    org_id=user.org_id,
                    email=user.email,
                    name=user.name,
                    role=user.role,
                    must_change_password=user.must_change_password,
                )
        if locked_for is not None:
            raise AccountLocked(locked_for)
        if identity is None:
            raise LoginFailed
        return identity

    async def change_password(self, user_id: str, current: str, new: str) -> None:
        """Replace the member's password after proving the current one."""
        if self._db is None:
            raise LoginFailed
        async with self._db.session() as session:
            user = await session.get(UserModel, user_id)
            if user is None or not verify_password(user.password_hash, current):
                raise LoginFailed
            check_password_policy(new, user.email)
            if verify_password(user.password_hash, new):
                raise WeakPassword("Choose a password different from the current one.")
            user.password_hash = hash_password(new)
            user.password_updated_at = int(time.time())
            user.must_change_password = False

    async def create_user(
        self,
        *,
        user_id: str,
        org_id: str,
        email: str,
        name: str,
        role: str,
        password: str,
        must_change: bool,
    ) -> None:
        """Create a local member (administrators); the e-mail must be unused."""
        if self._db is None:
            raise RuntimeError("Local accounts need the PostgreSQL store")
        normalized = email.strip().lower()
        check_password_policy(password, normalized)
        async with self._db.session(tenant_org_id=org_id) as session:
            taken = (
                await session.execute(select(UserModel.id).where(UserModel.email == normalized))
            ).first()
            if taken is not None:
                raise ValueError("That e-mail address is already registered.")
            session.add(
                UserModel(
                    id=user_id,
                    org_id=org_id,
                    email=normalized,
                    name=name,
                    role=role,
                    password_hash=hash_password(password),
                    password_updated_at=int(time.time()),
                    must_change_password=must_change,
                )
            )
            await session.flush()
            await ensure_membership(session, user_id, org_id, role)

    async def reset_password(self, org_id: str, user_id: str, password: str) -> bool:
        """Set a temporary password the member must change at next login (administrators)."""
        if self._db is None:
            return False
        async with self._db.session(tenant_org_id=org_id) as session:
            user = await session.get(UserModel, user_id)
            if user is None or user.org_id != org_id:
                return False
            check_password_policy(password, user.email)
            user.password_hash = hash_password(password)
            user.password_updated_at = int(time.time())
            user.must_change_password = True
            user.failed_login_count = 0
            user.locked_until = None
            return True


async def bootstrap_initial_admin(db_mgr: DatabaseSessionManager | None) -> bool:
    """Create the first administrator from ``INITIAL_ADMIN_EMAIL`` / ``INITIAL_ADMIN_PASSWORD``.

    Runs at startup when local accounts are enabled. It only ever *creates*: an existing
    account is never modified, so leaving the password in the environment cannot reset a
    changed one. Returns whether an account was created.
    """
    email = os.getenv("INITIAL_ADMIN_EMAIL", "").strip().lower()
    password = os.getenv("INITIAL_ADMIN_PASSWORD", "")
    accounts = LocalAccounts(db_mgr)
    if not (accounts.available and email and password):
        return False
    from api.auth.sso_policy import map_org

    org_id = map_org(email)
    try:
        await accounts.create_user(
            user_id=f"usr_{email.replace('@', '_').replace('.', '_')}",
            org_id=org_id,
            email=email,
            name=email.split("@", 1)[0].title(),
            role="admin",
            password=password,
            must_change=False,
        )
    except ValueError:
        return False  # already registered
    except WeakPassword as err:
        logger.error("INITIAL_ADMIN_PASSWORD rejected: %s", err)
        return False
    logger.info("Created the initial administrator %s", email)
    return True

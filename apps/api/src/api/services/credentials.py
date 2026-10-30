"""Encrypted credential management service storing and resolving user-level OAuth/PAT tokens."""

import base64
import hashlib
import logging
import os
import time
import uuid
from dataclasses import dataclass
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import select

from api.auth.roles import MEMBER
from api.db.models import OrganizationModel, UserCredentialModel, UserModel
from api.db.session import DatabaseSessionManager
from api.services.memberships import ensure_membership

logger = logging.getLogger(__name__)

# Fallback development key ensuring zero-config local operation
_DEV_KEY_SOURCE = "rocket-chat-development-symmetric-encryption-seed-2026"
# Without a database (development and tests): (org, user, provider) -> the sealed credential.
# Per organization, as the table is: a person who belongs to several keeps them apart.
_in_memory_credentials: dict[tuple[str, str, str], dict[str, Any]] = {}


def _get_fernet() -> Fernet:
    """Instantiate a Fernet cipher from environment configuration or stable dev seed."""
    raw_key = os.getenv("CREDENTIALS_ENCRYPTION_KEY")
    if raw_key:
        try:
            return Fernet(raw_key.encode("utf-8"))
        except Exception:
            # If raw string is not base64 32-bytes, derive via sha256
            derived = base64.urlsafe_b64encode(hashlib.sha256(raw_key.encode("utf-8")).digest())
            return Fernet(derived)

    # Deterministic fallback for dev/test environments
    derived_dev = base64.urlsafe_b64encode(hashlib.sha256(_DEV_KEY_SOURCE.encode("utf-8")).digest())
    return Fernet(derived_dev)


def encrypt_secret(plaintext: str) -> str:
    """Encrypt a secret string at rest using authenticated symmetric encryption."""
    if not plaintext:
        return ""
    cipher = _get_fernet()
    return cipher.encrypt(plaintext.encode("utf-8")).decode("utf-8")


def decrypt_secret(ciphertext: str) -> str:
    """Decrypt a ciphertext token, raising ValueError on corruption or key mismatch."""
    if not ciphertext:
        return ""
    cipher = _get_fernet()
    try:
        return cipher.decrypt(ciphertext.encode("utf-8")).decode("utf-8")
    except InvalidToken as err:
        raise ValueError(
            "Failed to decrypt secret: invalid token or encryption key mismatch"
        ) from err


@dataclass
class UserCredentialData:
    """Decrypted user credential data object."""

    user_id: str
    org_id: str
    provider: str
    secret: str
    metadata: dict[str, Any]
    created_at: int
    updated_at: int


async def store_user_credential(
    db_mgr: DatabaseSessionManager | None,
    user_id: str,
    org_id: str,
    provider: str,
    secret: str,
    metadata: dict[str, Any] | None = None,
) -> UserCredentialData:
    """Persist or update encrypted user credential in database and in-memory cache."""
    now = int(time.time())
    meta = metadata or {}
    encrypted = encrypt_secret(secret)

    # In-memory sync for fast reads and non-db fallback
    _in_memory_credentials[(org_id, user_id, provider)] = {
        "user_id": user_id,
        "org_id": org_id,
        "provider": provider,
        "encrypted_secret": encrypted,
        "metadata_json": meta,
        "created_at": now,
        "updated_at": now,
    }

    if db_mgr and db_mgr.session_factory:
        try:
            async with db_mgr.session(tenant_org_id=org_id) as session:
                # Ensure parent organization exists
                org = await session.get(OrganizationModel, org_id)
                if not org:
                    org = OrganizationModel(
                        id=org_id,
                        name=org_id.replace("_", " ").title(),
                        slug=org_id.replace("_", "-").lower(),
                    )
                    session.add(org)
                    await session.flush()

                # Ensure parent user exists to satisfy foreign key constraint
                user = await session.get(UserModel, user_id)
                if not user:
                    user_login = meta.get("login") or meta.get("username") or user_id
                    user_email = meta.get("email") or f"{user_id}@{org_id}.internal"
                    user = UserModel(
                        id=user_id,
                        org_id=org_id,
                        email=user_email,
                        name=user_login,
                        role=MEMBER,
                    )
                    session.add(user)
                    await session.flush()
                    await ensure_membership(session, user_id, org_id, MEMBER)

                stmt = select(UserCredentialModel).where(
                    UserCredentialModel.user_id == user_id,
                    UserCredentialModel.provider == provider,
                )
                res = await session.execute(stmt)
                record = res.scalar_one_or_none()
                if record:
                    record.org_id = org_id
                    record.encrypted_secret = encrypted
                    record.metadata_json = meta
                    record.updated_at = now
                else:
                    record = UserCredentialModel(
                        id=f"ucred_{uuid.uuid4().hex[:12]}",
                        user_id=user_id,
                        org_id=org_id,
                        provider=provider,
                        encrypted_secret=encrypted,
                        metadata_json=meta,
                        created_at=now,
                        updated_at=now,
                    )
                    session.add(record)
        except Exception as err:
            logger.warning(
                "Database write failed for user_credential %s/%s: %s", user_id, provider, err
            )

    return UserCredentialData(
        user_id=user_id,
        org_id=org_id,
        provider=provider,
        secret=secret,
        metadata=meta,
        created_at=now,
        updated_at=now,
    )


async def get_user_credential(
    db_mgr: DatabaseSessionManager | None,
    user_id: str,
    provider: str,
    org_id: str = "default_org",
) -> UserCredentialData | None:
    """Retrieve and decrypt user credential from DB or memory cache."""
    if db_mgr and db_mgr.session_factory:
        try:
            async with db_mgr.session(tenant_org_id=org_id) as session:
                stmt = select(UserCredentialModel).where(
                    UserCredentialModel.user_id == user_id,
                    UserCredentialModel.provider == provider,
                )
                res = await session.execute(stmt)
                record = res.scalar_one_or_none()
                if record:
                    decrypted = decrypt_secret(record.encrypted_secret)
                    return UserCredentialData(
                        user_id=record.user_id,
                        org_id=record.org_id,
                        provider=record.provider,
                        secret=decrypted,
                        metadata=record.metadata_json or {},
                        created_at=record.created_at,
                        updated_at=record.updated_at,
                    )
        except Exception as err:
            logger.debug("Database read failed for user_credential: %s", err)

    cached = _in_memory_credentials.get((org_id, user_id, provider))
    if cached:
        decrypted = decrypt_secret(cached["encrypted_secret"])
        return UserCredentialData(
            user_id=cached["user_id"],
            org_id=cached["org_id"],
            provider=cached["provider"],
            secret=decrypted,
            metadata=cached["metadata_json"],
            created_at=cached["created_at"],
            updated_at=cached["updated_at"],
        )

    return None


async def delete_user_credential(
    db_mgr: DatabaseSessionManager | None,
    user_id: str,
    provider: str,
    org_id: str = "default_org",
) -> bool:
    """Remove user credential from DB and in-memory cache."""
    removed = _in_memory_credentials.pop((org_id, user_id, provider), None) is not None

    if db_mgr and db_mgr.session_factory:
        try:
            async with db_mgr.session(tenant_org_id=org_id) as session:
                stmt = select(UserCredentialModel).where(
                    UserCredentialModel.user_id == user_id,
                    UserCredentialModel.provider == provider,
                )
                res = await session.execute(stmt)
                record = res.scalar_one_or_none()
                if record:
                    await session.delete(record)
                    removed = True
        except Exception as err:
            logger.warning(
                "Database delete failed for user_credential %s/%s: %s", user_id, provider, err
            )

    return removed

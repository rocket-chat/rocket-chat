"""Bring-your-own provider keys.

A model call uses the member's own key if they stored one, else the organization's, else the
platform's - as far as the organization's ``byok_policy`` allows. Keys are encrypted at rest;
only a fingerprint is ever shown or logged.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from typing import Any

import litellm
from config_engine.crypto import CredentialCipher
from sqlalchemy import select

from api.db.models import BYOKCredentialModel
from api.services.settings import effective_settings
from specifications.interfaces.llm import BYOKCredentials, ModelScope, ResolvedCredentials

KEY_CACHE_TTL_SECONDS = 10.0


def provider_of(model: str) -> str:
    """The LiteLLM provider a model id routes to (``openrouter/anthropic/x`` -> ``openrouter``)."""
    try:
        return str(litellm.get_llm_provider(model)[1])
    except Exception:
        return model.split("/", 1)[0] if "/" in model else "openai"


@dataclass(frozen=True)
class StoredKey:
    provider: str
    fingerprint: str
    api_base: str | None
    owner: str  # "org" or "user"
    created_at: int


class ModelCredentialStore:
    """Encrypted provider keys per member and per organization."""

    def __init__(self, db_mgr: Any, cipher: CredentialCipher | None = None) -> None:
        self._db = (
            db_mgr if db_mgr is not None and getattr(db_mgr, "session_factory", None) else None
        )
        self._cipher = cipher or CredentialCipher()
        # Development without a database: (org, user-or-None, provider) -> row-like dict.
        self._memory: dict[tuple[str, str | None, str], dict[str, Any]] = {}
        self._cache: dict[tuple[str, str | None, str], tuple[float, BYOKCredentials | None]] = {}

    async def put(
        self,
        org_id: str,
        user_id: str | None,
        provider: str,
        api_key: str,
        api_base: str | None = None,
    ) -> StoredKey:
        encrypted = self._cipher.encrypt(api_key)
        fingerprint = CredentialCipher.fingerprint(api_key)
        now = int(time.time())
        self._cache.pop((org_id, user_id, provider), None)
        if self._db is None:
            self._memory[(org_id, user_id, provider)] = {
                "encrypted": encrypted,
                "fingerprint": fingerprint,
                "api_base": api_base,
                "created_at": now,
            }
        else:
            async with self._db.session(tenant_org_id=org_id) as session:
                row = await self._row(session, org_id, user_id, provider)
                if row is None:
                    session.add(
                        BYOKCredentialModel(
                            id=f"byok_{uuid.uuid4().hex[:16]}",
                            org_id=org_id,
                            user_id=user_id,
                            provider=provider,
                            encrypted_api_key=encrypted,
                            key_fingerprint=fingerprint,
                            api_base=api_base,
                            created_at=now,
                        )
                    )
                else:
                    row.encrypted_api_key = encrypted
                    row.key_fingerprint = fingerprint
                    row.api_base = api_base
        return StoredKey(provider, fingerprint, api_base, "user" if user_id else "org", now)

    async def delete(self, org_id: str, user_id: str | None, provider: str) -> bool:
        self._cache.pop((org_id, user_id, provider), None)
        if self._db is None:
            return self._memory.pop((org_id, user_id, provider), None) is not None
        async with self._db.session(tenant_org_id=org_id) as session:
            row = await self._row(session, org_id, user_id, provider)
            if row is None:
                return False
            await session.delete(row)
            return True

    async def list(self, org_id: str, user_id: str | None) -> list[StoredKey]:
        owner = "user" if user_id else "org"
        if self._db is None:
            return sorted(
                (
                    StoredKey(p, v["fingerprint"], v["api_base"], owner, v["created_at"])
                    for (o, u, p), v in self._memory.items()
                    if o == org_id and u == user_id
                ),
                key=lambda key: key.provider,
            )
        async with self._db.session(tenant_org_id=org_id) as session:
            condition = (
                BYOKCredentialModel.user_id == user_id
                if user_id
                else BYOKCredentialModel.user_id.is_(None)
            )
            rows = await session.execute(
                select(BYOKCredentialModel)
                .where(BYOKCredentialModel.org_id == org_id, condition)
                .order_by(BYOKCredentialModel.provider)
            )
            return [
                StoredKey(r.provider, r.key_fingerprint, r.api_base, owner, r.created_at)
                for r in rows.scalars()
            ]

    async def credentials(
        self, org_id: str, user_id: str | None, provider: str
    ) -> BYOKCredentials | None:
        """The decrypted key, for the gateway only."""
        cache_key = (org_id, user_id, provider)
        cached = self._cache.get(cache_key)
        if cached and time.monotonic() - cached[0] < KEY_CACHE_TTL_SECONDS:
            return cached[1]
        credentials: BYOKCredentials | None = None
        if self._db is None:
            row = self._memory.get(cache_key)
            if row:
                credentials = BYOKCredentials(
                    provider=provider,
                    api_key=self._cipher.decrypt(row["encrypted"]),
                    api_base=row["api_base"],
                )
        else:
            async with self._db.session(tenant_org_id=org_id) as session:
                found = await self._row(session, org_id, user_id, provider)
                if found is not None:
                    credentials = BYOKCredentials(
                        provider=provider,
                        api_key=self._cipher.decrypt(found.encrypted_api_key),
                        api_base=found.api_base,
                    )
        self._cache[cache_key] = (time.monotonic(), credentials)
        return credentials

    @staticmethod
    async def _row(
        session: Any, org_id: str, user_id: str | None, provider: str
    ) -> BYOKCredentialModel | None:
        condition = (
            BYOKCredentialModel.user_id == user_id
            if user_id
            else BYOKCredentialModel.user_id.is_(None)
        )
        result = await session.execute(
            select(BYOKCredentialModel).where(
                BYOKCredentialModel.org_id == org_id,
                BYOKCredentialModel.provider == provider,
                condition,
            )
        )
        row: BYOKCredentialModel | None = result.scalars().first()
        return row


class CredentialResolver:
    """``CredentialResolverProtocol``: member key, else organization key, per policy."""

    def __init__(self, db_mgr: Any, store: ModelCredentialStore) -> None:
        self._db = db_mgr
        self._store = store

    async def resolve(self, scope: ModelScope, model: str) -> ResolvedCredentials | None:
        policy = str((await effective_settings(self._db, scope.org_id, "budgets"))["byok_policy"])
        if policy == "platform_only":
            return None
        provider = provider_of(model)
        if policy == "user_keys":
            own = await self._store.credentials(scope.org_id, scope.user_id, provider)
            if own is not None:
                return ResolvedCredentials(own, "user")
        shared = await self._store.credentials(scope.org_id, None, provider)
        return ResolvedCredentials(shared, "org") if shared is not None else None

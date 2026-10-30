"""Model provider credentials with an identity, and the rule that picks one for a call.

Each credential belongs to the organization or to one member, has a label, and may be the
default for its provider. An organization may hold several for one provider: a *restricted*
credential is skipped by the default chain and used only by the agents bound to it, so an
expensive key backs one task-specific agent and nothing else.

Resolution for one call (``CredentialResolver.resolve``):

1. The agent's binding for the model's provider wins (``key_source="agent"``). A binding to a
   credential that is gone, or that its owner no longer shares, fails the call ("credential
   withdrawn") instead of quietly billing another key.
2. With ``strict_binding``, a call on a provider the agent has no binding for is refused.
3. Otherwise the default chain, skipping restricted credentials, ordered by the
   organization's ``models.member_keys``: ``preferred`` (the member's, then the
   organization's), ``fallback`` (the organization's, then the member's) or ``off``. A member
   key is used only while the member's role holds ``bring_own_keys``.
4. Nothing found: the platform's key from the environment (``None``).

Secrets are sealed at rest; only a fingerprint is ever shown or logged.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace
from typing import Any, Literal

import litellm
from config_engine.crypto import CredentialCipher
from sqlalchemy import select

from api.db.models import ProviderCredentialModel
from api.services.settings import effective_settings
from specifications.interfaces.llm import (
    BYOKCredentials,
    CredentialRefusedError,
    ModelScope,
    ResolvedCredentials,
)

KEY_CACHE_TTL_SECONDS = 10.0

OwnerKind = Literal["org", "member"]
MEMBER_KEY_MODES = ("off", "fallback", "preferred")


def provider_of(model: str) -> str:
    """The LiteLLM provider a model id routes to (``openrouter/anthropic/x`` -> ``openrouter``)."""
    try:
        return str(litellm.get_llm_provider(model)[1])
    except Exception:
        return model.split("/", 1)[0] if "/" in model else "openai"


@dataclass(frozen=True)
class StoredCredential:
    """A credential as anyone but the gateway sees it: no secret, a fingerprint."""

    id: str
    org_id: str
    label: str
    owner_kind: OwnerKind
    owner_user_id: str | None
    provider: str
    kind: str
    fingerprint: str
    api_base: str | None
    default_for_provider: bool
    restricted_to_agents: bool
    shared_with_org_agents: tuple[str, ...]
    created_at: int
    updated_at: int

    @property
    def owner(self) -> Literal["org", "user"]:
        """``org`` or ``user`` (the name the cockpit has always shown)."""
        return "org" if self.owner_kind == "org" else "user"


@dataclass
class _Row:
    """The in-memory twin of a ``provider_credentials`` row (deployments without a database)."""

    stored: StoredCredential
    encrypted: str


@dataclass(frozen=True)
class CredentialChanges:
    """Fields an update may change; ``None`` leaves a field as it is."""

    label: str | None = None
    api_base: str | None = None
    default_for_provider: bool | None = None
    restricted_to_agents: bool | None = None
    shared_with_org_agents: tuple[str, ...] | None = None
    clear_api_base: bool = False


@dataclass(frozen=True)
class NewCredential:
    owner_kind: OwnerKind
    owner_user_id: str | None
    provider: str
    api_key: str
    label: str = ""
    api_base: str | None = None
    default_for_provider: bool = True
    restricted_to_agents: bool = False
    kind: str = "api_key"
    shared_with_org_agents: tuple[str, ...] = field(default_factory=tuple)


def _stored(row: ProviderCredentialModel) -> StoredCredential:
    return StoredCredential(
        id=row.id,
        org_id=row.org_id,
        label=row.label or "",
        owner_kind="member" if row.owner_kind == "member" else "org",
        owner_user_id=row.owner_user_id,
        provider=row.provider,
        kind=row.kind or "api_key",
        fingerprint=row.key_fingerprint,
        api_base=row.api_base,
        default_for_provider=bool(row.default_for_provider),
        restricted_to_agents=bool(row.restricted_to_agents),
        shared_with_org_agents=tuple(row.shared_with_org_agents or ()),
        created_at=row.created_at,
        updated_at=row.updated_at or row.created_at,
    )


def _apply(stored: StoredCredential, changes: CredentialChanges, now: int) -> StoredCredential:
    return replace(
        stored,
        label=changes.label if changes.label is not None else stored.label,
        api_base=None
        if changes.clear_api_base
        else (changes.api_base if changes.api_base is not None else stored.api_base),
        default_for_provider=stored.default_for_provider
        if changes.default_for_provider is None
        else changes.default_for_provider,
        restricted_to_agents=stored.restricted_to_agents
        if changes.restricted_to_agents is None
        else changes.restricted_to_agents,
        shared_with_org_agents=stored.shared_with_org_agents
        if changes.shared_with_org_agents is None
        else changes.shared_with_org_agents,
        updated_at=now,
    )


class ModelCredentialStore:
    """Sealed provider credentials of an organization and its members."""

    def __init__(self, db_mgr: Any, cipher: CredentialCipher | None = None) -> None:
        self._db = (
            db_mgr if db_mgr is not None and getattr(db_mgr, "session_factory", None) else None
        )
        self._cipher = cipher or CredentialCipher()
        self._memory: dict[str, _Row] = {}
        self._cache: dict[tuple[str, str], tuple[float, BYOKCredentials | None]] = {}

    async def create(self, org_id: str, new: NewCredential) -> StoredCredential:
        now = int(time.time())
        stored = StoredCredential(
            id=f"cred_{uuid.uuid4().hex[:16]}",
            org_id=org_id,
            label=new.label.strip() or new.provider,
            owner_kind=new.owner_kind,
            owner_user_id=new.owner_user_id if new.owner_kind == "member" else None,
            provider=new.provider,
            kind=new.kind,
            fingerprint=CredentialCipher.fingerprint(new.api_key),
            api_base=new.api_base,
            default_for_provider=new.default_for_provider and not new.restricted_to_agents,
            restricted_to_agents=new.restricted_to_agents,
            shared_with_org_agents=new.shared_with_org_agents,
            created_at=now,
            updated_at=now,
        )
        encrypted = self._cipher.encrypt(new.api_key)
        if stored.default_for_provider:
            await self._clear_default(org_id, stored)
        if self._db is None:
            self._memory[stored.id] = _Row(stored, encrypted)
            return stored
        async with self._db.session(tenant_org_id=org_id) as session:
            session.add(
                ProviderCredentialModel(
                    id=stored.id,
                    org_id=org_id,
                    label=stored.label,
                    owner_kind=stored.owner_kind,
                    owner_user_id=stored.owner_user_id,
                    provider=stored.provider,
                    kind=stored.kind,
                    encrypted_secret=encrypted,
                    key_fingerprint=stored.fingerprint,
                    api_base=stored.api_base,
                    default_for_provider=stored.default_for_provider,
                    restricted_to_agents=stored.restricted_to_agents,
                    shared_with_org_agents=list(stored.shared_with_org_agents),
                    created_at=now,
                    updated_at=now,
                )
            )
        return stored

    async def _clear_default(self, org_id: str, keep: StoredCredential) -> None:
        """One default per owner and provider: a new default replaces the previous one."""
        for other in await self.list(
            org_id, owner_user_id=keep.owner_user_id, owner_kind=keep.owner_kind
        ):
            if (
                other.provider == keep.provider
                and other.default_for_provider
                and other.id != keep.id
            ):
                await self.update(org_id, other.id, CredentialChanges(default_for_provider=False))

    async def get(self, org_id: str, credential_id: str) -> StoredCredential | None:
        if self._db is None:
            row = self._memory.get(credential_id)
            return row.stored if row and row.stored.org_id == org_id else None
        async with self._db.session(tenant_org_id=org_id) as session:
            found = await session.get(ProviderCredentialModel, credential_id)
            return _stored(found) if found is not None and found.org_id == org_id else None

    async def list(
        self,
        org_id: str,
        *,
        owner_user_id: str | None = None,
        owner_kind: OwnerKind | None = None,
    ) -> list[StoredCredential]:
        """Credentials of the organization (``owner_kind="org"``), of one member, or all."""

        def wanted(item: StoredCredential) -> bool:
            if owner_kind is not None and item.owner_kind != owner_kind:
                return False
            return owner_user_id is None or item.owner_user_id == owner_user_id

        if self._db is None:
            found = [r.stored for r in self._memory.values() if r.stored.org_id == org_id]
        else:
            async with self._db.session(tenant_org_id=org_id) as session:
                rows = await session.execute(
                    select(ProviderCredentialModel).where(ProviderCredentialModel.org_id == org_id)
                )
                found = [_stored(r) for r in rows.scalars()]
        return sorted(
            (item for item in found if wanted(item)), key=lambda c: (c.provider, c.label, c.id)
        )

    async def update(
        self, org_id: str, credential_id: str, changes: CredentialChanges
    ) -> StoredCredential | None:
        now = int(time.time())
        self._forget(org_id, credential_id)
        if self._db is None:
            row = self._memory.get(credential_id)
            if row is None or row.stored.org_id != org_id:
                return None
            row.stored = _apply(row.stored, changes, now)
            updated = row.stored
        else:
            async with self._db.session(tenant_org_id=org_id) as session:
                found = await session.get(ProviderCredentialModel, credential_id)
                if found is None or found.org_id != org_id:
                    return None
                updated = _apply(_stored(found), changes, now)
                found.label = updated.label
                found.api_base = updated.api_base
                found.default_for_provider = updated.default_for_provider
                found.restricted_to_agents = updated.restricted_to_agents
                found.shared_with_org_agents = list(updated.shared_with_org_agents)
                found.updated_at = now
        if changes.default_for_provider:
            await self._clear_default(org_id, updated)
        return updated

    async def rotate(
        self, org_id: str, credential_id: str, api_key: str
    ) -> StoredCredential | None:
        """Replace the secret, keeping the credential's identity and every binding to it."""
        now = int(time.time())
        encrypted = self._cipher.encrypt(api_key)
        fingerprint = CredentialCipher.fingerprint(api_key)
        self._forget(org_id, credential_id)
        if self._db is None:
            row = self._memory.get(credential_id)
            if row is None or row.stored.org_id != org_id:
                return None
            row.encrypted = encrypted
            row.stored = replace(row.stored, fingerprint=fingerprint, updated_at=now)
            return row.stored
        async with self._db.session(tenant_org_id=org_id) as session:
            found = await session.get(ProviderCredentialModel, credential_id)
            if found is None or found.org_id != org_id:
                return None
            found.encrypted_secret = encrypted
            found.key_fingerprint = fingerprint
            found.updated_at = now
            return _stored(found)

    async def delete(self, org_id: str, credential_id: str) -> StoredCredential | None:
        self._forget(org_id, credential_id)
        if self._db is None:
            row = self._memory.get(credential_id)
            if row is None or row.stored.org_id != org_id:
                return None
            del self._memory[credential_id]
            return row.stored
        async with self._db.session(tenant_org_id=org_id) as session:
            found = await session.get(ProviderCredentialModel, credential_id)
            if found is None or found.org_id != org_id:
                return None
            removed = _stored(found)
            await session.delete(found)
            return removed

    def _forget(self, org_id: str, credential_id: str) -> None:
        self._cache.pop((org_id, credential_id), None)

    async def secret(self, org_id: str, credential_id: str) -> BYOKCredentials | None:
        """The decrypted key, for the gateway only."""
        cache_key = (org_id, credential_id)
        cached = self._cache.get(cache_key)
        if cached and time.monotonic() - cached[0] < KEY_CACHE_TTL_SECONDS:
            return cached[1]
        credentials: BYOKCredentials | None = None
        if self._db is None:
            row = self._memory.get(credential_id)
            if row is not None and row.stored.org_id == org_id:
                credentials = BYOKCredentials(
                    provider=row.stored.provider,
                    api_key=self._cipher.decrypt(row.encrypted),
                    api_base=row.stored.api_base,
                )
        else:
            async with self._db.session(tenant_org_id=org_id) as session:
                found = await session.get(ProviderCredentialModel, credential_id)
                if found is not None and found.org_id == org_id:
                    credentials = BYOKCredentials(
                        provider=found.provider,
                        api_key=self._cipher.decrypt(found.encrypted_secret),
                        api_base=found.api_base,
                    )
        self._cache[cache_key] = (time.monotonic(), credentials)
        return credentials

    async def default_for(
        self, org_id: str, provider: str, *, owner_user_id: str | None
    ) -> StoredCredential | None:
        """The unrestricted credential the default chain uses for ``provider``: the owner's
        marked default, else their newest (``owner_user_id=None``: the organization's)."""
        kind: OwnerKind = "member" if owner_user_id else "org"
        candidates = [
            item
            for item in await self.list(org_id, owner_user_id=owner_user_id, owner_kind=kind)
            if item.provider == provider and not item.restricted_to_agents
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda c: (c.default_for_provider, c.created_at))


# Whether the member's role holds ``bring_own_keys`` now: (org_id, user_id) -> bool.
MemberMayUseKeys = Callable[[str, str], Awaitable[bool]]


class CredentialResolver:
    """``CredentialResolverProtocol``: the agent's binding, else the default chain."""

    def __init__(
        self,
        db_mgr: Any,
        store: ModelCredentialStore,
        member_may_use_keys: MemberMayUseKeys | None = None,
    ) -> None:
        self._db = db_mgr
        self._store = store
        self._member_may_use_keys = member_may_use_keys

    async def resolve(self, scope: ModelScope, model: str) -> ResolvedCredentials | None:
        provider = provider_of(model)
        models = await effective_settings(self._db, scope.org_id, "models")
        member_keys = str(models.get("member_keys") or "preferred")
        binding = next((b for b in scope.credential_bindings if b.provider == provider), None)
        if binding is not None:
            return await self._bound(scope, binding.credential_id, provider, member_keys)
        if scope.strict_binding:
            raise CredentialRefusedError(
                f"This agent may only call the providers it is bound to; it has no "
                f"credential for {provider}.",
                reason="unbound",
                provider=provider,
            )
        return await self._default_chain(scope, provider, member_keys)

    async def _bound(
        self, scope: ModelScope, credential_id: str, provider: str, member_keys: str
    ) -> ResolvedCredentials:
        stored = await self._store.get(scope.org_id, credential_id)
        if stored is not None and stored.owner_kind == "member":
            shared = scope.agent_id is not None and scope.agent_id in stored.shared_with_org_agents
            own = stored.owner_user_id == scope.member_id
            if member_keys == "off" or not (shared or own):
                stored = None
        secret = await self._store.secret(scope.org_id, credential_id) if stored else None
        if stored is None or secret is None:
            raise CredentialRefusedError(
                f"The {provider} credential bound to this agent was withdrawn; an "
                "administrator must bind another one.",
                reason="withdrawn",
                provider=provider,
            )
        return ResolvedCredentials(secret, "agent", stored.id)

    async def _member_key(self, scope: ModelScope, provider: str) -> ResolvedCredentials | None:
        if self._member_may_use_keys is not None and not await self._member_may_use_keys(
            scope.org_id, scope.member_id
        ):
            return None
        stored = await self._store.default_for(
            scope.org_id, provider, owner_user_id=scope.member_id
        )
        secret = await self._store.secret(scope.org_id, stored.id) if stored else None
        return ResolvedCredentials(secret, "user", stored.id) if stored and secret else None

    async def _org_key(self, scope: ModelScope, provider: str) -> ResolvedCredentials | None:
        stored = await self._store.default_for(scope.org_id, provider, owner_user_id=None)
        secret = await self._store.secret(scope.org_id, stored.id) if stored else None
        return ResolvedCredentials(secret, "org", stored.id) if stored and secret else None

    async def _default_chain(
        self, scope: ModelScope, provider: str, member_keys: str
    ) -> ResolvedCredentials | None:
        if member_keys == "preferred":
            return await self._member_key(scope, provider) or await self._org_key(scope, provider)
        if member_keys == "fallback":
            return await self._org_key(scope, provider) or await self._member_key(scope, provider)
        return await self._org_key(scope, provider)

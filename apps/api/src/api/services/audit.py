"""Governance services: audit trail, retention, model data controls and rate limits.

This module owns the server-side governance features:

* **audit trail.** One durable, RLS-scoped row per tool execution, approval
  request/decision, model call, credential change and settings mutation, so "who ran
  what, when, with what result" is answerable.
* **retention.** Per-org windows for sessions, traces, memories, attachments and
  audit rows, applied by a sweep the reconciliation loop can call.
* **model data governance.** Provider allowlist, zero-retention flag and region
  pinning, surfaced in settings and enforced against the effective model.
* **G4 — approval policy.** Which risk levels are auto-approved, denied, or forced to a
  human gate; enforced server-side at the approval event.
* **G6 — API rate limits.** Per-user and per-org token buckets bound the authenticated
  turn path the same way the Slack path is bounded.

The audit store mirrors ``TurnTraceStore``: a PostgreSQL repository (RLS-scoped) and a
bounded in-memory fallback so unit tests and database-less development still work.
"""

from __future__ import annotations

import base64
import json
import logging
import math
import time
import uuid
from collections import OrderedDict, deque
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import delete, select

from api.db.models import (
    AttachmentModel,
    AuditEventModel,
    MemoryModel,
    OrganizationModel,
    SessionModel,
    TurnTraceModel,
)
from api.db.session import DatabaseSessionManager

logger = logging.getLogger(__name__)

__all__ = [
    "APPROVAL_DECISIONS",
    "APPROVAL_RISK_LEVELS",
    "AUDIT_EVENT_TYPES",
    "MAX_AUDIT_EVENTS_IN_MEMORY",
    "ApprovalPolicy",
    "AuditEvent",
    "AuditEventStore",
    "AuditService",
    "EgressPolicy",
    "GovernancePolicy",
    "GovernanceRateLimiter",
    "ModelDataGovernance",
    "RateLimitDecision",
    "RetentionPolicy",
    "RetentionSweeper",
    "TokenBucketRateLimiter",
    "decode_audit_cursor",
    "encode_audit_cursor",
    "is_expired",
]

# Closed vocabulary: an unknown event type is a programming error, not user input, so it
# is validated rather than silently persisted.
AUDIT_EVENT_TYPES: tuple[str, ...] = (
    "tool_call",
    "approval_request",
    "approval_decision",
    "model_call",
    "credential_change",
    "settings_change",
    "login",
    "git_publish",
)
APPROVAL_DECISIONS: tuple[str, ...] = ("approved", "denied")
APPROVAL_RISK_LEVELS: tuple[str, ...] = ("low", "medium", "high", "critical")

# The in-memory fallback is bounded: development and tests never grow it
# without limit, and the oldest rows fall out first.
MAX_AUDIT_EVENTS_IN_MEMORY = 2000

# Retention windows use a closed set of keys so a typo cannot silently disable a purge.
_RETENTION_KEY_BY_TABLE: dict[str, str] = {
    "sessions": "session_retention_days",
    "turn_traces": "trace_retention_days",
    "memories": "memory_retention_days",
    "attachments": "attachment_retention_days",
    "audit_events": "audit_retention_days",
}


@dataclass(frozen=True)
class AuditEvent:
    """One durable governance record, decoupled from the ORM entity."""

    id: str
    org_id: str
    event_type: str
    actor_user_id: str | None = None
    session_id: str | None = None
    tool_name: str | None = None
    risk_level: str | None = None
    approved_by: str | None = None
    result_summary: str | None = None
    created_at: int = 0
    redacted: bool = False
    redaction_count: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ApprovalPolicy:
    """Which risk levels bypass, or are refused, a human gate."""

    auto_approve: tuple[str, ...] = ()
    deny: tuple[str, ...] = ()

    def decision_for(self, risk_level: str | None) -> str:
        """Return ``"auto_approve"``, ``"deny"`` or ``"require_human"``.

        Everything not named in the policy keeps today's behaviour: a human gate. That
        asymmetry is deliberate — the default must never silently approve a risky action.
        """
        level = (risk_level or "").strip().lower()
        if level in self.deny:
            return "deny"
        if level in self.auto_approve:
            return "auto_approve"
        return "require_human"

    @classmethod
    def from_settings(cls, settings: Mapping[str, Any] | None) -> ApprovalPolicy:
        source = settings or {}
        raw = source.get("approval_policy")
        policy = raw if isinstance(raw, Mapping) else {}
        return cls(
            auto_approve=_risk_levels(policy.get("auto_approve")),
            deny=_risk_levels(policy.get("deny")),
        )


@dataclass(frozen=True)
class ModelDataGovernance:
    """Org-level model data controls passed toward the gateway."""

    provider_allowlist: tuple[str, ...] = ()
    zero_retention: bool = False
    region: str = ""

    def provider_allowed(self, model: str) -> bool:
        """Whether a model's provider satisfies the allowlist (empty means all)."""
        candidate = (model or "").strip().lower()
        if not candidate:
            return False
        if not self.provider_allowlist:
            return True
        provider = candidate.split("/", 1)[0]
        return any(
            provider == pattern.lower() or candidate.startswith(pattern.lower())
            for pattern in self.provider_allowlist
        )

    @classmethod
    def from_settings(cls, settings: Mapping[str, Any] | None) -> ModelDataGovernance:
        source = settings or {}
        raw = source.get("provider_allowlist")
        allowlist: tuple[str, ...] = ()
        if isinstance(raw, str):
            allowlist = tuple(part.strip() for part in raw.split(",") if part.strip())
        elif isinstance(raw, Sequence):
            allowlist = tuple(str(item).strip() for item in raw if str(item).strip())
        return cls(
            provider_allowlist=allowlist,
            zero_retention=bool(source.get("zero_retention", False)),
            region=str(source.get("region") or "").strip(),
        )


@dataclass(frozen=True)
class EgressPolicy:
    """Per-org data-egress allowlist for agent sandboxes.

    The Helm chart already renders ``sandbox.networkPolicy.allowedEgressRules`` into the
    namespace NetworkPolicy; this carries the *organisation* allowlist and translates it
    into the same rule shape, so the sandbox driver can add per-session egress rules
    without the chart having to know tenant policy.
    """

    allowed_cidrs: tuple[str, ...] = ()
    block_internal_subnets: bool = False

    @classmethod
    def from_settings(cls, settings: Mapping[str, Any] | None) -> EgressPolicy:
        source = settings or {}
        raw = source.get("egress_allowlist")
        cidrs: tuple[str, ...] = ()
        if isinstance(raw, str):
            cidrs = tuple(part.strip() for part in raw.split(",") if part.strip())
        elif isinstance(raw, Sequence):
            cidrs = tuple(str(item).strip() for item in raw if str(item).strip())
        return cls(
            allowed_cidrs=cidrs,
            block_internal_subnets=bool(source.get("block_internal_subnets", False)),
        )

    def network_policy_egress_rules(self) -> list[dict[str, Any]]:
        """Translate the allowlist into Kubernetes NetworkPolicy egress rule dicts."""
        rules: list[dict[str, Any]] = [
            {"to": [{"ipBlock": {"cidr": cidr}}]} for cidr in self.allowed_cidrs
        ]
        if self.block_internal_subnets:
            rules.append(
                {
                    "to": [
                        {
                            "ipBlock": {
                                "cidr": "0.0.0.0/0",
                                "except": [
                                    "169.254.169.254/32",
                                    "10.0.0.0/8",
                                    "172.16.0.0/12",
                                    "192.168.0.0/16",
                                ],
                            }
                        }
                    ]
                }
            )
        return rules


@dataclass(frozen=True)
class RetentionPolicy:
    """Per-org retention windows, in days; ``None`` means "keep indefinitely"."""

    session_days: int | None = None
    trace_days: int | None = None
    memory_days: int | None = None
    attachment_days: int | None = None
    audit_days: int | None = None

    def cutoff(self, table: str, now: int) -> int | None:
        """Epoch-seconds cutoff before which rows in ``table`` are expired."""
        key = _RETENTION_KEY_BY_TABLE.get(table)
        if key is None:
            return None
        days = {
            "session_retention_days": self.session_days,
            "trace_retention_days": self.trace_days,
            "memory_retention_days": self.memory_days,
            "attachment_retention_days": self.attachment_days,
            "audit_retention_days": self.audit_days,
        }[key]
        if days is None or days <= 0:
            return None
        return now - days * 86400

    @classmethod
    def from_settings(cls, settings: Mapping[str, Any] | None) -> RetentionPolicy:
        source = settings or {}

        def _days(key: str) -> int | None:
            raw = source.get(key)
            if raw is None:
                return None
            try:
                value = int(raw)
            except (TypeError, ValueError):
                return None
            return value if value > 0 else None

        return cls(
            session_days=_days("session_retention_days"),
            trace_days=_days("trace_retention_days"),
            memory_days=_days("memory_retention_days"),
            attachment_days=_days("attachment_retention_days"),
            audit_days=_days("audit_retention_days"),
        )


def is_expired(created_at: int, cutoff: int | None) -> bool:
    """Whether a row created at ``created_at`` is older than the retention ``cutoff``."""
    return cutoff is not None and created_at < cutoff


def _risk_levels(raw: Any) -> tuple[str, ...]:
    if isinstance(raw, str):
        candidates: Sequence[Any] = [part.strip() for part in raw.split(",")]
    elif isinstance(raw, Sequence):
        candidates = raw
    else:
        return ()
    return tuple(str(item).strip().lower() for item in candidates if str(item).strip())


@dataclass(frozen=True)
class GovernancePolicy:
    """The aggregate of the per-org governance domains read from ``org_settings``."""

    approval: ApprovalPolicy = field(default_factory=ApprovalPolicy)
    model_data: ModelDataGovernance = field(default_factory=ModelDataGovernance)
    retention: RetentionPolicy = field(default_factory=RetentionPolicy)
    egress: EgressPolicy = field(default_factory=EgressPolicy)
    # ``None`` keeps a question open until someone answers it.
    question_timeout_seconds: int | None = None

    @classmethod
    def from_settings(cls, settings: Mapping[str, Any] | None) -> GovernancePolicy:
        timeout = (settings or {}).get("question_timeout_seconds")
        return cls(
            approval=ApprovalPolicy.from_settings(settings),
            model_data=ModelDataGovernance.from_settings(settings),
            retention=RetentionPolicy.from_settings(settings),
            egress=EgressPolicy.from_settings(settings),
            question_timeout_seconds=int(timeout) if timeout else None,
        )


class AuditEventStore:
    """PostgreSQL repository for durable audit events, scoped by tenant RLS."""

    def __init__(self, manager: DatabaseSessionManager) -> None:
        self.manager = manager

    @staticmethod
    def _to_model(event: AuditEvent) -> AuditEventModel:
        return AuditEventModel(
            id=event.id,
            org_id=event.org_id,
            actor_user_id=event.actor_user_id,
            session_id=event.session_id,
            event_type=event.event_type,
            tool_name=event.tool_name,
            risk_level=event.risk_level,
            approved_by=event.approved_by,
            result_summary=event.result_summary,
            created_at=event.created_at,
            redacted=event.redacted,
            redaction_count=event.redaction_count,
            metadata_json=event.metadata,
        )

    @staticmethod
    def _to_event(model: AuditEventModel) -> AuditEvent:
        return AuditEvent(
            id=model.id,
            org_id=model.org_id,
            event_type=model.event_type,
            actor_user_id=model.actor_user_id,
            session_id=model.session_id,
            tool_name=model.tool_name,
            risk_level=model.risk_level,
            approved_by=model.approved_by,
            result_summary=model.result_summary,
            created_at=model.created_at,
            redacted=bool(model.redacted),
            redaction_count=int(model.redaction_count) if model.redaction_count is not None else 0,
            metadata=dict(model.metadata_json or {}),
        )

    async def append(self, event: AuditEvent) -> None:
        async with self.manager.session(tenant_org_id=event.org_id) as session:
            session.add(self._to_model(event))

    async def list_page(
        self,
        org_id: str,
        *,
        limit: int,
        cursor: tuple[int, str] | None = None,
    ) -> list[AuditEvent]:
        """Return audit rows newest-first, strictly after ``cursor`` when given."""
        async with self.manager.session(tenant_org_id=org_id) as session:
            stmt = select(AuditEventModel).where(AuditEventModel.org_id == org_id)
            if cursor is not None:
                created_at, event_id = cursor
                # Keyset pagination over the (created_at, id) sort key: no OFFSET, so a
                # concurrent insert cannot make a page skip or repeat a row.
                stmt = stmt.where(
                    (AuditEventModel.created_at < created_at)
                    | ((AuditEventModel.created_at == created_at) & (AuditEventModel.id < event_id))
                )
            stmt = stmt.order_by(
                AuditEventModel.created_at.desc(), AuditEventModel.id.desc()
            ).limit(limit)
            result = await session.execute(stmt)
            return [self._to_event(model) for model in result.scalars().all()]


class AuditService:
    """Writes and reads the governance audit trail, with a bounded in-memory fallback."""

    def __init__(self, db_manager: DatabaseSessionManager | None = None) -> None:
        self._store = AuditEventStore(db_manager) if db_manager is not None else None
        self._memory: deque[AuditEvent] = deque(maxlen=MAX_AUDIT_EVENTS_IN_MEMORY)

    async def record(
        self,
        event_type: str,
        *,
        org_id: str,
        actor_user_id: str | None = None,
        session_id: str | None = None,
        tool_name: str | None = None,
        risk_level: str | None = None,
        approved_by: str | None = None,
        result_summary: str | None = None,
        redacted: bool = False,
        redaction_count: int = 0,
        metadata: Mapping[str, Any] | None = None,
        now: int | None = None,
    ) -> AuditEvent:
        if event_type not in AUDIT_EVENT_TYPES:
            raise ValueError(f"Unknown audit event type: {event_type!r}")
        event = AuditEvent(
            id=f"aud_{uuid.uuid4().hex[:16]}",
            org_id=org_id,
            event_type=event_type,
            actor_user_id=actor_user_id,
            session_id=session_id,
            tool_name=tool_name,
            risk_level=risk_level,
            approved_by=approved_by,
            result_summary=(result_summary or None),
            created_at=now if now is not None else int(time.time()),
            redacted=redacted,
            redaction_count=redaction_count,
            metadata=dict(metadata or {}),
        )
        if self._store is None:
            self._memory.append(event)
        else:
            # An audit write must never fail the turn it describes (invariant 10: the
            # failure stays visible in logs), because the turn's own result is the
            # primary artefact and a governance row is secondary evidence.
            try:
                await self._store.append(event)
            except Exception:
                logger.exception(
                    "Failed to persist audit event %s (%s) for org %s",
                    event.id,
                    event_type,
                    org_id,
                )
        return event

    async def list_page(
        self,
        org_id: str,
        *,
        limit: int,
        cursor: tuple[int, str] | None = None,
    ) -> list[AuditEvent]:
        if self._store is not None:
            return await self._store.list_page(org_id, limit=limit, cursor=cursor)
        ordered = sorted(
            (event for event in self._memory if event.org_id == org_id),
            key=lambda event: (event.created_at, event.id),
            reverse=True,
        )
        if cursor is not None:
            created_at, event_id = cursor
            ordered = [
                event
                for event in ordered
                if event.created_at < created_at
                or (event.created_at == created_at and event.id < event_id)
            ]
        return ordered[:limit]

    # -- convenience wrappers used by the route layer --------------------------

    async def record_tool_call(
        self,
        *,
        org_id: str,
        actor_user_id: str | None,
        session_id: str,
        tool_name: str,
        status: str,
        duration_ms: int = 0,
        redacted: bool = False,
        redaction_count: int = 0,
    ) -> AuditEvent:
        return await self.record(
            "tool_call",
            org_id=org_id,
            actor_user_id=actor_user_id,
            session_id=session_id,
            tool_name=tool_name,
            result_summary=f"{tool_name}: {status}",
            redacted=redacted,
            redaction_count=redaction_count,
            metadata={"duration_ms": duration_ms, "status": status},
        )

    async def record_git_publish(
        self,
        *,
        org_id: str,
        actor_user_id: str | None,
        session_id: str,
        tool_name: str,
        outcome: str,
    ) -> AuditEvent:
        """A branch left a sandbox: which tool, and the publication's own one-line summary."""
        return await self.record(
            "git_publish",
            org_id=org_id,
            actor_user_id=actor_user_id,
            session_id=session_id,
            tool_name=tool_name,
            result_summary=outcome[:500],
            metadata={"tool": tool_name},
        )

    async def record_model_call(
        self,
        *,
        org_id: str,
        actor_user_id: str | None,
        session_id: str,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
    ) -> AuditEvent:
        return await self.record(
            "model_call",
            org_id=org_id,
            actor_user_id=actor_user_id,
            session_id=session_id,
            tool_name=model,
            result_summary=(
                f"{model}: {prompt_tokens} prompt + {completion_tokens} completion tokens"
            ),
            metadata={"model": model},
        )

    async def record_approval_request(
        self,
        *,
        org_id: str,
        actor_user_id: str | None,
        session_id: str,
        action_type: str | None,
        risk_level: str | None,
        decision: str,
    ) -> AuditEvent:
        return await self.record(
            "approval_request",
            org_id=org_id,
            actor_user_id=actor_user_id,
            session_id=session_id,
            tool_name=action_type,
            risk_level=risk_level,
            result_summary=f"policy: {decision}",
            metadata={"decision": decision},
        )

    async def record_approval_decision(
        self,
        *,
        org_id: str,
        actor_user_id: str | None,
        session_id: str,
        action_type: str | None,
        risk_level: str | None,
        approved: bool,
        approved_by: str,
    ) -> AuditEvent:
        return await self.record(
            "approval_decision",
            org_id=org_id,
            actor_user_id=actor_user_id,
            session_id=session_id,
            tool_name=action_type,
            risk_level=risk_level,
            approved_by=approved_by,
            result_summary="approved" if approved else "denied",
        )


# ---------------------------------------------------------------------------
# Retention sweep
# ---------------------------------------------------------------------------

# Children before parents: FK cascades would remove them anyway, but deleting the leaf
# rows explicitly keeps the per-table counts meaningful for operators.
_SWEEP_TABLES: tuple[tuple[str, Any, str], ...] = (
    ("turn_traces", TurnTraceModel, "started_at"),
    ("memories", MemoryModel, "created_at"),
    ("attachments", AttachmentModel, "created_at"),
    ("audit_events", AuditEventModel, "created_at"),
    ("sessions", SessionModel, "updated_at"),
)


class RetentionSweeper:
    """Deletes tenant rows older than each org's configured retention windows.

    Runs on the administrative connection: RLS is FORCEd, so a cross-tenant purge must
    use the schema-owner role, exactly as migrations and the interrupted-turn recovery
    pass do. The reconciliation loop calls :meth:`sweep` periodically.
    """

    def __init__(
        self,
        manager: DatabaseSessionManager,
        policy_provider: Callable[[str], Awaitable[RetentionPolicy]] | None = None,
    ) -> None:
        self._manager = manager
        self._policy_provider = policy_provider

    async def _policy_for(self, org_id: str) -> RetentionPolicy:
        if self._policy_provider is None:
            return RetentionPolicy()
        return await self._policy_provider(org_id)

    async def sweep(self, now: int | None = None) -> dict[str, int]:
        """Purge expired rows for every organisation; return per-table delete counts."""
        effective_now = now if now is not None else int(time.time())
        counts: dict[str, int] = {}
        async with self._manager.session() as session:
            result = await session.execute(select(OrganizationModel.id))
            org_ids = [row[0] for row in result.all()]

        for org_id in org_ids:
            policy = await self._policy_for(org_id)
            async with self._manager.session(tenant_org_id=org_id) as session:
                for table, model, time_column in _SWEEP_TABLES:
                    cutoff = policy.cutoff(table, effective_now)
                    if cutoff is None:
                        continue
                    column = getattr(model, time_column)
                    # Explicit tenant filter: this connection is the schema owner, which row-level
                    # security does not constrain, so without it one organization's window would
                    # purge every organization's rows.
                    deleted = await session.execute(
                        delete(model).where(model.org_id == org_id, column < cutoff)
                    )
                    rowcount = int(getattr(deleted, "rowcount", 0) or 0)
                    counts[table] = counts.get(table, 0) + rowcount
        if counts:
            logger.info("Retention sweep deleted rows: %s", counts)
        return counts


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RateLimitDecision:
    """The outcome of one bucket check."""

    allowed: bool
    retry_after_seconds: int = 0
    remaining: int = 0


@dataclass
class _Bucket:
    tokens: float
    updated_at: float


class TokenBucketRateLimiter:
    """A bounded token bucket keyed by an opaque string.

    The registry keeps at most ``max_keys`` buckets, evicting the least-recently-used, so
    a caller cannot grow it without bound. ``clock`` is injectable so tests
    can advance time deterministically instead of sleeping.
    """

    def __init__(
        self,
        *,
        capacity: int,
        refill_per_second: float,
        max_keys: int = 1024,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.capacity = max(1, capacity)
        self.refill_per_second = max(0.0, refill_per_second)
        self.max_keys = max(1, max_keys)
        self._clock = clock or time.monotonic
        self._buckets: OrderedDict[str, _Bucket] = OrderedDict()

    def check(self, key: str, *, cost: float = 1.0) -> RateLimitDecision:
        now = self._clock()
        bucket = self._buckets.get(key)
        if bucket is None:
            bucket = _Bucket(tokens=float(self.capacity), updated_at=now)
        else:
            elapsed = max(0.0, now - bucket.updated_at)
            bucket.tokens = min(
                float(self.capacity), bucket.tokens + elapsed * self.refill_per_second
            )
            bucket.updated_at = now
            self._buckets.move_to_end(key)

        if bucket.tokens >= cost:
            bucket.tokens -= cost
            self._buckets[key] = bucket
            self._evict_overflow()
            return RateLimitDecision(True, remaining=int(bucket.tokens))

        if self.refill_per_second <= 0:
            retry_after = 0
        else:
            retry_after = max(1, math.ceil((cost - bucket.tokens) / self.refill_per_second))
        self._buckets[key] = bucket
        self._evict_overflow()
        return RateLimitDecision(False, retry_after_seconds=retry_after, remaining=0)

    def _evict_overflow(self) -> None:
        while len(self._buckets) > self.max_keys:
            self._buckets.popitem(last=False)


class GovernanceRateLimiter:
    """Per-user and per-org rate limits for the authenticated API path.

    Slack has its own per-``(team, user)``/``(team, channel)`` buckets; this
    bounds the authenticated turn path with the same token-bucket shape. Both checks must pass, and the denial names its scope so the caller can
    attribute the rejection in ``/metrics``.
    """

    def __init__(
        self,
        *,
        per_user_per_minute: int = 60,
        per_org_per_minute: int = 300,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._user = TokenBucketRateLimiter(
            capacity=per_user_per_minute,
            refill_per_second=per_user_per_minute / 60.0,
            clock=clock,
        )
        self._org = TokenBucketRateLimiter(
            capacity=per_org_per_minute,
            refill_per_second=per_org_per_minute / 60.0,
            clock=clock,
        )

    def check(self, *, org_id: str, user_id: str) -> tuple[RateLimitDecision, str]:
        """Return ``(decision, scope)`` where scope is ``api_org``/``api_user``/``""``."""
        org_decision = self._org.check(f"org:{org_id}")
        if not org_decision.allowed:
            return org_decision, "api_org"
        user_decision = self._user.check(f"user:{org_id}:{user_id}")
        if not user_decision.allowed:
            return user_decision, "api_user"
        return user_decision, ""


# ---------------------------------------------------------------------------
# Cursor encoding for the admin audit listing
# ---------------------------------------------------------------------------


def encode_audit_cursor(event: AuditEvent) -> str:
    """Encode the ``(created_at, id)`` keyset position as an opaque token."""
    payload = json.dumps({"created_at": event.created_at, "id": event.id}).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii")


def decode_audit_cursor(cursor: str) -> tuple[int, str]:
    """Decode a cursor produced by :func:`encode_audit_cursor`; raise on malformed input."""
    try:
        raw = base64.urlsafe_b64decode(cursor.encode("ascii"))
        data = json.loads(raw)
        return int(data["created_at"]), str(data["id"])
    except Exception as err:
        raise ValueError("Malformed audit cursor") from err

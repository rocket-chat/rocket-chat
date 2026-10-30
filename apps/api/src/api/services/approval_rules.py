"""Approvals a member remembered: "Allow for this session" and "Always for this command in this
repository".

A rule is saved from an approval the platform itself published (never from what a client says the
call was), and the tool registry reads the rules before it raises a gate (``covering_rule``), so
enforcement is the registry's, never the cockpit's. Organization controls win over every rule:
``governance.remembered_approvals`` chooses the scopes members may use (``off`` stops existing
rules), a locked permission mode disables them, the approval policy's denied and auto-approved
risk levels are never answered by a rule, and an irreversible (critical) call always waits.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from typing import Any

from agent_core.permissions import rememberable_command
from sqlalchemy import delete, select, update

from api.db.models import ApprovalRuleModel
from api.services.audit import ApprovalPolicy
from api.services.settings import effective_settings, resolve_settings
from specifications.interfaces.agent import ApprovalRule, RememberedApprovals
from specifications.interfaces.events import EventBusProtocol, EventType, SessionEvent

SESSION, REPOSITORY = "session", "repository"
# ``governance.remembered_approvals`` -> the scopes members may use.
_SCOPES_BY_SETTING: dict[str, tuple[str, ...]] = {
    "session_and_repository": (SESSION, REPOSITORY),
    "session": (SESSION,),
    "off": (),
}
# Risk levels a rule may ever answer; ``critical`` is not one of them.
_ANSWERABLE_RISKS = ("low", "medium", "high")

# Without a database (development and tests): by organization, then rule id.
_memory: dict[str, dict[str, StoredRule]] = {}


@dataclass(frozen=True)
class StoredRule:
    id: str
    org_id: str
    user_id: str
    scope: str
    tool: str
    command: str
    repository: str
    session_id: str | None
    created_at: int
    last_used_at: int | None = None

    def as_rule(self) -> ApprovalRule:
        return ApprovalRule(
            id=self.id,
            scope=self.scope,
            tool=self.tool,
            command=self.command,
            repository=self.repository,
        )


def _plain(row: ApprovalRuleModel) -> StoredRule:
    return StoredRule(
        id=row.id,
        org_id=row.org_id,
        user_id=row.user_id,
        scope=row.scope,
        tool=row.tool,
        command=row.command,
        repository=row.repository,
        session_id=row.session_id,
        created_at=row.created_at,
        last_used_at=row.last_used_at,
    )


def _has_db(db_mgr: Any) -> bool:
    return db_mgr is not None and bool(getattr(db_mgr, "session_factory", None))


class ApprovalRuleStore:
    """The ``approval_rules`` table (forced RLS), or process memory without a database."""

    def __init__(self, db_mgr: Any) -> None:
        self._db = db_mgr if _has_db(db_mgr) else None

    async def add(self, rule: StoredRule) -> StoredRule:
        """Save ``rule``, or return the identical one already saved."""
        for existing in await self._select(rule.org_id, user_id=None, session_id=None):
            if (
                existing.scope,
                existing.tool,
                existing.command,
                existing.repository,
                existing.session_id,
                existing.user_id if rule.scope == REPOSITORY else "",
            ) == (
                rule.scope,
                rule.tool,
                rule.command,
                rule.repository,
                rule.session_id,
                rule.user_id if rule.scope == REPOSITORY else "",
            ):
                return existing
        if self._db is None:
            _memory.setdefault(rule.org_id, {})[rule.id] = rule
            return rule
        async with self._db.session(tenant_org_id=rule.org_id) as session:
            session.add(
                ApprovalRuleModel(
                    id=rule.id,
                    org_id=rule.org_id,
                    user_id=rule.user_id,
                    scope=rule.scope,
                    tool=rule.tool,
                    command=rule.command,
                    repository=rule.repository,
                    session_id=rule.session_id,
                    created_at=rule.created_at,
                )
            )
        return rule

    async def _select(
        self, org_id: str, *, user_id: str | None, session_id: str | None
    ) -> list[StoredRule]:
        if self._db is None:
            rows: Iterable[StoredRule] = _memory.get(org_id, {}).values()
        else:
            async with self._db.session(tenant_org_id=org_id) as session:
                result = await session.execute(
                    select(ApprovalRuleModel).where(ApprovalRuleModel.org_id == org_id)
                )
                rows = [_plain(r) for r in result.scalars().all()]
        return sorted(
            (
                r
                for r in rows
                if (user_id is None or r.user_id == user_id)
                and (session_id is None or r.session_id == session_id)
            ),
            key=lambda r: (r.created_at, r.id),
        )

    async def for_session(self, org_id: str, session_id: str) -> list[StoredRule]:
        """The session's own rules (every participant's)."""
        return [
            r
            for r in await self._select(org_id, user_id=None, session_id=session_id)
            if r.scope == SESSION
        ]

    async def for_member(self, org_id: str, user_id: str) -> list[StoredRule]:
        """The member's repository rules."""
        return [
            r
            for r in await self._select(org_id, user_id=user_id, session_id=None)
            if r.scope == REPOSITORY
        ]

    async def for_turn(self, org_id: str, session_id: str, requester: str) -> list[StoredRule]:
        return [
            *await self.for_session(org_id, session_id),
            *await self.for_member(org_id, requester),
        ]

    async def get(self, org_id: str, rule_id: str) -> StoredRule | None:
        if self._db is None:
            return _memory.get(org_id, {}).get(rule_id)
        async with self._db.session(tenant_org_id=org_id) as session:
            row = await session.get(ApprovalRuleModel, rule_id)
            return _plain(row) if row is not None and row.org_id == org_id else None

    async def revoke(self, org_id: str, rule_id: str) -> bool:
        if self._db is None:
            return _memory.get(org_id, {}).pop(rule_id, None) is not None
        async with self._db.session(tenant_org_id=org_id) as session:
            result = await session.execute(
                delete(ApprovalRuleModel)
                .where(ApprovalRuleModel.id == rule_id, ApprovalRuleModel.org_id == org_id)
                .returning(ApprovalRuleModel.id)
            )
            return result.scalar_one_or_none() is not None

    async def touch(self, org_id: str, rule_id: str) -> None:
        now = int(time.time())
        if self._db is None:
            rules = _memory.get(org_id, {})
            if rule_id in rules:
                rules[rule_id] = replace(rules[rule_id], last_used_at=now)
            return
        async with self._db.session(tenant_org_id=org_id) as session:
            await session.execute(
                update(ApprovalRuleModel)
                .where(ApprovalRuleModel.id == rule_id)
                .values(last_used_at=now)
            )


async def allowed_scopes(db_mgr: Any, org_id: str) -> tuple[str, ...]:
    """The scopes members of the organization may remember approvals in (none: switched off)."""
    governance = await effective_settings(db_mgr, org_id, "governance")
    scopes = _SCOPES_BY_SETTING.get(str(governance.get("remembered_approvals")), ())
    if not scopes:
        return ()
    # A locked permission mode is the organization deciding how much is supervised; a member's
    # remembered "yes" would loosen it.
    preferences = await resolve_settings(db_mgr, org_id, "user_preferences")
    return () if "permission_mode" in preferences.locked_keys else scopes


def offered_options(
    scopes: tuple[str, ...], payload: Mapping[str, Any], policy_decision: str
) -> list[str]:
    """How the person may remember a "yes" to the approval ``payload`` describes."""
    if policy_decision != "require_human" or payload.get("risk_level") not in _ANSWERABLE_RISKS:
        return []
    tool = str(payload.get("tool") or "")
    if not tool:
        return []  # a request the agent made itself (request_approval) has no call to repeat
    options = [SESSION] if SESSION in scopes else []
    arguments = payload.get("arguments")
    command = rememberable_command(tool, arguments if isinstance(arguments, Mapping) else {})
    if REPOSITORY in scopes and payload.get("repository") and command is not None:
        options.append(REPOSITORY)
    return options


async def remembered_for_turn(
    db_mgr: Any, org_id: str, session_id: str, requester: str, policy: ApprovalPolicy
) -> RememberedApprovals:
    """The rules the registry may apply in this session's turn, as of now."""
    scopes = await allowed_scopes(db_mgr, org_id)
    if not scopes:
        return RememberedApprovals()
    rules = await ApprovalRuleStore(db_mgr).for_turn(org_id, session_id, requester)
    return RememberedApprovals(
        rules=tuple(r.as_rule() for r in rules if r.scope in scopes),
        risks=frozenset(
            level for level in _ANSWERABLE_RISKS if policy.decision_for(level) == "require_human"
        ),
    )


def rule_from_approval(
    payload: Mapping[str, Any], scope: str, org_id: str, session_id: str, user_id: str
) -> StoredRule | None:
    """The rule a "yes, remember it" to ``payload`` saves; ``None`` when the scope was not offered."""
    if scope not in payload.get("remember_options", []):
        return None
    tool = str(payload.get("tool") or "")
    arguments = payload.get("arguments")
    command = rememberable_command(tool, arguments if isinstance(arguments, Mapping) else {})
    return StoredRule(
        id=f"apr_{uuid.uuid4().hex[:12]}",
        org_id=org_id,
        user_id=user_id,
        scope=scope,
        tool=tool,
        command=(command or "") if scope == REPOSITORY else "",
        repository=str(payload.get("repository") or "") if scope == REPOSITORY else "",
        session_id=session_id if scope == SESSION else None,
        created_at=int(time.time()),
    )


def published_approval(
    event_bus: EventBusProtocol | None, session_id: str, action_id: str
) -> dict[str, Any] | None:
    """The approval request the platform published for ``action_id``, from the bus history.

    Read before the decision is submitted: the turn resumes at once, and what the rule is
    saved from must be what the person was shown, never what a client claims the call was.
    """
    if event_bus is None or not action_id:
        return None
    history, _ = event_bus.replay_since(session_id, 0)
    for event in reversed(history):
        if (
            event.event_type is EventType.APPROVAL_REQUIRED
            and event.payload.get("action_id") == action_id
        ):
            return dict(event.payload)
    return None


async def remember_answered_approval(
    *,
    db_mgr: Any,
    event_bus: EventBusProtocol | None,
    payload: Mapping[str, Any] | None,
    scope: str,
    org_id: str,
    session_id: str,
    user_id: str,
) -> StoredRule | None:
    """Save the rule a "yes, remember it" asks for, when the approval offered that scope and the
    organization still allows it; announce it to the session's participants."""
    if payload is None or scope not in await allowed_scopes(db_mgr, org_id):
        return None
    rule = rule_from_approval(payload, scope, org_id, session_id, user_id)
    if rule is None:
        return None
    saved = await ApprovalRuleStore(db_mgr).add(rule)
    await publish_rules_changed(event_bus, session_id, "added", saved)
    return saved


async def publish_rules_changed(
    event_bus: EventBusProtocol | None, session_id: str, change: str, rule: StoredRule
) -> None:
    if event_bus is None:
        return
    await event_bus.publish(
        SessionEvent(
            session_id=session_id,
            event_type=EventType.APPROVAL_RULES_CHANGED,
            payload={"change": change, "rule_id": rule.id, "scope": rule.scope},
        )
    )

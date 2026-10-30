"""The turn's view of the approvals people remembered (``TurnCallbacks.approval_rules``)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TypedDict

from api.services.approval_rules import ApprovalRuleStore, remembered_for_turn
from api.turns.context import TurnContext
from specifications.interfaces.agent import ApprovalRule, RememberedApprovals


class RememberedApprovalCallbacks(TypedDict):
    approval_rules: Callable[[str], Awaitable[RememberedApprovals]]
    approval_rule_used: Callable[[ApprovalRule, str, str], Awaitable[None]]


def remembered_approval_callbacks(ctx: TurnContext) -> RememberedApprovalCallbacks:
    """Read the session's and the requester's rules at each gate; audit every rule that answers."""
    org = ctx.session.tenant_org_id
    requester = ctx.binding.policy.attribution.requester_user_id or ctx.session.tenant_user_id

    async def approval_rules(session_id: str) -> RememberedApprovals:
        return await remembered_for_turn(
            ctx.req.db_mgr, org, session_id, requester, ctx.governance.approval
        )

    async def approval_rule_used(rule: ApprovalRule, tool: str, risk_level: str) -> None:
        await ApprovalRuleStore(ctx.req.db_mgr).touch(org, rule.id)
        await ctx.audit.record_approval_decision(
            org_id=org,
            actor_user_id=requester,
            session_id=ctx.session_id,
            action_type=tool,
            risk_level=risk_level,
            approved=True,
            approved_by=f"rule:{rule.id}",
        )

    return RememberedApprovalCallbacks(
        approval_rules=approval_rules, approval_rule_used=approval_rule_used
    )

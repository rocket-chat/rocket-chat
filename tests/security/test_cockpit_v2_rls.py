"""Background subagent runs, answer ratings and remembered approvals are each organization's
own, under forced Row-Level Security."""

from __future__ import annotations

import time

from api.db.models import AnswerFeedbackModel, ApprovalRuleModel, SessionModel, SubagentRunModel
from api.db.session import DatabaseSessionManager

from tests.support.rls import (
    TEST_DATABASE_URL,
    add_row,
    assert_cannot_write_into,
    assert_isolated,
    needs_postgres,
    new_id,
    two_orgs,
)


async def _session(db: DatabaseSessionManager, org: str) -> str:
    session_id = new_id("sess")
    await add_row(
        db,
        org,
        SessionModel(
            id=session_id, org_id=org, user_id=f"u_{org}", title="t", created_at=int(time.time())
        ),
    )
    return session_id


def _run(org: str, session_id: str) -> SubagentRunModel:
    now = int(time.time())
    return SubagentRunModel(
        id=new_id("run"), org_id=org, session_id=session_id, subagent_id=new_id("sub"),
        role="researcher", task="t", status="running", created_at=now, updated_at=now,
    )  # fmt: skip


def _feedback(org: str, session_id: str) -> AnswerFeedbackModel:
    now = int(time.time())
    return AnswerFeedbackModel(
        id=new_id("fb"), org_id=org, session_id=session_id, user_id=f"u_{org}", turn_number=1,
        rating="up", created_at=now, updated_at=now,
    )  # fmt: skip


def _rule(org: str, session_id: str) -> ApprovalRuleModel:
    return ApprovalRuleModel(
        id=new_id("rule"), org_id=org, user_id=f"u_{org}", scope="session",
        session_id=session_id, tool="bash_exec", created_at=int(time.time()),
    )  # fmt: skip


@needs_postgres
async def test_cockpit_v2_tables_are_isolated_in_postgres() -> None:
    db = DatabaseSessionManager(TEST_DATABASE_URL)
    try:
        org_a, org_b = await two_orgs(db)
        session_a = await _session(db, org_a)
        for make, model in (
            (_run, SubagentRunModel),
            (_feedback, AnswerFeedbackModel),
            (_rule, ApprovalRuleModel),
        ):
            await add_row(db, org_a, make(org_a, session_a))
            await assert_isolated(db, model, org_a, org_b)
            await assert_cannot_write_into(db, org_b, make(org_a, session_a))
    finally:
        await db.close()

"""A turn the platform refuses reads, in Slack, as what it is: a spend limit, or an agent the
person may not use. Neither is reported as an error, nor counted as a bot failure (P3.3)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
from api.models import AgentPersonaModel
from api.services.agent_access import reset_memory_grants, set_grants
from api.services.personas import save_persona
from slack_assistant.policy import SlackOrgPolicy
from test_slack_shared_pipeline import ScriptedModel, Stack

from specifications.interfaces.llm import BudgetExceededError, CallRefusal, StreamChunk

LIMIT = BudgetExceededError(
    "Spend limit reached (Slack workspace, per day): $3.00 of $3.00. An administrator can "
    "raise it.",
    scope="Slack workspace, per day",
    limit_usd=3.0,
    spent_usd=3.0,
    limit_id="lim_slack",
    subject="slack_workspace",
    period="day",
)


class RefusingModel(ScriptedModel):
    """The gateway's answer when a spend limit has no room left."""

    async def chat_stream(self, request: Any) -> AsyncIterator[StreamChunk]:
        self.requests.append(request)
        yield StreamChunk(
            is_finished=True,
            finish_reason="error",
            error=str(LIMIT),
            refusal=CallRefusal.from_error(LIMIT),
        )


@pytest.fixture(autouse=True)
def _no_grants() -> None:
    reset_memory_grants()


@pytest.mark.asyncio
async def test_a_spend_limit_is_posted_as_a_limit_not_an_error() -> None:
    stack = Stack(RefusingModel())

    await stack.send()

    posted = stack.posted()
    assert "Spend limit reached" in posted and "$3.00 of $3.00" in posted
    assert "encountered an error" not in posted
    streaks = stack.assistant._failures._streaks.values()
    assert all(s.consecutive_failures == 0 for s in streaks), "a refusal is not a bot failure"


@pytest.mark.asyncio
async def test_an_agent_the_requester_may_not_use_is_named_with_the_way_out() -> None:
    agent = AgentPersonaModel(
        id="agent-slack-pro",
        name="Pro Reviewer",
        role_title="x",
        system_prompt="PRO",
        scope="org",
    )
    saved = await save_persona(None, "default_org", "root", True, agent)
    await set_grants(None, "default_org", saved.id, [("member", "bob")], "root")
    model = ScriptedModel()
    stack = Stack(model, SlackOrgPolicy(default_agent_persona=saved.id))

    await stack.send()

    posted = stack.posted()
    assert "have access to Pro Reviewer" in posted and "Ask an administrator" in posted
    assert "encountered an error" not in posted
    assert not model.agent_requests(), "the turn never reached the model"

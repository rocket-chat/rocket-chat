"""A mission can report each run to a Slack channel its owner chose."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from api.main import app
from api.services.missions import MissionStore
from api.services.settings import save_org_config
from fastapi.testclient import TestClient
from slack_assistant.assistant import SlackAssistant
from slack_assistant.config import SlackAssistantSettings
from slack_assistant.policy import SlackOrgPolicy

HEADERS = {
    "X-Tenant-Org-Id": "test_org",
    "X-Tenant-User-Id": "mission_user",
    "X-User-Role": "admin",
}
BODY = {"name": "Nightly audit", "prompt": "audit it", "cron": "0 9 * * 1-5"}


@pytest.fixture
def client() -> Any:
    with TestClient(app) as c:
        app.state.db_manager = None
        app.state.mission_store = MissionStore(None)
        yield c


def test_a_mission_stores_its_channel_and_rejects_a_malformed_one(client: TestClient) -> None:
    ok = client.post("/v1/missions", json={**BODY, "slack_channel": "C0123ABCD"}, headers=HEADERS)
    assert ok.status_code == 200 and ok.json()["slack_channel"] == "C0123ABCD"
    bad = client.post("/v1/missions", json={**BODY, "slack_channel": "#general"}, headers=HEADERS)
    assert bad.status_code == 422


def test_the_channel_must_be_allowed_when_the_organization_restricts_posting(
    client: TestClient,
) -> None:
    async def restrict() -> None:
        await save_org_config(
            None, "restricted_org", "slack", {"outbound_channels": ["COK0001"]}, []
        )

    asyncio.run(restrict())
    headers = {**HEADERS, "X-Tenant-Org-Id": "restricted_org"}
    refused = client.post(
        "/v1/missions", json={**BODY, "slack_channel": "CNOPE001"}, headers=headers
    )
    allowed = client.post(
        "/v1/missions", json={**BODY, "slack_channel": "COK0001"}, headers=headers
    )
    assert refused.status_code == 422
    assert allowed.status_code == 200, allowed.text


def _assistant(policy: SlackOrgPolicy | None = None) -> SlackAssistant:
    async def resolve(_org: str) -> SlackOrgPolicy:
        return policy or SlackOrgPolicy(web_base_url="https://rocket.example")

    assistant = SlackAssistant(
        orchestrator=SimpleNamespace(),  # type: ignore[arg-type]
        settings=SlackAssistantSettings(),
        resolve_slack_policy_fn=resolve,
    )
    client = AsyncMock()
    client.chat_postMessage.return_value = {"ts": "5.5"}
    assistant.app = SimpleNamespace(client=client)  # type: ignore[assignment]
    assistant._slack_api_ready = True
    return assistant


async def test_a_finished_run_is_posted_with_a_link_to_its_session() -> None:
    assistant = _assistant()
    posted = await assistant.post_mission_report(
        org_id="o",
        channel_id="C1",
        mission_name="Nightly audit",
        session_id="session_abc",
        answer="3 findings, none critical.",
        succeeded=True,
    )
    assert posted
    sent = assistant.app.client.chat_postMessage.call_args.kwargs  # type: ignore[attr-defined]
    assert sent["channel"] == "C1" and "Nightly audit" in sent["text"]
    assert "3 findings" in str(sent["blocks"])
    assert "https://rocket.example/chat/session_abc" in str(sent["blocks"])


async def test_a_long_report_is_attached_in_full() -> None:
    assistant = _assistant()
    await assistant.post_mission_report(
        org_id="o",
        channel_id="C1",
        mission_name="Report",
        session_id="session_abc",
        answer="finding " * 1000,
        succeeded=True,
    )
    upload = assistant.app.client.files_upload_v2.call_args.kwargs  # type: ignore[attr-defined]
    assert upload["filename"] == "report.md" and upload["thread_ts"] == "5.5"


async def test_the_outbound_allowlist_is_enforced_at_post_time_too() -> None:
    assistant = _assistant(SlackOrgPolicy(outbound_channels=frozenset({"COK0001"})))
    posted = await assistant.post_mission_report(
        org_id="o",
        channel_id="COTHER001",
        mission_name="m",
        session_id="s",
        answer="x",
        succeeded=True,
    )
    assert posted is False
    assistant.app.client.chat_postMessage.assert_not_called()  # type: ignore[attr-defined]

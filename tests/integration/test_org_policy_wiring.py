"""Organization settings actually change runtime behaviour."""

from __future__ import annotations

from typing import Any

import pytest
from agent_core.tools.git_tools import commit_spec_for_policy, git_open_pr
from api.main import app
from api.services.settings import save_org_config, save_user_config
from api.turns.shared import _turn_service
from config_engine.cascading import image_is_allowed
from fastapi.testclient import TestClient

from specifications.interfaces.agent import GitPolicy
from specifications.interfaces.git import SigningMode

ORG = "policy_org"
ADMIN = {"X-Tenant-Org-Id": ORG, "X-Tenant-User-Id": "admin_user", "X-User-Role": "admin"}
MEMBER = {"X-Tenant-Org-Id": ORG, "X-Tenant-User-Id": "member_user", "X-User-Role": "developer"}


# --- git policy -> commit identity, trailers and PR credentials ------------------------


def _policy(**overrides: Any) -> GitPolicy:
    base = {
        "bot_name": "Bot",
        "bot_email": "bot@x.io",
        "user_name": "Alice",
        "user_email": "alice@x.io",
    }
    return GitPolicy(**{**base, **overrides})


def test_co_authored_commits_as_the_bot_and_credits_the_member() -> None:
    spec = commit_spec_for_policy("msg", None, None, _policy(authorship="co_authored"))
    assert (spec.committer_name, spec.committer_email) == ("Bot", "bot@x.io")
    assert [(a.name, a.email) for a in spec.co_authors] == [("Alice", "alice@x.io")]
    assert spec.branch == ""  # stay on the checked-out branch


def test_user_only_commits_as_the_member_without_trailers() -> None:
    spec = commit_spec_for_policy("msg", "feat/x", None, _policy(authorship="user_only"))
    assert (spec.committer_name, spec.committer_email) == ("Alice", "alice@x.io")
    assert spec.co_authors == [] and spec.branch == "feat/x"


def test_bot_only_commits_as_the_bot_without_trailers() -> None:
    spec = commit_spec_for_policy("msg", None, None, _policy(authorship="bot_only"))
    assert spec.committer_name == "Bot" and spec.co_authors == []


@pytest.mark.parametrize(
    ("mode", "expected"),
    [
        ("github_app", SigningMode.GITHUB_APP),
        ("gpg_key", SigningMode.GPG_KEY),
        ("none", SigningMode.NONE),
    ],
)
def test_signing_mode_follows_policy(mode: str, expected: SigningMode) -> None:
    assert (
        commit_spec_for_policy("m", None, None, _policy(signing_mode=mode)).signing_mode == expected
    )


class _Engine:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str | None]] = []

    async def push_and_open_pr(
        self,
        session_id: str,
        title: str,
        body: str,
        target_branch: str | None = None,
        token: str | None = None,
        installations: tuple[tuple[int, str], ...] = (),
        publish: Any = None,
    ) -> str:
        # The base is the repository's own default branch; the setting is only its fallback.
        self.calls.append((target_branch or publish.default_branch, token))
        return "https://github.com/o/r/pull/1"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("pr_creation", "expected_token"), [("as_user", "member-token"), ("as_bot", None)]
)
async def test_pr_credential_follows_policy(pr_creation: str, expected_token: str | None) -> None:
    engine = _Engine()
    policy = _policy(pr_creation=pr_creation, user_token="member-token", default_branch="develop")
    await git_open_pr(None, "s", "t", "b", git_engine=engine, policy=policy)  # type: ignore[arg-type]
    assert engine.calls == [("develop", expected_token)]


# --- turn plan: generation parameters, fail-over allowlist, git rules ------------------


@pytest.mark.asyncio
async def test_turn_plan_applies_models_and_github_settings() -> None:
    await save_org_config(
        None,
        ORG,
        "models",
        {
            "temperature": 0.7,
            "max_tokens": 1234,
            "fallback_models": ["ok/model-a", "banned/model-b"],
            "forbidden_models": ["banned/*"],
        },
        [],
    )
    await save_org_config(
        None,
        ORG,
        "github",
        {
            "commit_authorship_policy": "user_only",
            "default_branch": "develop",
            "bot_author_name": "Ops",
        },
        [],
    )
    await save_user_config(
        None,
        ORG,
        "u1",
        "user_preferences",
        {"git_author_name": "Alice", "git_author_email": "a@x.io"},
    )

    plan = await _turn_service.plan_turn(
        db_mgr=None, org_id=ORG, user_id="u1", agent_id=None, global_default_model="p/default"
    )
    generation, git = plan.policy.generation, plan.policy.git
    assert (generation.temperature, generation.max_tokens) == (0.7, 1234)
    assert generation.fallback_models == ("ok/model-a",)  # the forbidden one is dropped
    assert (git.authorship, git.default_branch, git.bot_name) == ("user_only", "develop", "Ops")
    assert (git.user_name, git.user_email) == ("Alice", "a@x.io")


@pytest.mark.asyncio
async def test_persona_overrides_generation_and_sandbox_settings() -> None:
    from api.models import AgentPersonaModel
    from api.services.personas import save_persona

    persona = AgentPersonaModel(
        id="p-tuned",
        name="Tuned",
        role_title="t",
        system_prompt="s",
        temperature=1.1,
        sandbox_image="node:20-slim",
        cpu_limit="1.5",
        memory_limit="2Gi",
    )
    await save_persona(None, ORG, "u1", True, persona)
    plan = await _turn_service.plan_turn(
        db_mgr=None, org_id=ORG, user_id="u1", agent_id=persona.id, global_default_model="p/d"
    )
    assert plan.policy.generation.temperature == 1.1
    assert (plan.sandbox_image, plan.cpu_limit, plan.memory_limit) == ("node:20-slim", "1.5", "2Gi")


def test_image_policy() -> None:
    assert image_is_allowed("python:3.12-slim", default_image="python:3.12-slim")
    assert not image_is_allowed("evil:latest", default_image="python:3.12-slim")
    assert image_is_allowed(
        "ghcr.io/acme/dev:1", default_image="x", allowed_images=["ghcr.io/acme/*"]
    )
    assert not image_is_allowed("", default_image="x")


# --- API-level policies -----------------------------------------------------------------


@pytest.fixture
def client() -> TestClient:
    from api.store import InMemorySessionStore

    app.state.session_store = InMemorySessionStore()
    return TestClient(app)


@pytest.mark.setting("general.session_privacy_default")
def test_session_privacy_default_applies_unless_chosen_explicitly(client: TestClient) -> None:
    client.put(
        "/v1/admin/settings/general",
        json={"config": {"session_privacy_default": "shared_org"}},
        headers=ADMIN,
    )
    inherited = client.post("/v1/sessions", json={"title": "a"}, headers=MEMBER).json()
    explicit = client.post(
        "/v1/sessions", json={"title": "b", "is_shared": False}, headers=MEMBER
    ).json()
    assert inherited["is_shared"] is True
    assert explicit["is_shared"] is False


def _server(server_id: str, transport: str) -> dict[str, Any]:
    return {
        "id": server_id,
        "name": server_id,
        "transport": transport,
        "endpoint_or_command": "https://mcp.example.com/mcp",
        "scope": "user",
        "tools": [{"name": "t", "description": "d"}],
    }


@pytest.mark.setting("mcp.user_allowed_transports")
@pytest.mark.setting("mcp.max_user_servers")
def test_mcp_policy_limits_transports_and_personal_server_count(client: TestClient) -> None:
    client.put(
        "/v1/admin/settings/mcp",
        json={"config": {"user_allowed_transports": ["http"], "max_user_servers": 1}},
        headers=ADMIN,
    )
    blocked = client.post("/v1/mcp/servers", json=_server("m1", "sse"), headers=MEMBER)
    assert blocked.status_code == 400 and "transport" in blocked.json()["detail"]

    assert (
        client.post("/v1/mcp/servers", json=_server("m1", "http"), headers=MEMBER).status_code
        == 200
    )
    # Updating the same server is not a new one...
    assert (
        client.post("/v1/mcp/servers", json=_server("m1", "http"), headers=MEMBER).status_code
        == 200
    )
    # ...but a second personal server exceeds the cap.
    over = client.post("/v1/mcp/servers", json=_server("m2", "http"), headers=MEMBER)
    assert over.status_code == 400 and "limit" in over.json()["detail"]
    client.delete("/v1/mcp/servers/m1", headers=MEMBER)


# --- Slack org policy -------------------------------------------------------------------


def test_slack_policy_channel_types_and_defaults() -> None:
    from slack_assistant.policy import SlackOrgPolicy

    dms_only = SlackOrgPolicy.from_settings({"allowed_channel_types": ["im"]})
    assert dms_only.allows_channel_type("im") and not dms_only.allows_channel_type("channel")
    assert not dms_only.allows_channel_type("group") and not dms_only.allows_channel_type("mpim")
    assert SlackOrgPolicy().allows_channel_type("channel")
    parsed = SlackOrgPolicy.from_settings(
        {"allowed_tools": "web_fetch, file_read", "outbound_channels": "C1,C2", "dm_policy": "none"}
    )
    assert parsed.allowed_tools == ("web_fetch", "file_read")
    assert parsed.outbound_channels == frozenset({"C1", "C2"}) and parsed.dm_policy == "none"


@pytest.mark.setting("mcp.allowed_transports")
def test_an_organization_server_must_use_a_transport_the_organization_allows(
    client: TestClient,
) -> None:
    client.put(
        "/v1/admin/settings/mcp",
        json={"config": {"allowed_transports": ["http"]}},
        headers=ADMIN,
    )

    blocked = client.post(
        "/v1/mcp/servers", json={**_server("o1", "sse"), "scope": "org"}, headers=ADMIN
    )
    allowed = client.post(
        "/v1/mcp/servers", json={**_server("o2", "http"), "scope": "org"}, headers=ADMIN
    )

    assert blocked.status_code == 400 and "transport" in blocked.json()["detail"]
    assert allowed.status_code == 200, allowed.text
    client.delete("/v1/mcp/servers/o2", headers=ADMIN)

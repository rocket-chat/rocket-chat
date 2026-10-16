"""Unit tests for multiple Slack bots, response modes, routing, and bot management API."""

from __future__ import annotations

import time
from collections.abc import Iterator
from dataclasses import replace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from api.main import app
from api.services.slack_bot_setup import check_tokens, manifest
from api.services.slack_bots import (
    MAX_BOTS_PER_ORG,
    SlackBotError,
    SlackBotRecord,
    SlackBotStore,
    SlackIdentity,
    SlackTokens,
    runtime_bot,
    slash_command_of,
    validate,
)
from api.services.slack_routing import (
    RouterClassification,
    list_route_decisions,
    record_route_decision,
    try_classify,
)
from fastapi.testclient import TestClient
from slack_assistant.bot import DISMISS, RouteDecision, SlackBot, SlackRoute, SlackRouter
from slack_assistant.state import SlackAssistantState

from specifications.interfaces.llm import LLMGatewayProtocol, current_model_scope


def _make_bot(
    bot_id: str = "b1",
    org_id: str = "org_1",
    name: str = "Support Bot",
    mode: str = "thread",
    router: dict | None = None,
) -> SlackBotRecord:
    now = int(time.time())
    return SlackBotRecord(
        id=bot_id,
        org_id=org_id,
        name=name,
        enabled=True,
        response_mode=mode,
        router=router,
        created_at=now,
        updated_at=now,
    )


# -- Store Tests -------------------------------------------------------------


@pytest.mark.asyncio
async def test_slack_bot_store_crud() -> None:
    store = SlackBotStore(None)
    tokens = SlackTokens("xoxb-valid-bot-token", "xapp-valid-app-token")
    record = _make_bot(bot_id="", name="Triage Bot")

    created = await store.create(record, tokens)
    assert created.id != ""
    assert created.name == "Triage Bot"
    assert created.bot_token_fingerprint is not None
    assert created.app_token_fingerprint is not None

    fetched = await store.get("org_1", created.id)
    assert fetched is not None
    assert fetched.id == created.id

    # Tokens are never stored on the record
    assert not hasattr(fetched, "bot_token")

    # Tokens can be retrieved for execution
    retrieved = await store.tokens("org_1", created.id)
    assert retrieved is not None
    assert retrieved.bot_token == "xoxb-valid-bot-token"
    assert retrieved.app_token == "xapp-valid-app-token"

    # Update
    updated = await store.update("org_1", created.id, {"name": "Renamed Bot", "enabled": False})
    assert updated is not None
    assert updated.name == "Renamed Bot"
    assert not updated.enabled

    # List
    listed = await store.list("org_1")
    assert len(listed) == 1
    assert listed[0].id == created.id

    # Cross-tenant list isolation
    assert len(await store.list("other_org")) == 0

    # Delete
    deleted = await store.delete("org_1", created.id)
    assert deleted is True
    assert await store.get("org_1", created.id) is None


@pytest.mark.asyncio
async def test_slack_bot_store_validation() -> None:
    store = SlackBotStore(None)
    # Empty name rejected
    with pytest.raises(SlackBotError, match="A bot needs a name"):
        await store.create(_make_bot(name="  "), None)

    # Invalid response mode rejected
    with pytest.raises(SlackBotError, match="Response mode must be one of"):
        await store.create(
            SlackBotRecord(id="", org_id="org_1", name="Bot", response_mode="invalid"), None
        )

    # Invalid router: no classifier agent
    with pytest.raises(SlackBotError, match="A router needs a classifier agent"):
        await store.create(_make_bot(name="Bot", router={"classifier_agent_id": ""}), None)

    # Invalid router: duplicate route names
    with pytest.raises(SlackBotError, match="unique name"):
        await store.create(
            _make_bot(
                name="Bot",
                router={
                    "classifier_agent_id": "persona-general",
                    "routes": [
                        {"name": "dev", "agent_id": "a1", "description": "code"},
                        {"name": "dev", "agent_id": "a2", "description": "code2"},
                    ],
                },
            ),
            None,
        )


@pytest.mark.asyncio
async def test_slack_bot_store_max_limit() -> None:
    store = SlackBotStore(None)
    for i in range(MAX_BOTS_PER_ORG):
        await store.create(_make_bot(bot_id="", name=f"Bot {i}"), None)

    with pytest.raises(SlackBotError, match="at most"):
        await store.create(_make_bot(bot_id="", name="Over Limit Bot"), None)


# -- Manifest & Token Check Tests --------------------------------------------


@pytest.mark.asyncio
async def test_check_tokens_validates_prefix() -> None:
    with pytest.raises(SlackBotError, match="bot token starts with xoxb-"):
        await check_tokens(SlackTokens("invalid-token", "xapp-valid"))

    with pytest.raises(SlackBotError, match="app-level token starts with xapp-"):
        await check_tokens(SlackTokens("xoxb-valid", "invalid-token"))


def test_manifest_generation() -> None:
    record = _make_bot(name="Code Assistant")
    record.listen_channels = ["C_ALL"]
    generated = manifest(record, web_url="https://rocket.example.com")

    assert generated["display_information"]["name"] == "Code Assistant"
    assert "oauth_config" in generated
    assert "channels:history" in generated["oauth_config"]["scopes"]["bot"]
    # Listens without mentions: includes message.channels event
    events = generated["settings"]["event_subscriptions"]["bot_events"]
    assert "message.channels" in events
    assert "message.groups" in events


# -- Routing & Classification Tests ------------------------------------------


def test_slack_router_resolution() -> None:
    routes = (
        SlackRoute(name="dev", description="Coding questions", agent_id="agent-dev"),
        SlackRoute(name="ops", description="Deployment & infra", agent_id="agent-ops"),
    )
    router = SlackRouter(
        classifier_agent_id="persona-general",
        routes=routes,
        allow_dismiss=True,
        min_confidence=0.6,
        fallback="dev",
    )

    # High confidence match
    assert router.resolve(RouteDecision("ops", 0.8, "infra task")) == "ops"

    # Below confidence threshold: takes fallback
    assert router.resolve(RouteDecision("ops", 0.4, "not sure")) == "dev"

    # Dismiss when allowed
    assert router.resolve(RouteDecision(DISMISS, 0.9, "casual chat")) == DISMISS


@pytest.mark.asyncio
async def test_try_classify() -> None:
    bot = SlackBot(
        id="bot-1",
        org_id="org_1",
        name="Router Bot",
        bot_token="",
        app_token="",
        router=SlackRouter(
            classifier_agent_id="persona-general",
            routes=(SlackRoute(name="code", description="Coding", agent_id="a-code"),),
            allow_dismiss=True,
        ),
    )

    mock_gateway = MagicMock(spec=LLMGatewayProtocol)
    billed: list[object] = []

    async def classify(*_: object) -> RouterClassification:
        billed.append(current_model_scope.get())
        return RouterClassification(route="code", confidence=0.85, reason="User asks about Python")

    with patch("api.services.slack_routing.generate_structured", classify):
        result = await try_classify(
            mock_gateway,
            bot,
            "org_1",
            "How do I fix this bug?",
            default_model="openrouter/openai/gpt-4o-mini",
        )
    assert result["route"] == "code"
    assert result["applied"] == "code"
    assert result["confidence"] == 0.85
    assert result["model"] == "openrouter/openai/gpt-4o-mini", "the organization's default"
    scope = billed[0]
    assert scope is not None and scope.org_id == "org_1", "billed to the bot's organization"
    assert current_model_scope.get() is None, "the scope does not leak past the call"


@pytest.mark.asyncio
async def test_decision_recording() -> None:
    await record_route_decision(
        None,
        org_id="org_1",
        bot_id="b1",
        channel_id="C1",
        thread_ts="123.456",
        slack_user_id="U1",
        excerpt="context",
        route="code",
        applied="code",
        confidence=0.9,
        reason="Python code",
        model="gpt-4o",
        latency_ms=120,
    )

    decisions = await list_route_decisions(None, "org_1", "b1")
    assert len(decisions) >= 1
    latest = decisions[0]
    assert latest["route"] == "code"
    assert latest["confidence"] == 0.9
    assert latest["latency_ms"] == 120


# -- Session ID & Multi-Bot Isolation Tests ----------------------------------


def test_session_ids_per_bot_and_mode() -> None:
    state1 = SlackAssistantState()
    state1.bot = SlackBot("bot-alpha", "org_1", "Alpha", "x", "y")

    state2 = SlackAssistantState()
    state2.bot = SlackBot("bot-beta", "org_1", "Beta", "x", "y")

    sid1 = state1.get_session_id("C01", "1700000000.123")
    sid2 = state2.get_session_id("C01", "1700000000.123")

    # Two bots in the same thread get separate sessions
    assert sid1 != sid2
    assert "botalph" in sid1
    assert "botbeta" in sid2

    # Private mode adds requester suffix
    priv = state1.get_session_id("C01", "1700000000.123", private_for="UUSER1")
    assert priv != sid1
    assert priv.endswith("_UUSER1")


# -- API Endpoint Tests ------------------------------------------------------


@pytest.fixture
def api_client() -> Iterator[TestClient]:
    # The lifespan gives this test its own stores (and database manager, on PostgreSQL).
    with TestClient(app) as client:
        yield client


def _auth_cookie(client: TestClient, role: str = "admin", org: str = "test_org") -> None:
    # Tenant headers (trusted in tests, like the proxy in production): a session cookie's
    # roles are re-read from the members table, where this test has no member.
    client.headers.update(
        {"X-Tenant-Org-Id": org, "X-Tenant-User-Id": "test_admin", "X-User-Role": role}
    )


def test_slack_bots_admin_endpoints(api_client: TestClient) -> None:
    # 1. Non-admin is rejected
    _auth_cookie(api_client, role="developer", org="test_org")
    res = api_client.get("/v1/admin/slack/bots")
    assert res.status_code == 403

    # 2. Admin is allowed
    _auth_cookie(api_client, role="admin", org="test_org")
    res = api_client.get("/v1/admin/slack/bots")
    assert res.status_code == 200
    assert "Dev Assistant" not in [b["name"] for b in res.json()]

    # 3. Create bot (without connecting it to Slack)
    with (
        patch("api.routers.slack_bots._apply_to_running_bots", AsyncMock()),
        patch(
            "api.routers.slack_bots.check_tokens",
            checked := AsyncMock(
                return_value=SlackIdentity(
                    team_id="T_TEST",
                    team_name="Test Workspace",
                    bot_user_id="U_BOT",
                    app_id="A_APP",
                    handle="rocket-bot",
                )
            ),
        ),
    ):
        create_res = api_client.post(
            "/v1/admin/slack/bots",
            json={
                "name": "Dev Assistant",
                "bot_token": "xoxb-valid-token",
                "app_token": "xapp-valid-token",
                "response_mode": "private",
            },
        )
        assert create_res.status_code == 201
        data = create_res.json()
        bot_id = data["id"]
        assert data["name"] == "Dev Assistant"
        assert data["handle"] == "rocket-bot"
        assert data["response_mode"] == "private"
        assert "bot_token" not in data

        # 4. Get bot
        get_res = api_client.get(f"/v1/admin/slack/bots/{bot_id}")
        assert get_res.status_code == 200
        assert get_res.json()["id"] == bot_id

        # 5. Manifest
        manifest_res = api_client.get(f"/v1/admin/slack/bots/{bot_id}/manifest")
        assert manifest_res.status_code == 200
        assert "display_information" in manifest_res.json()

        # 6. Test connection, after the Slack app was renamed: the bot follows its new name
        checked.return_value = replace(checked.return_value, handle="Rocky")
        test_res = api_client.post(f"/v1/admin/slack/bots/{bot_id}/test")
        assert test_res.status_code == 200
        assert test_res.json()["ok"] is True
        assert test_res.json()["handle"] == "Rocky"
        refreshed = api_client.get(f"/v1/admin/slack/bots/{bot_id}").json()
        assert (refreshed["handle"], refreshed["command"]) == ("Rocky", "/rocky")

        chosen = api_client.put(f"/v1/admin/slack/bots/{bot_id}", json={"slash_command": "/ask"})
        assert chosen.json()["command"] == "/ask"
        refused = api_client.put(f"/v1/admin/slack/bots/{bot_id}", json={"slash_command": "ask"})
        assert refused.status_code == 400

        # 7. Update bot
        up_res = api_client.put(
            f"/v1/admin/slack/bots/{bot_id}",
            json={"name": "Updated Bot", "response_mode": "thread"},
        )
        assert up_res.status_code == 200
        assert up_res.json()["name"] == "Updated Bot"
        assert up_res.json()["response_mode"] == "thread"

        # 8. Delete bot
        del_res = api_client.delete(f"/v1/admin/slack/bots/{bot_id}")
        assert del_res.status_code == 200
        assert del_res.json()["deleted"] is True

        # 9. Not found after delete
        assert api_client.get(f"/v1/admin/slack/bots/{bot_id}").status_code == 404


# -- System One Classifier Tests ---------------------------------------------


def test_systemone_helpers() -> None:
    from api.services.systemone import is_systemone_model, normalize_systemone_model

    assert is_systemone_model("typesafe/jev-latest") is True
    assert is_systemone_model("openrouter/typesafe/jev-1.14") is True
    assert is_systemone_model("openrouter/inception/mercury-decide") is True
    assert is_systemone_model("openai/gpt-4o") is False
    assert is_systemone_model("anthropic/claude-3.5-sonnet") is False

    assert normalize_systemone_model("openrouter/typesafe/jev-latest") == "typesafe/jev-latest"
    assert normalize_systemone_model("typesafe/jev-latest") == "typesafe/jev-latest"


def test_systemone_payload_and_parse() -> None:
    from api.services.systemone import build_systemone_payload, parse_systemone_response

    payload = build_systemone_payload(
        model="openrouter/typesafe/jev-latest",
        message="Please fix this auth bug",
        thread_excerpt="User reported 401 error",
        question_key="route",
        instructions="Route the request",
        criteria={"bug": "Software defects", "dismiss": "Chit chat"},
    )
    assert payload["model"] == "typesafe/jev-latest"
    assert payload["state"]["user_message"] == "Please fix this auth bug"
    assert payload["state"]["thread_context"] == "User reported 401 error"
    assert payload["questions"]["route"]["type"] == "choice"

    mock_resp = {
        "model": "typesafe/jev-latest",
        "answers": {
            "route": {
                "type": "choice",
                "choice": "bug",
                "confidence": 0.94,
                "probabilities": {"bug": 0.94, "dismiss": 0.06},
            }
        },
        "usage": {"input_tokens": 35, "output_tokens": 0},
    }
    result = parse_systemone_response(mock_resp, "route")
    assert result.choice == "bug"
    assert result.confidence == 0.94
    assert result.probabilities["bug"] == 0.94
    assert result.input_tokens == 35


@pytest.mark.asyncio
async def test_systemone_try_classify_dispatch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-or-key")

    mock_resp_data = {
        "model": "typesafe/jev-latest",
        "answers": {
            "route": {
                "type": "choice",
                "choice": "dev_bug",
                "confidence": 0.91,
                "probabilities": {"dev_bug": 0.91, "dismiss": 0.09},
            }
        },
        "usage": {"input_tokens": 28, "output_tokens": 0},
    }

    mock_resp = MagicMock()
    mock_resp.json.return_value = mock_resp_data
    mock_resp.raise_for_status.return_value = None

    router = SlackRouter(
        classifier_agent_id="classifier_1",
        classifier_model="openrouter/typesafe/jev-latest",
        routes=(
            SlackRoute(
                name="dev_bug",
                description="Code bug fixes",
                agent_id="ag_dev",
            ),
        ),
        allow_dismiss=True,
    )
    bot = SlackBot(
        id="b_sys1",
        org_id="org_test",
        name="Router Bot",
        bot_token="xoxb-test",
        app_token="xapp-test",
        router=router,
    )

    gateway = MagicMock(spec=LLMGatewayProtocol)

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_resp
        res = await try_classify(
            gateway,
            bot,
            "org_test",
            "Fix typo in auth.py",
        )
        assert res["route"] == "dev_bug"
        assert res["applied"] == "dev_bug"
        assert res["confidence"] == 0.91
        assert res["probabilities"]["dev_bug"] == 0.91
        assert "System One" in res["reason"]
        mock_post.assert_awaited_once()


@pytest.mark.asyncio
async def test_systemone_fallback_to_structured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-or-key")

    router = SlackRouter(
        classifier_agent_id="classifier_1",
        classifier_model="openrouter/typesafe/jev-latest",
        routes=(
            SlackRoute(
                name="dev_bug",
                description="Code bug fixes",
                agent_id="ag_dev",
            ),
        ),
    )
    bot = SlackBot(
        id="b_sys1",
        org_id="org_test",
        name="Router Bot",
        bot_token="xoxb-test",
        app_token="xapp-test",
        router=router,
    )

    gateway = MagicMock(spec=LLMGatewayProtocol)

    with (
        patch("httpx.AsyncClient.post", side_effect=Exception("Network error")),
        patch(
            "api.services.slack_routing.generate_structured",
            new_callable=AsyncMock,
        ) as mock_struct,
    ):
        mock_struct.return_value = RouterClassification(
            route="dev_bug", confidence=0.88, reason="Fallback to structured"
        )
        res = await try_classify(
            gateway,
            bot,
            "org_test",
            "Fix typo in auth.py",
        )
        assert res["route"] == "dev_bug"
        assert res["confidence"] == 0.88
        assert res["reason"] == "Fallback to structured"
        mock_struct.assert_awaited_once()


async def test_the_handle_is_the_name_people_see_not_the_original_username() -> None:
    client = MagicMock()
    client.auth_test = AsyncMock(
        return_value={"team_id": "T1", "team": "Acme", "user_id": "UBOT", "user": "dify"}
    )
    client.bots_info = AsyncMock(return_value={"bot": {"app_id": "A1"}})
    client.apps_connections_open = AsyncMock(return_value={"ok": True})
    client.users_info = AsyncMock(
        return_value={"user": {"profile": {"display_name": "Rocky", "real_name": "Rocky"}}}
    )
    with patch("api.services.slack_bot_setup.AsyncWebClient", return_value=client):
        identity = await check_tokens(SlackTokens("xoxb-1", "xapp-1"))
        client.users_info.side_effect = RuntimeError("missing_scope")
        fallback = await check_tokens(SlackTokens("xoxb-1", "xapp-1"))

    assert identity.handle == "Rocky"
    assert fallback.handle == "dify", "without the profile, the username"


def test_a_bot_can_choose_its_slash_command() -> None:
    record = SlackBotRecord(id="b1", org_id="o", name="Rocky", handle="Rocky")
    assert slash_command_of(record) == "/rocky"
    chosen = validate(replace(record, slash_command=" /Ask-Rocky "))
    assert chosen.slash_command == "/ask-rocky"
    assert slash_command_of(chosen) == "/ask-rocky"
    assert runtime_bot(chosen, SlackTokens("x", "y")).command == "/ask-rocky"
    commands = manifest(chosen)["features"]["slash_commands"]
    assert [c["command"] for c in commands] == ["/ask-rocky"], "one command, pairing included"
    for bad in ("rocky", "/", "/has space", "/" + "x" * 40):
        with pytest.raises(SlackBotError):
            validate(replace(record, slash_command=bad))

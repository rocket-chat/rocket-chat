"""A member who cannot open a Slack guest's direct message asks for a pairing link.

The conversation is not made public: the link goes to the Slack user, in their DM with the
bot that holds it, and whoever confirms it links their own account to that Slack user.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any, ClassVar

import pytest
from api.auth.session_manager import SESSION_COOKIE_NAME, create_session_token
from api.main import app
from api.routers import slack_admin
from api.services import slack_pairing_request
from api.services.slack_bots import SlackTokens
from api.services.slack_link import PairingCodes, pairing_link, slack_guest_id
from fastapi.testclient import TestClient

from specifications.interfaces.session import SessionRecord, SessionSource

ORG = "org_a"
GUEST = slack_guest_id("T1", "U1")
DM_SESSION = "slack_bot1_D1_1.0"


class _Store:
    def __init__(self, *records: SessionRecord) -> None:
        self._records = {r.session_id: r for r in records}

    async def get_session(self, session_id: str) -> SessionRecord | None:
        return self._records.get(session_id)


class _Bots:
    async def tokens(self, org_id: str, bot_id: str) -> SlackTokens | None:
        return SlackTokens(bot_token=f"xoxb-{bot_id}", app_token="xapp") if org_id == ORG else None


class _Slack:
    """Records the Web API calls; one instance per client the code creates."""

    posts: ClassVar[list[dict[str, Any]]] = []

    def __init__(self, token: str, timeout: int) -> None:
        self.token = token

    async def chat_postMessage(self, **kwargs: Any) -> dict[str, Any]:
        self.posts.append({"token": self.token, **kwargs})
        return {"ok": True}


def _record(
    session_id: str = DM_SESSION, owner: str = GUEST, org: str = ORG, **metadata: Any
) -> SessionRecord:
    return SessionRecord(
        session_id=session_id,
        tenant_org_id=org,
        tenant_user_id=owner,
        title="Vérifier remboursements",
        source=SessionSource.SLACK,
        metadata={
            "slack_is_dm": True,
            "slack_bot_id": "bot1",
            "slack_channel_id": "D1",
            **metadata,
        },
    )


@pytest.fixture
def stack(monkeypatch: pytest.MonkeyPatch) -> Iterator[PairingCodes]:
    monkeypatch.delenv("DEV_AUTH_BYPASS", raising=False)
    monkeypatch.delenv("ROCKET_TRUST_PROXY_HEADERS", raising=False)
    monkeypatch.setenv("ROCKET_PUBLIC_URL", "https://rocket.example.com")
    codes = PairingCodes()

    async def guest_of(_db: Any, _org: str, member_id: str) -> tuple[str, str] | None:
        return ("T1", "U1") if member_id == GUEST else None

    _Slack.posts = []
    monkeypatch.setattr(slack_pairing_request, "slack_guest_of", guest_of)
    monkeypatch.setattr(slack_pairing_request, "AsyncWebClient", _Slack)
    monkeypatch.setattr(app.state, "db_manager", None, raising=False)
    monkeypatch.setattr(app.state, "slack_pairing_codes", codes, raising=False)
    monkeypatch.setattr(app.state, "slack_bot_store", _Bots(), raising=False)
    monkeypatch.setattr(
        app.state,
        "session_store",
        _Store(
            _record(),
            _record("slack_bot1_C9_2.0", slack_is_dm=False, link_access=True),
            _record("other_org_session", org="org_b"),
            _record("web_session", owner="bertille"),
        ),
        raising=False,
    )
    yield codes


def _client(user: str = "bertille", roles: tuple[str, ...] = ("developer",)) -> TestClient:
    client = TestClient(app)
    client.cookies.set(
        SESSION_COOKIE_NAME, create_session_token(user_id=user, org_id=ORG, roles=list(roles))
    )
    return client


def test_context_says_linking_opens_a_guests_dm_for_someone_else(stack: PairingCodes) -> None:
    ask = _client().get
    assert ask("/v1/slack/pairing-context", params={"session_id": DM_SESSION}).json() == {
        "pairing_required": True,
        "slack_user_id": "U1",
    }
    # Nothing to link for a shared thread (readable from its link), another organization's
    # session, a session the caller owns, or one that does not exist.
    for session_id in ("slack_bot1_C9_2.0", "other_org_session", "missing"):
        assert not ask("/v1/slack/pairing-context", params={"session_id": session_id}).json()[
            "pairing_required"
        ]
    owner = _client(GUEST).get("/v1/slack/pairing-context", params={"session_id": DM_SESSION})
    assert owner.json()["pairing_required"] is False


def test_the_link_goes_to_the_slack_user_through_the_sessions_own_bot(stack: PairingCodes) -> None:
    sent = _client().post("/v1/slack/pairing-requests", json={"session_id": DM_SESSION})

    assert sent.status_code == 202 and sent.json() == {"status": "sent", "slack_user_id": "U1"}
    (post,) = _Slack.posts
    assert post["token"] == "xoxb-bot1" and post["channel"] == "D1"
    link = next(w for w in post["text"].split("<")[1:] if w.startswith("https://")).split("|")[0]
    code = link.split("code=")[1].split("&")[0]
    assert link == pairing_link("https://rocket.example.com", code, DM_SESSION)
    # The code is the Slack user's, so confirming it links that Slack user.
    assert code in post["text"] and "A colleague" in post["text"]


@pytest.mark.asyncio
async def test_the_code_in_the_dm_belongs_to_the_slack_user(stack: PairingCodes) -> None:
    _client().post("/v1/slack/pairing-requests", json={"session_id": DM_SESSION})
    code = _Slack.posts[0]["text"].split("code=")[1].split("&")[0]

    assert await stack.peek(code) == ("U1", "T1")
    assert await stack.peek(code) == ("U1", "T1"), "previewing must not consume the code"
    preview = _client().get("/v1/slack/pair/preview", params={"code": code})
    assert preview.json() == {"slack_user_id": "U1", "team_id": "T1"}


def test_asking_again_within_the_cooldown_sends_nothing(stack: PairingCodes) -> None:
    client = _client()
    assert (
        client.post("/v1/slack/pairing-requests", json={"session_id": DM_SESSION}).status_code
        == 202
    )
    again = client.post("/v1/slack/pairing-requests", json={"session_id": DM_SESSION})

    assert again.status_code == 429
    assert len(_Slack.posts) == 1


def test_only_members_who_can_link_and_eligible_sessions_are_served(stack: PairingCodes) -> None:
    viewer = _client(roles=("viewer",)).post(
        "/v1/slack/pairing-requests", json={"session_id": DM_SESSION}
    )
    assert viewer.status_code == 403
    for session_id in ("slack_bot1_C9_2.0", "other_org_session", "web_session"):
        refused = _client().post("/v1/slack/pairing-requests", json={"session_id": session_id})
        assert refused.status_code == 404
    assert _Slack.posts == []


def test_a_bot_that_no_longer_exists_is_reported(
    stack: PairingCodes, monkeypatch: pytest.MonkeyPatch
) -> None:
    class _NoBots:
        async def tokens(self, org_id: str, bot_id: str) -> None:
            return None

    monkeypatch.setattr(app.state, "slack_bot_store", _NoBots(), raising=False)
    gone = _client().post("/v1/slack/pairing-requests", json={"session_id": DM_SESSION})
    assert gone.status_code == 409 and _Slack.posts == []


def test_preview_rejects_a_dead_code_and_anonymous_callers(stack: PairingCodes) -> None:
    assert _client().get("/v1/slack/pair/preview", params={"code": "ZZZZ2222"}).status_code == 400
    assert (
        TestClient(app).get("/v1/slack/pair/preview", params={"code": "ZZZZ2222"}).status_code
        == 401
    )


@pytest.mark.asyncio
async def test_an_account_linked_to_another_slack_user_keeps_the_code(
    stack: PairingCodes, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def other(*_: Any) -> str:
        return "U_SOMEONE_ELSE"

    monkeypatch.setattr(slack_admin, "linked_slack_user", other)
    code = await stack.begin("U1", "T1")

    refused = _client().post("/v1/slack/pair", json={"code": code})

    assert refused.status_code == 409 and "another Slack user" in refused.json()["detail"]
    assert await stack.peek(code) == ("U1", "T1"), "a refusal must not cost the person their link"


@pytest.mark.asyncio
async def test_codes_can_be_peeked_and_throttled() -> None:
    now = [1000.0]
    codes = PairingCodes(ttl_seconds=600, clock=lambda: now[0])
    assert not await codes.issued_recently("U1")
    code = await codes.begin("U1", "T1")
    assert await codes.issued_recently("U1") and not await codes.issued_recently("U2")
    now[0] += 61
    assert not await codes.issued_recently("U1"), "the cooldown ends before the code expires"
    assert await codes.peek(code) == ("U1", "T1")
    now[0] += 600
    assert await codes.peek(code) is None


def test_pairing_link_carries_the_code_and_the_session_to_open() -> None:
    assert pairing_link("https://r.io/", "ABCD2345") == "https://r.io/link-slack?code=ABCD2345"
    assert pairing_link("https://r.io", "ABCD2345", "slack_b_D1_1.0/x").endswith(
        "&session=slack_b_D1_1.0%2Fx"
    )

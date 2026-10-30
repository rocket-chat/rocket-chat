"""Sessions bound to a chat thread: readable by the organization from their link, listed for
those who take part, sharing that survives the database, authors by name, readable titles."""

from __future__ import annotations

from typing import Any

import pytest
from api.auth.access import _check_session_access
from api.db.store import _with_sharing
from api.routers.session_participants import guest_platform
from api.services.title import generate_session_title, placeholder_title
from fastapi import HTTPException
from fastapi.testclient import TestClient
from slack_assistant.assistant import _Admitted, _new_session_record, _participants
from slack_assistant.policy import SlackOrgPolicy

from specifications.interfaces.llm import ChatMessage
from specifications.interfaces.session import SessionRecord, SessionSource

MEMBER = {"X-Tenant-Org-Id": "thread_org", "X-Tenant-User-Id": "grace", "X-User-Role": "member"}


def _record(**metadata: Any) -> SessionRecord:
    return SessionRecord(
        session_id="slack_C1_1_0", tenant_org_id="thread_org", tenant_user_id="slack:T1",
        title="t", source=SessionSource.SLACK, metadata=dict(metadata),
    )  # fmt: skip


def test_a_shared_record_keeps_its_sharing_in_the_database() -> None:
    record = _record()
    record.is_shared, record.collaborators = True, ["a", "a", "b"]
    stored = _with_sharing(record)
    assert stored["is_shared"] is True and stored["collaborators"] == ["a", "b"]


def test_a_thread_session_is_readable_from_its_link_but_not_writable() -> None:
    record = _record(link_access=True)
    _check_session_access(record, "someone", ["member"])
    with pytest.raises(HTTPException):
        _check_session_access(record, "someone", ["member"], require_write=True)
    with pytest.raises(HTTPException):
        _check_session_access(_record(), "someone", ["member"])
    record.collaborators = ["someone"]
    _check_session_access(record, "someone", ["member"], require_write=True)


def _admitted(**overrides: Any) -> _Admitted:
    from dataclasses import replace

    base = _Admitted(
        prompt="p", title_text="fix it", team_scope="T1", is_dm=False, session_user_id="slack:T1",
        session_org_id="thread_org", rocket_user_id="grace", org_policy=SlackOrgPolicy(),
        workspace_org="thread_org", rocket_org_id="thread_org", linked=True,
    )  # fmt: skip
    return replace(base, **overrides)


def test_a_linked_member_who_writes_in_a_public_thread_becomes_a_collaborator() -> None:
    record = _new_session_record("s", _admitted(), "C1", "1.0", "U1")
    assert record.metadata["link_access"] is True and not record.is_shared
    assert record.collaborators == ["grace"]
    assert _participants(_admitted(linked=False)) == []
    assert _participants(_admitted(rocket_org_id="other")) == []
    dm = _new_session_record("s", _admitted(is_dm=True, session_user_id="grace"), "D1", "1.0", "U1")
    assert dm.metadata["link_access"] is False and dm.collaborators == []


def test_a_thread_session_is_in_the_list_of_participants_only(client: TestClient) -> None:
    store = client.app.state.session_store  # type: ignore[attr-defined]
    import asyncio

    record = _record(link_access=True)
    record.session_id = "slack_C9_9_9"
    asyncio.run(store.create_session(record))
    listed = {s["session_id"] for s in client.get("/v1/sessions", headers=MEMBER).json()}
    assert record.session_id not in listed
    assert client.get(f"/v1/sessions/{record.session_id}", headers=MEMBER).status_code == 200

    def join(r: SessionRecord) -> None:
        r.collaborators = ["grace"]
        r.metadata = {**r.metadata, "collaborators": ["grace"]}

    asyncio.run(store.mutate_session(record.session_id, "thread_org", join))
    listed = {s["session_id"] for s in client.get("/v1/sessions", headers=MEMBER).json()}
    assert record.session_id in listed


def test_participants_name_members_and_mark_guests(client: TestClient) -> None:
    import asyncio

    record = _record(link_access=True)
    record.session_id = "slack_C8_8_8"
    record.conversation_history = [
        ChatMessage(role="user", content="[Speaker: Ada] hi", author_user_id="slack:T1:U1"),
        ChatMessage(role="user", content="hello", author_user_id="grace"),
    ]
    asyncio.run(client.app.state.session_store.create_session(record))  # type: ignore[attr-defined]
    body = client.get(f"/v1/sessions/{record.session_id}/participants", headers=MEMBER).json()
    by_id = {p["user_id"]: p for p in body["participants"]}
    assert by_id["slack:T1:U1"]["guest_of"] == "slack" and by_id["grace"]["guest_of"] is None
    assert guest_platform("teams:t:u") == "teams" and guest_platform("ada") is None


@pytest.mark.parametrize(
    "prompt, title",
    [
        (
            "[Speaker: Ada (<@U1>)] <@U9> can you fix the flaky login test in CI? It fails.",
            "Can you fix the flaky login test",
        ),
        ("Look at https://x.example/a and explain", "Look at and explain"),
        ("```py\nx = 1\n``` why is this slow", "Why is this slow"),
        ("/review", None),
    ],
)
def test_a_placeholder_title_is_the_first_sentence_without_noise(
    prompt: str, title: str | None
) -> None:
    assert placeholder_title(prompt) == title


async def test_the_ai_title_reads_the_latest_request_too(monkeypatch: pytest.MonkeyPatch) -> None:
    import api.services.title as title_module

    captured: dict[str, Any] = {}

    async def structured(gateway: Any, request: Any, schema: Any) -> Any:
        captured["request"] = request
        return schema(title="Add rate limits to export API")

    monkeypatch.setattr(title_module, "generate_structured", structured)
    conversation = [
        ChatMessage(role="user", content="[Speaker: Ada] hello"),
        ChatMessage(role="assistant", content="Hi, what do you need?"),
        ChatMessage(role="user", content="[Speaker: Ada] add rate limits to the export API"),
    ]
    title = await generate_session_title(gateway=object(), model="m", conversation=conversation)  # type: ignore[arg-type]
    sent = captured["request"].messages[1].content
    assert title == "Add rate limits to export API"
    assert "Latest request:\nadd rate limits to the export API" in sent and "[Speaker" not in sent

"""Slack users who are not linked, and shared threads, are owned by guest members.

Production never invents users, so a session owned by a raw Slack id failed to save and the
turn runner used to hand it to a development identity instead. The Slack bridge now
provisions a guest per Slack user (and one per workspace for shared threads); pairing gives
the guest's sessions to the member.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from typing import Any

import pytest
from api.db.models import SessionModel, UserModel
from api.services.slack_link import (
    GUEST_ROLE,
    adopt_guest_sessions,
    ensure_slack_guest,
    find_member,
    slack_guest_id,
)
from api.services.title import generate_session_title

from specifications.interfaces.llm import ChatMessage
from tests.integration.test_slack_shared_pipeline import THREAD, ScriptedModel, Stack


def _with_guests(stack: Stack, calls: list[tuple[str, str, str | None]]) -> None:
    async def guest(org: str, team: str, slack_user: str | None) -> str:
        calls.append((org, team, slack_user))
        return slack_guest_id(team, slack_user)

    stack.assistant.guest_user_fn = guest


async def _dm(stack: Stack, text: str) -> None:
    await asyncio.wait_for(
        stack.assistant.process_incoming_request(
            channel_id="D1", thread_ts="5.0", raw_text=text, say_fn=stack.say,
            user_id="U1", channel_type="im", team_id="T1",
        ),
        5,
    )  # fmt: skip


async def test_an_unlinked_slack_user_owns_their_dm_through_a_guest() -> None:
    stack, calls = Stack(ScriptedModel()), []
    _with_guests(stack, calls)

    await _dm(stack, "Tu peux regarder les logs beta ?")

    record = await stack.store.get_session(stack.assistant.get_session_id("D1", "5.0"))
    assert record is not None and record.tenant_user_id == "slack:T1:U1"
    assert record.title.startswith("Slack DM: Tu peux") and "Speaker" not in record.title
    assert record.metadata["title_source"] == "auto", "the AI titler may name it"
    assert calls and calls[0][2] == "U1"


async def test_a_shared_thread_is_owned_by_the_workspace_guest() -> None:
    stack, calls = Stack(ScriptedModel()), []
    _with_guests(stack, calls)

    await asyncio.wait_for(stack.send(), 5)

    record = await stack.store.get_session(stack.assistant.get_session_id(*THREAD))
    assert record is not None and record.tenant_user_id == "slack:T1"


async def test_a_linked_member_gets_no_guest() -> None:
    stack, calls = Stack(ScriptedModel()), []
    _with_guests(stack, calls)

    async def linked(slack_user: str, email: str | None, org: str) -> tuple[str, str]:
        return "member_alice", org

    stack.assistant.resolve_user_fn = linked
    await _dm(stack, "hello")

    record = await stack.store.get_session(stack.assistant.get_session_id("D1", "5.0"))
    assert record is not None and record.tenant_user_id == "member_alice"
    assert calls == []


async def test_titles_ignore_the_speaker_attribution() -> None:
    seen: list[Any] = []

    class Gateway:
        async def chat_stream(self, request: Any) -> Any:
            seen.append(request)
            from specifications.interfaces.llm import StreamChunk

            yield StreamChunk(text_delta='{"title": "Beta rental start logs"}')

    await generate_session_title(
        gateway=Gateway(),  # type: ignore[arg-type]
        model="m",
        conversation=[ChatMessage(role="user", content="[Speaker: @U1 (<@U1>)] logs beta")],
    )
    assert "Speaker" not in seen[0].messages[-1].content


requires_db = pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="needs PostgreSQL")


@pytest.fixture
async def db_manager(tenant_rows: None) -> Any:
    from api.db.session import DatabaseSessionManager

    manager = DatabaseSessionManager(os.environ["TEST_DATABASE_URL"])
    yield manager
    await manager.close()


@requires_db
async def test_guests_are_created_once_and_never_mistaken_for_a_pairing(db_manager: Any) -> None:
    slack_user = f"U{uuid.uuid4().hex[:8].upper()}"

    first, second = await asyncio.gather(
        ensure_slack_guest(db_manager, "test_org", "T1", slack_user),
        ensure_slack_guest(db_manager, "test_org", "T1", slack_user),
    )

    assert first == second == f"slack:T1:{slack_user}"
    async with db_manager.session(tenant_org_id="test_org") as session:
        guest = await session.get(UserModel, first)
        assert guest is not None and guest.role == GUEST_ROLE and guest.password_hash is None
    assert await find_member(db_manager, slack_user, "test_org") is None, "not a linked member"


@requires_db
async def test_pairing_moves_the_guests_sessions_to_the_member(db_manager: Any) -> None:
    slack_user = f"U{uuid.uuid4().hex[:8].upper()}"
    guest = await ensure_slack_guest(db_manager, "test_org", "T1", slack_user)
    session_id = f"s_{slack_user}"
    async with db_manager.session(tenant_org_id="test_org") as session:
        session.add(SessionModel(id=session_id, org_id="test_org", user_id=guest, title="t"))

    moved = await adopt_guest_sessions(db_manager, "test_org", "T1", slack_user, "dev_user")

    assert moved == 1
    async with db_manager.session(tenant_org_id="test_org") as session:
        row = await session.get(SessionModel, session_id)
        assert row is not None and row.user_id == "dev_user"

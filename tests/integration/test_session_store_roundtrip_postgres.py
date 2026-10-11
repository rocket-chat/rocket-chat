"""What the cockpit shows must survive the database: the PostgreSQL store keeps every field."""

from __future__ import annotations

import os
import uuid

import pytest
from api.db.session import DatabaseSessionManager
from api.db.store import PostgresSessionStore

from specifications.interfaces.llm import ChatMessage
from specifications.interfaces.session import SessionRecord

pytestmark = pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="needs PostgreSQL")


@pytest.mark.asyncio
async def test_reasoning_and_tool_calls_survive_a_save_and_reload(tenant_rows: None) -> None:
    manager = DatabaseSessionManager(os.environ["TEST_DATABASE_URL"])
    store = PostgresSessionStore(manager)
    session_id = f"rt_{uuid.uuid4().hex[:8]}"
    tool_calls = [
        {"id": "call-1", "name": "bash_exec", "arguments": {"command": "ls"}, "result": "ok"}
    ]
    await store.create_session(
        SessionRecord(
            session_id=session_id,
            tenant_org_id="test_org",
            tenant_user_id="member_user",
            title="round trip",
            conversation_history=[
                ChatMessage(role="user", content="analyze", author_user_id="teammate_ana"),
                ChatMessage(
                    role="assistant",
                    content="done",
                    reasoning="Plan: look first, then act.",
                    tool_calls=tool_calls,
                ),
            ],
        )
    )

    reloaded = await store.get_session(session_id, "test_org")
    assert reloaded is not None
    user, assistant = reloaded.conversation_history
    assert (user.reasoning, assistant.reasoning) == (None, "Plan: look first, then act.")
    assert assistant.tool_calls == tool_calls
    assert (user.author_user_id, assistant.author_user_id) == ("teammate_ana", None)

    assistant.reasoning = "Plan: revised."
    await store.update_session(reloaded)
    again = await store.get_session(session_id, "test_org")
    assert again is not None and again.conversation_history[1].reasoning == "Plan: revised."
    await manager.close()

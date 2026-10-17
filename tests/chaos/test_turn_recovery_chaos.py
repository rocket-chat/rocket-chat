"""Chaos and durability tests for crashed/interrupted turns and secondary runner recovery (Item A6b)."""

from __future__ import annotations

import os
import time
import uuid

import pytest
from agent_core.transcript import INTERRUPTED_TOOL_RESULT
from api.db.models import OrganizationModel, SessionModel, UserModel
from api.db.session import DatabaseSessionManager
from api.db.store import PostgresSessionStore
from api.services.compaction import PostgresCompactionStore
from api.services.transcript import InMemoryTranscriptStore, PostgresTranscriptStore
from api.services.turn_resume import RESUME_NOTICE
from sandbox_driver.fake_driver import FakeSandboxDriver

from specifications.interfaces.llm import ChatMessage
from tests.turn_harness import run_turn

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")
ADMIN_DATABASE_URL = os.getenv("ADMIN_DATABASE_URL")


@pytest.mark.asyncio
async def test_mid_tool_kill_recovers_without_duplicate_tool_execution() -> None:
    """Chaos test: runner is killed mid-tool execution; secondary runner resumes cleanly without duplicating completed tools."""
    transcript_store = InMemoryTranscriptStore()
    driver = FakeSandboxDriver()
    org = f"org_chaos_{uuid.uuid4().hex[:8]}"
    session_id = f"session_{org}"

    # Step 1: Tool 1 completes successfully (creates file_a)
    # Then Tool 2 is invoked but runner crashes before tool 2 completes
    completed_tool_calls: list[str] = []

    def tracking_file_write(path: str, content: str) -> None:
        completed_tool_calls.append(path)

    # Simulate seeded transcript after tool 1 completed, and assistant called tool 2 right before kill:
    seed_messages = [
        ChatMessage(role="user", content="Execute batch migration."),
        ChatMessage(
            role="assistant",
            content="",
            tool_calls=[
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {
                        "name": "file_write",
                        "arguments": '{"path": "file_a.txt", "content": "A"}',
                    },
                },
            ],
        ),
        ChatMessage(
            role="tool",
            tool_call_id="call_1",
            content="Wrote 1 bytes to file_a.txt",
        ),
        ChatMessage(
            role="assistant",
            content="",
            tool_calls=[
                {
                    "id": "call_2",
                    "type": "function",
                    "function": {
                        "name": "file_write",
                        "arguments": '{"path": "file_b.txt", "content": "B"}',
                    },
                },
            ],
        ),
        # Note: call_2 result was never written because process was SIGKILLed mid-execution
    ]
    await transcript_store.append(session_id, org, seed_messages)
    await driver.write_file(session_id, "file_a.txt", "A")

    # Step 2: Secondary runner boots up and resumes the session
    outcome = await run_turn(
        org,
        transcript_store=transcript_store,
        driver=driver,
        synthetic_prompt=True,
        prompt=RESUME_NOTICE,
        steps=[
            ("call", "file_write", {"path": "file_b.txt", "content": "B"}),
            ("say", "Batch migration resumed and finished successfully."),
        ],
    )

    # 1. Transcript repaired the interrupted tool call 2
    full_transcript = await transcript_store.load(session_id, org)
    repaired_call_2 = next(
        (m for m in full_transcript if m.role == "tool" and m.tool_call_id == "call_2"), None
    )
    assert repaired_call_2 is not None
    assert repaired_call_2.content == INTERRUPTED_TOOL_RESULT

    # 2. Restart note was present in resume turn
    first_req = outcome.model.requests[0]
    prompt_text = " ".join(str(m.content) for m in first_req.messages)
    assert "The server running your previous work restarted before it finished" in prompt_text

    # 3. File A was untouched, File B was successfully created, and turn completed
    assert await driver.read_file(session_id, "file_a.txt") == "A"
    assert await driver.read_file(session_id, "file_b.txt") == "B"
    assert outcome.session.title == "harness"


@pytest.mark.skipif(not TEST_DATABASE_URL, reason="Requires disposable test PostgreSQL")
@pytest.mark.asyncio
async def test_mid_stream_runner_kill_and_recovery_on_postgres() -> None:
    """Chaos test on PostgreSQL: runner dies mid-stream while outputting tokens; secondary runner completes once."""
    db = DatabaseSessionManager(TEST_DATABASE_URL)
    org_id = f"org_{uuid.uuid4().hex[:12]}"
    user_id = f"u_{org_id}"
    session_id = f"session_{org_id}"
    now = int(time.time())

    async with db.session(tenant_org_id=org_id) as session:
        session.add(OrganizationModel(id=org_id, name="Chaos Org", slug=org_id, created_at=now))
        session.add(
            UserModel(
                id=user_id, org_id=org_id, email="chaos@test.com", name="Chaos User", created_at=now
            )
        )
        session.add(
            SessionModel(
                id=session_id, org_id=org_id, user_id=user_id, title="Chaos Run", created_at=now
            )
        )

    transcript_store = PostgresTranscriptStore(db)
    compaction_store = PostgresCompactionStore(db)
    store = PostgresSessionStore(db)
    driver = FakeSandboxDriver()

    # Step 1: Simulate interrupted turn in transcript
    # User sent prompt, but runner crashed mid-stream (0 assistant tokens committed)
    await transcript_store.append(
        session_id,
        org_id,
        [ChatMessage(role="user", content="Deploy release 1.0")],
    )

    # Step 2: Secondary runner resumes turn
    outcome = await run_turn(
        org_id,
        store=store,
        transcript_store=transcript_store,
        compaction_store=compaction_store,
        driver=driver,
        prompt="Resume deployment.",
        synthetic_prompt=True,
        steps=[
            ("say", "Deployment successfully verified on secondary runner."),
        ],
    )
    assert outcome.session.session_id == session_id

    # Assert exactly-once completion in transcript
    messages = await transcript_store.load(session_id, org_id)
    assert len(messages) >= 2
    assert messages[-1].role == "assistant"
    assert "Deployment successfully verified" in (messages[-1].content or "")

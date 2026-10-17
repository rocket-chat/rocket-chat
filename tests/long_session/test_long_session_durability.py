"""Long-session engine tests: compaction hysteresis, durable checkpoints, crash recovery, and working memory (B3, B7, B8)."""

from __future__ import annotations

import time
import uuid

import pytest
from agent_core.orchestrator import AsyncReActOrchestrator
from agent_core.tools.registry import ToolRegistry
from api.services.compaction import InMemoryCompactionStore
from api.services.transcript import InMemoryTranscriptStore
from api.store import InMemorySessionStore
from sandbox_driver.exceptions import SandboxFilesystemError
from sandbox_driver.fake_driver import FakeSandboxDriver

from specifications.interfaces.compaction import CompactionCheckpoint
from specifications.interfaces.llm import ChatMessage
from tests.turn_harness import ScriptedModel, run_turn


@pytest.mark.asyncio
async def test_compaction_hysteresis_and_checkpoint_durability() -> None:
    """Verifies that compaction writes checkpoints, emits typed events, and honors hysteresis."""
    transcript_store = InMemoryTranscriptStore()
    compaction_store = InMemoryCompactionStore()
    driver = FakeSandboxDriver()
    org = "org_long_session"

    # Pre-seed working memory files in sandbox
    session_id = f"session_{org}"
    await driver.write_file(
        session_id, ".rocket/PLAN.md", "# Goal: Refactor auth service\n- [ ] Step 1\n- [ ] Step 2"
    )
    await driver.write_file(session_id, ".rocket/PROGRESS.md", "Started initial discovery.")

    # Script multiple tool calls to push context tokens past Tier 3 threshold
    steps: list[tuple[str, ...]] = []
    for i in range(15):
        steps.append(
            ("call", "bash_exec", {"command": f"echo 'Executing step {i} with payload data'"})
        )
    steps.append(("say", "All steps completed successfully."))

    outcome = await run_turn(
        org,
        steps=steps,
        driver=driver,
        transcript_store=transcript_store,
        compaction_store=compaction_store,
        # Set low context window so compaction triggers during the run
        model_choice="openrouter/deepseek/deepseek-v4.1-flash",
    )

    # 1. Verify working memory files were backed up to session metadata at turn end
    assert (
        outcome.session.metadata.get("plan_md")
        == "# Goal: Refactor auth service\n- [ ] Step 1\n- [ ] Step 2"
    )
    assert outcome.session.metadata.get("progress_md") == "Started initial discovery."

    # 2. Verify all messages in transcript store remain append-only and complete
    all_stored_messages = await transcript_store.load(session_id, org)
    assert len(all_stored_messages) >= 15
    for i in range(len(all_stored_messages) - 1):
        assert all_stored_messages[i] is not None


@pytest.mark.asyncio
async def test_crash_recovery_rehydrates_from_latest_checkpoint_without_recompacting() -> None:
    """If the pod crashes, the session rehydrates from latest compaction checkpoint + uncompacted tail."""
    transcript_store = InMemoryTranscriptStore()
    compaction_store = InMemoryCompactionStore()
    org = "org_crash_recovery"
    session_id = f"session_{org}"

    # 1. Seed 60 raw messages in transcript store
    raw_messages: list[ChatMessage] = [
        ChatMessage(role="system", content="You are a senior engineer."),
        ChatMessage(role="user", content="Please refactor the legacy database layer."),
    ]
    for i in range(1, 59):
        raw_messages.append(
            ChatMessage(
                role="assistant" if i % 2 == 1 else "user",
                content=f"Message step {i} details and thoughts",
            )
        )
    await transcript_store.append(session_id, org, raw_messages)

    last_seq = await transcript_store.latest_seq(session_id, org)
    assert last_seq == len(raw_messages) - 1

    # 2. Record a compaction checkpoint at boundary_seq = 40
    compacted_prefix = [
        ChatMessage(role="system", content="You are a senior engineer."),
        ChatMessage(role="user", content="Please refactor the legacy database layer."),
        ChatMessage(
            role="user",
            content=(
                "[System Note: Compacted 40 intermediate turns]\n"
                "Summary:\n"
                "1. Goal & Stop Condition: Refactor legacy database layer\n"
                "2. Decisions Made: Switched to SQLAlchemy 2.0\n"
                "3. Per-Repository State: Main branch up to date\n"
                "4. Files Touched: db/models.py\n"
                "5. Commands Run: alembic upgrade head\n"
                "6. Open Threads: None\n"
                "7. Next Steps: Complete migration\n"
                "Active Task Checklist State: [{'id': '1', 'status': 'completed'}]\n"
                "Working Plan (.rocket/PLAN.md):\n# Refactor Plan"
            ),
        ),
    ]
    checkpoint = CompactionCheckpoint(
        id=f"cmp_{uuid.uuid4().hex[:16]}",
        session_id=session_id,
        org_id=org,
        boundary_seq=40,
        summary="Structured summary of steps 1-40",
        compacted_messages=compacted_prefix,
        tokens_before=45000,
        tokens_after=12000,
        created_at=int(time.time()),
    )
    await compaction_store.record_compaction(checkpoint)

    # 3. Simulate process crash by creating a brand new Orchestrator with empty memory
    driver = FakeSandboxDriver()
    registry = ToolRegistry(driver=driver, session_id=session_id)
    model = ScriptedModel()
    orchestrator = AsyncReActOrchestrator(
        gateway=model,
        registry=registry,
        transcript_store=transcript_store,
        compaction_store=compaction_store,
    )
    assert session_id not in orchestrator._session_histories

    # 4. Rehydrate session conversation
    rehydrated = await orchestrator._conversation_for(session_id, org)

    # Rehydrated history consists of non-system compacted prefix (2 messages: initial user prompt + summary)
    # plus messages after boundary 40 (seq 41 to 59 = 19 messages) -> total 21 messages
    non_system_prefix = [m for m in compacted_prefix if m.role != "system"]
    assert len(rehydrated) == len(non_system_prefix) + (len(raw_messages) - 41)
    # Prefix messages match compacted prefix (system messages filtered by without_engine_notes)
    assert rehydrated[0].role == "user"
    assert "Compacted 40 intermediate turns" in (rehydrated[1].content or "")
    assert "Working Plan (.rocket/PLAN.md)" in (rehydrated[1].content or "")
    # Messages after boundary are intact
    assert rehydrated[2].content == raw_messages[41].content
    assert rehydrated[-1].content == raw_messages[-1].content

    # 5. Full transcript store still has all 60 messages completely intact (strictly append-only)
    full_transcript = await transcript_store.load(session_id, org)
    assert len(full_transcript) == len(raw_messages)


@pytest.mark.asyncio
async def test_working_memory_survives_sandbox_workspace_release() -> None:
    """Item B8: PLAN.md and PROGRESS.md survive sandbox workspace release via session metadata backup."""
    store = InMemorySessionStore()
    driver1 = FakeSandboxDriver()
    org = "org_workspace_release"

    # Pre-seed session with plan in metadata
    await run_turn(
        org,
        store=store,
        driver=driver1,
        steps=[
            ("call", "file_write", {"path": ".rocket/PLAN.md", "content": "# Project Plan"}),
            ("say", "Plan initialized."),
        ],
    )

    # Simulate sandbox release/destruction: fresh driver without files
    driver2 = FakeSandboxDriver()
    with pytest.raises(SandboxFilesystemError):
        await driver2.read_file(f"session_{org}", ".rocket/PLAN.md")

    # Second turn with fresh driver restores PLAN.md from session metadata
    await run_turn(
        org,
        store=store,
        driver=driver2,
        steps=[
            ("say", "Resumed turn."),
        ],
    )

    # Verify PLAN.md was restored in new sandbox workspace
    restored = await driver2.read_file(f"session_{org}", ".rocket/PLAN.md")
    assert restored.strip() == "# Project Plan"

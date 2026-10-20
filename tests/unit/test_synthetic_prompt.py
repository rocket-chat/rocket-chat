"""Tests for synthetic prompts (resume, continuation, wake turns).

Synthetic prompts must be delivered as system-reminder notes and never stored
as plain user messages, ensuring that context compaction anchors on the user's real
request and memory recall uses the real request.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

from agent_core.context import ContextManager, _current_request
from agent_core.orchestrator import AsyncReActOrchestrator
from agent_core.tools.registry import ToolRegistry
from api.services.turn_resume import RESUME_NOTICE
from api.store import InMemorySessionStore
from sandbox_driver.fake_driver import FakeSandboxDriver

from specifications.interfaces.llm import ChatMessage
from tests.turn_harness import ScriptedModel, run_turn


async def test_resumed_turn_stores_no_resume_notice_user_row() -> None:
    store = InMemorySessionStore()
    driver = FakeSandboxDriver()

    # Turn 1: Real user prompt
    outcome1 = await run_turn(
        "org_synth_1",
        prompt="Analyze repo architecture",
        store=store,
        driver=driver,
        final_text="Step 1 complete.",
    )
    assert len(outcome1.session.conversation_history) == 2
    assert outcome1.session.conversation_history[0].role == "user"
    assert outcome1.session.conversation_history[0].content == "Analyze repo architecture"

    # Turn 2: Synthetic resume turn
    outcome2 = await run_turn(
        "org_synth_1",
        prompt=RESUME_NOTICE,
        synthetic_prompt=True,
        store=store,
        driver=driver,
        final_text="Resumed analysis complete.",
    )

    # Conversation history in the session record must not store RESUME_NOTICE
    history = outcome2.session.conversation_history
    assert not any(m.role == "user" and RESUME_NOTICE in (m.content or "") for m in history)
    # The user messages in history must only be the real user prompt
    user_messages = [m for m in history if m.role == "user"]
    assert len(user_messages) == 1
    assert user_messages[0].content == "Analyze repo architecture"

    # The model still received the resume notice as a note in the first ReAct request
    first_req = outcome2.model.requests[0]
    assert first_req is not None
    # Check that RESUME_NOTICE was included in the request
    rendered_content = " ".join(m.content or "" for m in first_req.messages)
    assert "restarted" in rendered_content.lower()


async def test_forced_compaction_after_resume_keeps_original_request_verbatim() -> None:
    context_mgr = ContextManager(recent_turns_to_preserve=1)

    original_user_prompt = "Write the comprehensive database migration for schema v3"
    synthetic_resume_note = f"<system-reminder>\n{RESUME_NOTICE}\n</system-reminder>"
    messages = [
        ChatMessage(role="system", content="You are a coding assistant."),
        ChatMessage(role="user", content=original_user_prompt),
        ChatMessage(role="assistant", content="Analyzing tables..."),
        ChatMessage(role="tool", content="Table schema: users, accounts", tool_call_id="call_1"),
        ChatMessage(role="user", content=synthetic_resume_note),
        ChatMessage(role="assistant", content="Generating migration script..."),
        ChatMessage(role="tool", content="Migration script drafted", tool_call_id="call_2"),
    ]

    # Verify that _current_request skips the synthetic reminder and anchors on the real user message
    anchor_idx = _current_request(messages)
    assert anchor_idx == 1
    assert messages[anchor_idx].content == original_user_prompt

    # Compact with anchors
    compacted = await context_mgr.compact_with_summary(messages, None)

    # The compacted messages must retain the original user prompt as the anchor
    user_messages = [m for m in compacted if m.role == "user"]
    assert any(original_user_prompt in (m.content or "") for m in user_messages)
    assert not any((m.content or "").startswith(RESUME_NOTICE) for m in user_messages)


async def test_memory_recall_uses_original_request_on_synthetic_turn() -> None:
    session_id = "s_synth_recall"
    driver = FakeSandboxDriver()
    registry = ToolRegistry(driver=driver, session_id=session_id)
    recall_mock = AsyncMock(return_value="recalled context")
    registry.build_memory_context = recall_mock  # type: ignore[method-assign]

    model = ScriptedModel(final_text="Done.")
    orchestrator = AsyncReActOrchestrator(model, registry)

    # Turn 1: Real user message
    async for _ in orchestrator.process_user_turn(session_id, "Deploy to Kubernetes cluster"):
        pass

    assert recall_mock.call_count == 1
    assert recall_mock.call_args[0][0] == "Deploy to Kubernetes cluster"

    # Turn 2: Synthetic resume prompt
    async for _ in orchestrator.process_user_turn(session_id, RESUME_NOTICE, synthetic_prompt=True):
        pass

    assert recall_mock.call_count == 2
    # Second recall must be called with the original request, NOT the synthetic prompt
    assert recall_mock.call_args[0][0] == "Deploy to Kubernetes cluster"


async def test_an_automatic_turn_prompt_is_sent_once_where_the_turn_starts() -> None:
    """A wake prompt re-sent as the newest message on every step made models restart the
    same first step over and over: it must stay where the turn began."""
    store = InMemorySessionStore()
    driver = FakeSandboxDriver()
    await run_turn("org_synth_wake", prompt="Fix CI", store=store, driver=driver)
    wake = "ci_status update for acme/api#7: failing. Proceed with the next steps."

    outcome = await run_turn(
        "org_synth_wake",
        prompt=wake,
        synthetic_prompt=True,
        store=store,
        driver=driver,
        script=[("list_dir", {"path": "."}), ("list_dir", {"path": "src"})],
    )

    requests = [r for r in outcome.model.requests if r.tools]
    assert len(requests) == 3
    for request in requests:
        carrying = [i for i, m in enumerate(request.messages) if wake in (m.content or "")]
        assert len(carrying) == 1
    later = requests[-1].messages
    opener = next(i for i, m in enumerate(later) if wake in (m.content or ""))
    assert later[opener].role == "user"
    assert any(m.role == "tool" for m in later[opener + 1 :])
    assert wake not in (later[-1].content or "")
    first = requests[0].messages
    first_opener = next(i for i, m in enumerate(first) if wake in (m.content or ""))
    assert [m.content for m in later[: first_opener + 1]] == [
        m.content for m in first[: first_opener + 1]
    ]

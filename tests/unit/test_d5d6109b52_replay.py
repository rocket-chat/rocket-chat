"""Regression test for incident session_d5d6109b52 replayed through the real orchestrator."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

from agent_core.orchestrator import AsyncReActOrchestrator
from agent_core.tools.registry import ToolRegistry
from sandbox_driver.fake_driver import FakeSandboxDriver

from tests.fixtures.replays.replay_gateway import ReplayGateway


async def test_d5d6109b52_replay_executes_reasoning_and_completes() -> None:
    fixture_path = (
        Path(__file__).parent.parent
        / "fixtures"
        / "replays"
        / "d5d6109b52"
        / "session_d5d6109b52.json"
    )
    gateway = ReplayGateway(fixture_path)
    session_id = "s_d5d6109b52_test"
    driver = FakeSandboxDriver()
    registry = ToolRegistry(driver=driver, session_id=session_id)
    registry.build_memory_context = AsyncMock(return_value=gateway.memory_note)  # type: ignore[method-assign]
    orchestrator = AsyncReActOrchestrator(gateway, registry)

    async for _ in orchestrator.process_user_turn(session_id, gateway.data["initial_prompt"]):
        pass

    # Verify that the turn didn't abort on length-truncated reasoning steps
    assert len(gateway.requests) == 4
    # Verify memory note placement: must be inserted immediately after the user message
    assert gateway.requests[0].messages[0].content == gateway.data["initial_prompt"]
    assert gateway.memory_note in (gateway.requests[0].messages[1].content or "")
    # Verify prefix stability: every request has the user message at index 0 and memory note at index 1
    for req in gateway.requests:
        assert req.messages[0].content == gateway.data["initial_prompt"]
        assert gateway.memory_note in (req.messages[1].content or "")

    # Step 1: initial request; Step 2: after checklist update tool
    # Steps 3 & 4: empty reply reasoning retries (finish_reason=length)
    assert "output token limit while you were still reasoning" in (
        gateway.requests[2].messages[-1].content or ""
    )
    assert "output token limit while you were still reasoning" in (
        gateway.requests[3].messages[-1].content or ""
    )
    history = orchestrator._session_histories[session_id]
    final_message = history[-1]
    assert final_message.role == "assistant"
    assert "Rebase onto origin/beta complete" in (final_message.content or "")

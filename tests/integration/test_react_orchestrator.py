"""Integration test for AsyncReActOrchestrator with Interactive Decision Gates."""

import asyncio
import json
import os
import uuid
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import MagicMock

import pytest
from agent_core.orchestrator import AsyncReActOrchestrator, OrchestratorState
from agent_core.tools.registry import ToolRegistry
from llm_gateway.gateway import LiteLLMGateway
from sandbox_driver.docker_driver import DockerSandboxDriver

from specifications.interfaces.agent import AgentEvent
from specifications.interfaces.sandbox import ResourceLimits, WorkspaceSpec


@pytest.mark.asyncio
async def test_react_orchestrator_bug_fix_and_decision_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_id = f"test_react_{uuid.uuid4().hex[:8]}"
    driver = DockerSandboxDriver(default_image="python:3.12-slim")

    spec = WorkspaceSpec(
        session_id=session_id,
        tenant_org_id="org_test",
        tenant_user_id="user_test",
        container_image="python:3.12-slim",
        resources=ResourceLimits(cpu_cores=1.0, memory_limit="1Gi"),
    )

    await driver.ensure_workspace(spec)
    await driver.start_sandbox(session_id)

    # Prepare failing calculator and test suite inside sandbox workspace
    initial_calc = "def divide(a: float, b: float) -> float:\n    return a / b\n"
    calc_test = (
        "import pytest\n"
        "from calculator import divide\n\n"
        "def test_divide_normal():\n"
        "    assert divide(10, 2) == 5\n\n"
        "def test_divide_by_zero():\n"
        "    with pytest.raises(ValueError, match='Cannot divide by zero'):\n"
        "        divide(10, 0)\n"
    )

    await driver.write_file(session_id, "calculator.py", initial_calc)
    await driver.write_file(session_id, "test_calculator.py", calc_test)

    # Install pytest inside sandbox container
    await driver.exec_command(session_id, "pip install pytest")

    registry = ToolRegistry(driver=driver, session_id=session_id)
    gateway = LiteLLMGateway()
    orchestrator = AsyncReActOrchestrator(gateway=gateway, registry=registry)

    has_live_key = bool(
        os.getenv("RUN_LIVE_LLM_TESTS")
        and (
            os.getenv("OPENROUTER_API_KEY")
            or os.getenv("ANTHROPIC_API_KEY")
            or os.getenv("OPENAI_API_KEY")
        )
    )

    if not has_live_key:
        turn_counter = 0

        async def simulated_acompletion(**kwargs: Any) -> AsyncIterator[Any]:
            nonlocal turn_counter
            turn_counter += 1

            if turn_counter == 1:
                # Turn 1: Run pytest to reproduce the bug
                async def _gen_turn1() -> AsyncIterator[Any]:
                    c1 = MagicMock()
                    ch1 = MagicMock()
                    ch1.delta.content = (
                        "<think>Step 1: Reproduce the test failure by running pytest.</think>"
                    )
                    ch1.delta.reasoning_content = None
                    ch1.delta.thinking = None
                    ch1.delta.tool_calls = None
                    ch1.finish_reason = None
                    c1.choices = [ch1]
                    c1.usage = None
                    yield c1

                    c2 = MagicMock()
                    ch2 = MagicMock()
                    ch2.delta.content = None
                    ch2.delta.reasoning_content = None
                    ch2.delta.thinking = None
                    tc = MagicMock()
                    tc.index = 0
                    tc.id = "call_test_1"
                    tc.type = "function"
                    tc.function.name = "bash_exec"
                    tc.function.arguments = json.dumps({"command": "pytest test_calculator.py"})
                    ch2.delta.tool_calls = [tc]
                    ch2.finish_reason = "tool_calls"
                    c2.choices = [ch2]
                    c2.usage = None
                    yield c2

                return _gen_turn1()

            elif turn_counter == 2:
                # Turn 2: Test failed with ZeroDivisionError. Ask decision clarification question.
                async def _gen_turn2() -> AsyncIterator[Any]:
                    c1 = MagicMock()
                    ch1 = MagicMock()
                    ch1.delta.content = (
                        "<think>Step 2: Test failed. Should we return None or raise ValueError? "
                        "Asking user.</think>"
                    )
                    ch1.delta.reasoning_content = None
                    ch1.delta.thinking = None
                    ch1.delta.tool_calls = None
                    ch1.finish_reason = None
                    c1.choices = [ch1]
                    c1.usage = None
                    yield c1

                    c2 = MagicMock()
                    ch2 = MagicMock()
                    ch2.delta.content = None
                    ch2.delta.reasoning_content = None
                    ch2.delta.thinking = None
                    tc = MagicMock()
                    tc.index = 0
                    tc.id = "call_ask_1"
                    tc.type = "function"
                    tc.function.name = "ask_question"
                    tc.function.arguments = json.dumps(
                        {
                            "question": "Should zero division return None or raise ValueError?",
                            "options": ["Raise ValueError", "Return None"],
                            "default_recommended_option": "Raise ValueError",
                        }
                    )
                    ch2.delta.tool_calls = [tc]
                    ch2.finish_reason = "tool_calls"
                    c2.choices = [ch2]
                    c2.usage = None
                    yield c2

                return _gen_turn2()

            elif turn_counter == 3:
                # Turn 3: User responded 'Raise ValueError'. Perform file_edit on calculator.py
                async def _gen_turn3() -> AsyncIterator[Any]:
                    c1 = MagicMock()
                    ch1 = MagicMock()
                    ch1.delta.content = (
                        "<think>Step 3: User confirmed Raise ValueError. "
                        "Applying file_edit.</think>"
                    )
                    ch1.delta.reasoning_content = None
                    ch1.delta.thinking = None
                    ch1.delta.tool_calls = None
                    ch1.finish_reason = None
                    c1.choices = [ch1]
                    c1.usage = None
                    yield c1

                    c2 = MagicMock()
                    ch2 = MagicMock()
                    ch2.delta.content = None
                    ch2.delta.reasoning_content = None
                    ch2.delta.thinking = None
                    tc = MagicMock()
                    tc.index = 0
                    tc.id = "call_edit_1"
                    tc.type = "function"
                    tc.function.name = "file_edit"
                    tc.function.arguments = json.dumps(
                        {
                            "path": "calculator.py",
                            "target_block": "def divide(a: float, b: float) -> float:\n    return a / b",
                            "replacement_block": (
                                "def divide(a: float, b: float) -> float:\n"
                                "    if b == 0:\n"
                                "        raise ValueError('Cannot divide by zero')\n"
                                "    return a / b"
                            ),
                        }
                    )
                    ch2.delta.tool_calls = [tc]
                    ch2.finish_reason = "tool_calls"
                    c2.choices = [ch2]
                    c2.usage = None
                    yield c2

                return _gen_turn3()

            elif turn_counter == 4:
                # Turn 4: Verify with pytest
                async def _gen_turn4() -> AsyncIterator[Any]:
                    c1 = MagicMock()
                    ch1 = MagicMock()
                    ch1.delta.content = "<think>Step 4: Re-running pytest to verify fix.</think>"
                    ch1.delta.reasoning_content = None
                    ch1.delta.thinking = None
                    ch1.delta.tool_calls = None
                    ch1.finish_reason = None
                    c1.choices = [ch1]
                    c1.usage = None
                    yield c1

                    c2 = MagicMock()
                    ch2 = MagicMock()
                    ch2.delta.content = None
                    ch2.delta.reasoning_content = None
                    ch2.delta.thinking = None
                    tc = MagicMock()
                    tc.index = 0
                    tc.id = "call_test_2"
                    tc.type = "function"
                    tc.function.name = "bash_exec"
                    tc.function.arguments = json.dumps({"command": "pytest test_calculator.py"})
                    ch2.delta.tool_calls = [tc]
                    ch2.finish_reason = "tool_calls"
                    c2.choices = [ch2]
                    c2.usage = None
                    yield c2

                return _gen_turn4()

            else:
                # Turn 5: Complete
                async def _gen_turn5() -> AsyncIterator[Any]:
                    c = MagicMock()
                    ch = MagicMock()
                    ch.delta.content = "Bug fixed! All unit tests are now green."
                    ch.delta.reasoning_content = None
                    ch.delta.thinking = None
                    ch.delta.tool_calls = None
                    ch.finish_reason = "stop"
                    c.choices = [ch]
                    c.usage = None
                    yield c

                return _gen_turn5()

        import litellm

        monkeypatch.setattr(litellm, "acompletion", simulated_acompletion)

    try:
        collected_events: list[AgentEvent] = []
        question_received = asyncio.Event()
        captured_question_id = ""

        async def run_consumer() -> None:
            nonlocal captured_question_id
            async for event in orchestrator.process_user_turn(
                session_id, "Fix the test in test_calculator.py."
            ):
                collected_events.append(event)
                if event.event_type == "interactive_question":
                    captured_question_id = event.payload["question_id"]
                    question_received.set()

        consumer_task = asyncio.create_task(run_consumer())

        # Wait until the decision gate is triggered
        await asyncio.wait_for(question_received.wait(), timeout=15.0)

        # 1. Verify orchestrator paused at decision gate
        state = orchestrator.get_session_state(session_id)
        assert state.lower() == OrchestratorState.PAUSED_FOR_INPUT.value

        # 2. Answer question to awaken orchestrator
        await orchestrator.submit_question_answer(
            session_id=session_id,
            question_id=captured_question_id,
            selected_options=["Raise ValueError"],
        )

        # Wait for remaining turn execution to finish
        await asyncio.wait_for(consumer_task, timeout=15.0)

        # 3. Verify FileDiff event was generated
        diff_events = [e for e in collected_events if e.event_type == "file_diff"]
        assert len(diff_events) > 0
        diff_payload = diff_events[0].payload
        assert diff_payload["path"] == "calculator.py"
        assert diff_payload["additions"] > 0
        assert "Cannot divide by zero" in diff_payload["diff_content"]

        # 4. Verify test now passes in sandbox
        final_test = await driver.exec_command(session_id, "pytest test_calculator.py")
        assert final_test.exit_code == 0
        assert "passed" in final_test.stdout

    finally:
        await driver.destroy_workspace(session_id)

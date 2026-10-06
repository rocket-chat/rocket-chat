"""Integration test verifying full Slack Assistant to DockerSandbox interoperability."""

import json
import os
import uuid
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from agent_core.orchestrator import AsyncReActOrchestrator
from agent_core.tools.registry import ToolRegistry
from llm_gateway.gateway import LiteLLMGateway
from sandbox_driver.docker_driver import DockerSandboxDriver
from slack_assistant.assistant import SlackAssistant
from slack_assistant.config import SlackAssistantSettings

from specifications.interfaces.sandbox import ResourceLimits, WorkspaceSpec


@pytest.mark.asyncio
async def test_slack_interoperability_math_bug_fix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Verify complete vertical flow:
    Slack Assistant message -> ReAct Orchestrator -> LiteLLM Gateway ->
    Docker Sandbox command execution -> AST File Editing -> Block Kit Diff Response.
    """
    channel_id = "C_DEV"
    raw_ts = f"1700000000.{uuid.uuid4().hex[:6]}"
    session_id = SlackAssistant.get_session_id(channel_id, raw_ts)

    driver = DockerSandboxDriver(default_image="python:3.12-slim")
    spec = WorkspaceSpec(
        session_id=session_id,
        tenant_org_id="slack_org",
        tenant_user_id="user_dev",
        container_image="python:3.12-slim",
        resources=ResourceLimits(cpu_cores=1.0, memory_limit="1Gi"),
    )

    await driver.ensure_workspace(spec)
    await driver.start_sandbox(session_id)

    # Populate sandbox with buggy math module and test suite
    buggy_math = (
        "def multiply(a: int, b: int) -> int:\n"
        "    return a + b  # Bug: addition instead of multiplication\n"
    )
    test_math = (
        "import unittest\n"
        "from math_mod import multiply\n\n"
        "class TestMath(unittest.TestCase):\n"
        "    def test_multiply(self):\n"
        "        self.assertEqual(multiply(3, 4), 12)\n\n"
        "if __name__ == '__main__':\n"
        "    unittest.main()\n"
    )

    await driver.write_file(session_id, "math_mod.py", buggy_math)
    await driver.write_file(session_id, "test_math.py", test_math)

    registry = ToolRegistry(driver=driver, session_id=session_id)
    gateway = LiteLLMGateway()
    orchestrator = AsyncReActOrchestrator(gateway=gateway, registry=registry)
    settings = SlackAssistantSettings(
        slack_bot_token="xoxb-test-interop-token",
        slack_app_token="xapp-test-interop-token",
    )
    assistant = SlackAssistant(
        orchestrator=orchestrator,
        sandbox_driver=driver,
        tool_registry=registry,
        settings=settings,
    )

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
                # Turn 1: Run unit test to confirm test failure
                async def _gen_step1() -> AsyncIterator[Any]:
                    c1 = MagicMock()
                    ch1 = MagicMock()
                    ch1.delta.content = (
                        "<think>Running python3 test_math.py to reproduce the bug.</think>"
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
                    tc.function.arguments = json.dumps({"command": "python3 test_math.py"})
                    ch2.delta.tool_calls = [tc]
                    ch2.finish_reason = "tool_calls"
                    c2.choices = [ch2]
                    c2.usage = None
                    yield c2

                return _gen_step1()

            elif turn_counter == 2:
                # Turn 2: Test failed with AssertionError. Apply file_edit to fix math_mod.py
                async def _gen_step2() -> AsyncIterator[Any]:
                    c1 = MagicMock()
                    ch1 = MagicMock()
                    ch1.delta.content = "<think>Fixing math_mod.py by replacing + with *.</think>"
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
                            "path": "math_mod.py",
                            "target_block": "    return a + b  # Bug: addition instead of multiplication",
                            "replacement_block": "    return a * b",
                        }
                    )
                    ch2.delta.tool_calls = [tc]
                    ch2.finish_reason = "tool_calls"
                    c2.choices = [ch2]
                    c2.usage = None
                    yield c2

                return _gen_step2()

            elif turn_counter == 3:
                # Turn 3: Verify the fix with python3 test_math.py
                async def _gen_step3() -> AsyncIterator[Any]:
                    c1 = MagicMock()
                    ch1 = MagicMock()
                    ch1.delta.content = "<think>Re-running python3 test_math.py to verify.</think>"
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
                    tc.function.arguments = json.dumps({"command": "python3 test_math.py"})
                    ch2.delta.tool_calls = [tc]
                    ch2.finish_reason = "tool_calls"
                    c2.choices = [ch2]
                    c2.usage = None
                    yield c2

                return _gen_step3()

            else:
                # Turn 4: Complete
                async def _gen_step4() -> AsyncIterator[Any]:
                    c = MagicMock()
                    ch = MagicMock()
                    ch.delta.content = "Bug resolved in math_mod.py. All unit tests pass."
                    ch.delta.reasoning_content = None
                    ch.delta.thinking = None
                    ch.delta.tool_calls = None
                    ch.finish_reason = "stop"
                    c.choices = [ch]
                    c.usage = None
                    yield c

                return _gen_step4()

        import litellm

        monkeypatch.setattr(litellm, "acompletion", simulated_acompletion)

    say_mock = AsyncMock()
    status_mock = AsyncMock()

    try:
        # Simulate incoming Slack mention in thread
        await assistant.process_incoming_request(
            channel_id=channel_id,
            thread_ts=raw_ts,
            raw_text="<@U_ROCKETCHAT> fix the bug in math.py",
            say_fn=say_mock,
            status_fn=status_mock,
        )

        # 1. Verify Slack status spinners were dispatched
        assert status_mock.call_count >= 3
        status_calls = [call.kwargs.get("status") for call in status_mock.call_args_list]
        assert any("Analyzing request..." in str(s) for s in status_calls)
        assert any("command" in str(s) or "test" in str(s) for s in status_calls)
        assert status_calls[-1] == ""  # Status cleared on completion

        # 2. Verify Slack say called with Block Kit payload
        assert say_mock.call_count == 1
        _, say_kwargs = say_mock.call_args
        assert say_kwargs["thread_ts"] == raw_ts
        blocks = say_kwargs.get("blocks", [])
        assert len(blocks) >= 3

        # 3. Verify syntax-highlighted diff and approval buttons in Block Kit
        blocks_text = json.dumps(blocks)
        assert "```diff" in blocks_text
        assert "math_mod.py" in blocks_text
        assert "+    return a * b" in blocks_text
        assert "approve_action" in blocks_text
        assert "reject_action" in blocks_text

        # 4. Verify actual file content in sandbox volume
        updated_content = await driver.read_file(session_id, "math_mod.py")
        assert "return a * b" in updated_content
        assert "return a + b" not in updated_content

        # 5. Verify unit tests pass in sandbox
        final_test = await driver.exec_command(session_id, "python3 test_math.py")
        assert final_test.exit_code == 0

    finally:
        await driver.destroy_workspace(session_id)

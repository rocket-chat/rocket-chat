import json
import os
import uuid
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import MagicMock

import pytest
from agent_core.tools.registry import ToolRegistry
from llm_gateway.gateway import LiteLLMGateway
from sandbox_driver.docker_driver import DockerSandboxDriver

from specifications.interfaces.llm import ChatMessage, ModelRequest
from specifications.interfaces.sandbox import ResourceLimits, WorkspaceSpec


@pytest.mark.asyncio
async def test_autonomous_tool_calling_and_reasoning_extraction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_id = f"test_tools_{uuid.uuid4().hex[:8]}"
    driver = DockerSandboxDriver(default_image="python:3.12-slim")

    spec = WorkspaceSpec(
        session_id=session_id,
        tenant_org_id="org_test",
        tenant_user_id="user_test",
        container_image="python:3.12-slim",
        resources=ResourceLimits(cpu_cores=1.0, memory_limit="1Gi"),
    )

    # Initialize live Docker sandbox
    await driver.ensure_workspace(spec)
    await driver.start_sandbox(session_id)

    registry = ToolRegistry(driver=driver, session_id=session_id)
    gateway = LiteLLMGateway()

    # If no live API key is configured, simulate LiteLLM streaming responses for the 2-turn tool loop
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
                # Turn 1: Model thinks, then requests web_fetch for https://httpbin.org/json
                async def _gen_turn1() -> AsyncIterator[Any]:
                    # Reasoning stream
                    c1 = MagicMock()
                    ch1 = MagicMock()
                    ch1.delta.content = (
                        "<think>Step 1: I need to fetch the JSON payload from httpbin.</think>"
                    )
                    ch1.delta.reasoning_content = None
                    ch1.delta.thinking = None
                    ch1.delta.tool_calls = None
                    ch1.finish_reason = None
                    c1.choices = [ch1]
                    c1.usage = None
                    yield c1

                    # Tool call: web_fetch
                    c2 = MagicMock()
                    ch2 = MagicMock()
                    ch2.delta.content = None
                    ch2.delta.reasoning_content = None
                    ch2.delta.thinking = None
                    tc = MagicMock()
                    tc.index = 0
                    tc.id = "call_fetch_1"
                    tc.type = "function"
                    tc.function.name = "web_fetch"
                    tc.function.arguments = json.dumps({"url": "https://httpbin.org/json"})
                    ch2.delta.tool_calls = [tc]
                    ch2.finish_reason = "tool_calls"
                    c2.choices = [ch2]
                    c2.usage = MagicMock(prompt_tokens=20, completion_tokens=15, total_tokens=35)
                    yield c2

                return _gen_turn1()

            elif turn_counter == 2:
                # Turn 2: Model receives payload, thinks, then executes bash to write /workspace/title.txt
                async def _gen_turn2() -> AsyncIterator[Any]:
                    c1 = MagicMock()
                    ch1 = MagicMock()
                    ch1.delta.content = (
                        "<think>Step 2: Title found is 'Sample Slide Show'. "
                        "Writing to /workspace/title.txt via bash.</think>"
                    )
                    ch1.delta.reasoning_content = None
                    ch1.delta.thinking = None
                    ch1.delta.tool_calls = None
                    ch1.finish_reason = None
                    c1.choices = [ch1]
                    c1.usage = None
                    yield c1

                    # Tool call: bash_exec
                    c2 = MagicMock()
                    ch2 = MagicMock()
                    ch2.delta.content = None
                    ch2.delta.reasoning_content = None
                    ch2.delta.thinking = None
                    tc = MagicMock()
                    tc.index = 0
                    tc.id = "call_bash_2"
                    tc.type = "function"
                    tc.function.name = "bash_exec"
                    tc.function.arguments = json.dumps(
                        {"command": "echo 'Sample Slide Show' > /workspace/title.txt"}
                    )
                    ch2.delta.tool_calls = [tc]
                    ch2.finish_reason = "tool_calls"
                    c2.choices = [ch2]
                    c2.usage = MagicMock(prompt_tokens=45, completion_tokens=20, total_tokens=65)
                    yield c2

                return _gen_turn2()
            else:
                # Turn 3: Final confirmation message
                async def _gen_turn3() -> AsyncIterator[Any]:
                    c = MagicMock()
                    ch = MagicMock()
                    ch.delta.content = "Saved title to /workspace/title.txt"
                    ch.delta.reasoning_content = None
                    ch.delta.thinking = None
                    ch.delta.tool_calls = None
                    ch.finish_reason = "stop"
                    c.choices = [ch]
                    c.usage = MagicMock(prompt_tokens=70, completion_tokens=10, total_tokens=80)
                    yield c

                return _gen_turn3()

        import litellm

        monkeypatch.setattr(litellm, "acompletion", simulated_acompletion)

    try:
        messages: list[ChatMessage] = [
            ChatMessage(
                role="user",
                content=(
                    "Fetch https://httpbin.org/json, extract the title, "
                    "and save it to /workspace/title.txt using bash."
                ),
            )
        ]

        collected_reasoning: list[str] = []
        collected_text: list[str] = []

        # Run multi-turn autonomous loop
        max_turns = 5
        for _ in range(max_turns):
            request = ModelRequest(
                model=os.getenv("DEFAULT_MODEL", "openrouter/anthropic/claude-3.7-sonnet"),
                messages=messages,
                tools=registry.get_tool_definitions(),
            )

            assistant_text = ""
            tool_calls_raw: dict[int, dict[str, Any]] = {}

            async for chunk in gateway.chat_stream(request):
                if chunk.reasoning_delta:
                    collected_reasoning.append(chunk.reasoning_delta)
                if chunk.text_delta:
                    collected_text.append(chunk.text_delta)
                    assistant_text += chunk.text_delta
                if chunk.tool_call_delta:
                    tc_idx = chunk.tool_call_delta.get("index", 0)
                    if tc_idx not in tool_calls_raw:
                        tool_calls_raw[tc_idx] = {
                            "id": chunk.tool_call_delta.get("id"),
                            "name": "",
                            "arguments": "",
                        }
                    func = chunk.tool_call_delta.get("function") or {}
                    if func.get("name"):
                        tool_calls_raw[tc_idx]["name"] = func["name"]
                    if func.get("arguments"):
                        tool_calls_raw[tc_idx]["arguments"] += func["arguments"]

            # If no tool calls were requested, the agent has finished
            if not tool_calls_raw:
                messages.append(ChatMessage(role="assistant", content=assistant_text))
                break

            # Execute requested tool calls through FastMCP registry
            formatted_tool_calls = []
            for tc_data in tool_calls_raw.values():
                formatted_tool_calls.append(
                    {
                        "id": tc_data["id"],
                        "type": "function",
                        "function": {
                            "name": tc_data["name"],
                            "arguments": tc_data["arguments"],
                        },
                    }
                )

            messages.append(
                ChatMessage(
                    role="assistant",
                    content=assistant_text or None,
                    tool_calls=formatted_tool_calls,
                )
            )

            for tc_data in tool_calls_raw.values():
                tool_name = tc_data["name"]
                args = json.loads(tc_data["arguments"]) if tc_data["arguments"] else {}
                tool_output = await registry.call_tool(tool_name, args)

                messages.append(
                    ChatMessage(
                        role="tool",
                        content=tool_output,
                        tool_call_id=tc_data["id"],
                    )
                )

        # Verification 1: Autonomous Tool Calling & File Persistence
        title_file_content = await driver.read_file(session_id, "title.txt")
        assert "Sample Slide Show" in title_file_content.strip()

        # Verification 2: Reasoning Stream Extraction
        full_reasoning = "".join(collected_reasoning)
        full_text = "".join(collected_text)

        assert len(collected_reasoning) > 0
        assert "<think>" not in full_reasoning
        assert "</think>" not in full_reasoning
        assert "<think>" not in full_text
        assert "</think>" not in full_text
        assert "Step 1:" in full_reasoning or "Step 2:" in full_reasoning

    finally:
        await driver.destroy_workspace(session_id)

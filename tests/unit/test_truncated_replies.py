"""A reply cut off at the output-token limit never runs its half-written tool call."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from agent_core.orchestrator import AsyncReActOrchestrator
from agent_core.subagents import SubagentRunner
from agent_core.tools.registry import ToolRegistry
from sandbox_driver.fake_driver import FakeSandboxDriver

from specifications.interfaces.agent import SubagentTaskRequest
from specifications.interfaces.llm import LLMGatewayProtocol, ModelRequest, StreamChunk

CUT = '{"path": "big.py", "content": "def main():\\n    print(\'unfinished'


class _CutOffOnce(LLMGatewayProtocol):
    def __init__(self) -> None:
        self.requests: list[ModelRequest] = []

    async def chat_stream(self, request: ModelRequest) -> AsyncIterator[StreamChunk]:
        self.requests.append(request)
        if len(self.requests) == 1:
            yield StreamChunk(
                tool_call_delta={
                    "index": 0,
                    "id": "c1",
                    "function": {"name": "file_write", "arguments": CUT},
                }
            )
            yield StreamChunk(is_finished=True, finish_reason="length")
            return
        yield StreamChunk(text_delta="I will write it in parts.")

    async def check_budget(self, *args: Any) -> bool:
        return True

    async def record_usage(self, *args: Any, **kwargs: Any) -> None:
        return None


async def test_the_cut_call_is_not_run_and_the_model_is_told_to_split_it() -> None:
    model = _CutOffOnce()
    driver = FakeSandboxDriver()
    orchestrator = AsyncReActOrchestrator(model, ToolRegistry(driver=driver, session_id="s_cut"))

    async for _ in orchestrator.process_user_turn("s_cut", "write big.py"):
        pass

    assert "big.py" not in driver.file_snapshot("s_cut")
    reminder = model.requests[1].messages[-1].content or ""
    assert "cut off at the output token limit" in reminder and "`file_write`" in reminder
    history = orchestrator._session_histories["s_cut"]
    assert not any(m.tool_calls for m in history), "nothing half-written is recorded"


async def test_a_delegate_is_told_the_same() -> None:
    model = _CutOffOnce()
    registry = ToolRegistry(driver=FakeSandboxDriver(), session_id="s_cut_sub")
    runner = SubagentRunner(gateway=model, registry=registry)

    result = await runner.run(
        SubagentTaskRequest(role="researcher", task="write big.py"), "s_cut_sub", None
    )

    notes = [m.content or "" for m in model.requests[1].messages if m.role == "user"]
    assert any("cut off at the output token limit" in n for n in notes)
    assert result.tool_calls_count == 0 and "in parts" in result.summary

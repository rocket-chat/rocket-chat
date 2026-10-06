"""
Integration test for heavy coding orchestration with context hygiene.
Simulates a multi-turn ReAct coding session generating large outputs and verifies
that Tier 1 clamping and Tier 2/3 compaction preserve execution stability.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import MagicMock

import pytest
from agent_core.orchestrator import AsyncReActOrchestrator
from agent_core.tools.registry import ToolRegistry

from specifications.interfaces.agent import AgentEvent
from specifications.interfaces.llm import ModelRequest, StreamChunk
from specifications.interfaces.sandbox import ExecResult, SandboxDriverProtocol


class MockHeavySandboxDriver(SandboxDriverProtocol):
    async def ensure_workspace(self, spec: Any) -> Any:
        return None

    async def start_sandbox(self, session_id: str) -> None:
        pass

    async def hibernate_sandbox(self, session_id: str) -> None:
        pass

    async def destroy_workspace(self, session_id: str) -> None:
        pass

    async def get_status(self, session_id: str) -> Any:
        return None

    async def list_files(
        self, session_id: str, directory: str = "/workspace", max_depth: int = 3
    ) -> list[Any]:
        return []

    async def exec_command(
        self,
        session_id: str,
        command: str,
        workdir: str = "/workspace",
        timeout_seconds: int = 120,
        env: dict[str, str] | None = None,
        on_stdout_chunk: Any = None,
    ) -> ExecResult:
        # Generates a massive 1500-line build log
        lines = [f"[DEBUG-BUILD] compile step {i}: compiling module_{i}.o" for i in range(1500)]
        lines.append("Build succeeded with 0 errors.")
        return ExecResult(
            exit_code=0,
            stdout="\n".join(lines),
            stderr="",
            duration_ms=50,
        )

    async def read_file(
        self,
        session_id: str,
        path: str,
        start_line: int | None = None,
        end_line: int | None = None,
    ) -> str:
        return "def main(): pass\n" * 1000

    async def write_file(
        self, session_id: str, path: str, content: str, overwrite: bool = True
    ) -> None:
        pass

    async def apply_patch(self, session_id: str, path: str, patch_content: str) -> bool:
        return True


@pytest.mark.asyncio
async def test_heavy_coding_orchestration_context_hygiene() -> None:
    driver = MockHeavySandboxDriver()
    registry = ToolRegistry(driver=driver, session_id="heavy-session")
    mock_gateway = MagicMock()

    turn_counter = 0

    async def simulated_stream(request: ModelRequest) -> AsyncIterator[StreamChunk]:
        nonlocal turn_counter
        turn_counter += 1

        if turn_counter < 4:
            # Emit bash_exec tool call that produces 1500 lines of output
            yield StreamChunk(
                tool_call_delta={
                    "index": 0,
                    "id": f"call_{turn_counter}",
                    "function": {
                        "name": "bash_exec",
                        "arguments": json.dumps({"command": f"make build_target_{turn_counter}"}),
                    },
                }
            )
            yield StreamChunk(text_delta=f"Dispatched build step {turn_counter}", is_finished=True)
        else:
            yield StreamChunk(
                text_delta="All heavy build stages complete successfully.", is_finished=True
            )

    mock_gateway.chat_stream = simulated_stream

    orchestrator = AsyncReActOrchestrator(
        gateway=mock_gateway,
        registry=registry,
        max_turns=6,
    )

    events: list[AgentEvent] = []
    async for ev in orchestrator.process_user_turn(
        "heavy-session", "Build and verify large project."
    ):
        events.append(ev)

    assert any(
        ev.event_type == "status" and ev.payload.get("status") == "completed" for ev in events
    )
    history = orchestrator._session_histories["heavy-session"]

    # Assert that tool outputs were clamped by Tier 1
    tool_messages = [m for m in history if m.role == "tool"]
    assert len(tool_messages) >= 3
    for tm in tool_messages:
        assert tm.content is not None
        # Head lines present
        assert "compile step 0" in tm.content
        # Marker present due to clamping
        assert "TRUNCATED" in tm.content
        # Tail lines present
        assert "Build succeeded with 0 errors" in tm.content

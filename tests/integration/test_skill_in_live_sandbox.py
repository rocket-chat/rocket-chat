"""A skill is executed by the agent loop inside a real sandbox (plan SG21); skipped without Docker."""

from __future__ import annotations

import json
import shutil
import subprocess
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

import pytest
from agent_core.orchestrator import AsyncReActOrchestrator
from agent_core.tools.registry import ToolRegistry
from api.services.skills import test_skill_execution as run_skill_in_sandbox
from sandbox_driver.docker_driver import DockerSandboxDriver

from specifications.interfaces.agent import TurnCallbacks
from specifications.interfaces.llm import LLMGatewayProtocol, StreamChunk
from specifications.interfaces.sandbox import WorkspaceSpec

IMAGE = "rocket-chat-sandbox:dev"


def _image_available() -> bool:
    if shutil.which("docker") is None:
        return False
    probe = subprocess.run(["docker", "image", "inspect", IMAGE], capture_output=True, check=False)
    return probe.returncode == 0


pytestmark = pytest.mark.skipif(not _image_available(), reason=f"Docker image {IMAGE} not built")

SKILL_CODE = """\
import os, pathlib

def audit(path: str, minimum: int = 1) -> dict:
    root = pathlib.Path(path)
    sizes = sorted(p.stat().st_size for p in root.glob("*.txt"))
    return {"files": len(sizes), "ok": len(sizes) >= minimum, "uid": os.getuid(),
            "sees_api_secret": "ENCRYPTION_MASTER_KEY" in os.environ}
"""


@dataclass
class _CallsTheSkill(LLMGatewayProtocol):
    requests: list[Any] = field(default_factory=list)

    async def chat_stream(self, request: Any) -> AsyncIterator[StreamChunk]:
        self.requests.append(request)
        results = [m.content for m in request.messages if m.role == "tool"]
        if not results:
            yield StreamChunk(
                tool_call_delta={
                    "index": 0,
                    "id": "call_1",
                    "function": {
                        "name": "execute_skill",
                        "arguments": json.dumps(
                            {"skill_name": "audit", "arguments": {"path": "/workspace/data"}}
                        ),
                    },
                }
            )
            return
        yield StreamChunk(text_delta=f"DONE {results[0]}")

    async def check_budget(self, tenant_org_id: str, tenant_user_id: str) -> bool:
        return True

    async def record_usage(self, *args: Any, **kwargs: Any) -> None:
        return None


@pytest.mark.asyncio
async def test_the_agent_runs_a_skill_in_a_real_sandbox() -> None:
    driver = DockerSandboxDriver()
    session_id = f"skill-{uuid.uuid4().hex[:6]}"
    await driver.ensure_workspace(
        WorkspaceSpec(
            session_id=session_id,
            tenant_org_id="o",
            tenant_user_id="u",
            container_image=IMAGE,
            require_non_root=True,
        )
    )
    try:
        await driver.start_sandbox(session_id)
        await driver.exec_command(
            session_id, "mkdir -p /workspace/data && echo a > /workspace/data/a.txt"
        )

        async def execute_skill(name: str, args: dict[str, Any]) -> str:
            ok, out, _, _ = await run_skill_in_sandbox(driver, session_id, name, SKILL_CODE, args)
            return out if ok else f"Skill execution error: {out}"

        gateway = _CallsTheSkill()
        orchestrator = AsyncReActOrchestrator(gateway=gateway, registry=ToolRegistry())
        events = [
            event
            async for event in orchestrator.process_user_turn(
                session_id,
                "audit the data folder",
                callbacks=TurnCallbacks(execute_skill=execute_skill),
            )
        ]

        assert any(e.event_type == "tool_call" for e in events)
        (tool_message,) = [m.content for m in gateway.requests[-1].messages if m.role == "tool"]
        assert json.loads(tool_message) == {
            "files": 1,
            "ok": True,
            "uid": 1000,
            "sees_api_secret": False,
        }
    finally:
        await driver.destroy_workspace(session_id)

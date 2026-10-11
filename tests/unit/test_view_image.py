"""view_image: the agent looks at a workspace image; sandbox capabilities hide unsupported tools."""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from agent_core.context import ExecutionContext, current_execution_ctx
from agent_core.orchestrator import AsyncReActOrchestrator
from agent_core.tools import images
from agent_core.tools.registry import ToolRegistry
from sandbox_driver.fake_driver import FakeSandboxDriver

from specifications.interfaces.agent import TurnPolicy
from specifications.interfaces.llm import (
    AttachmentRef,
    LLMGatewayProtocol,
    ModelRequest,
    StreamChunk,
)
from specifications.interfaces.sandbox import ExecResult

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64
WEBP = b"RIFF\x00\x00\x00\x00WEBPVP8 " + b"\x00" * 16


class _DiskSandbox(FakeSandboxDriver):
    """Serves ``/workspace/...`` from a real directory so stat, dd and base64 run for real."""

    def __init__(self, root: Path) -> None:
        super().__init__()
        self.root = root

    async def exec_command(
        self, session_id: str, command: str, *args: Any, **kwargs: Any
    ) -> ExecResult:
        local = command.replace("/workspace", str(self.root))
        if sys.platform == "darwin":  # BSD stat has different flags than the image's GNU stat
            local = local.replace("stat -c %s", "stat -f %z")
        done = await asyncio.to_thread(
            subprocess.run,
            ["sh", "-c", local],
            capture_output=True,
            text=True,
            check=False,
        )
        return ExecResult(done.returncode, done.stdout, done.stderr, 1)


@pytest.fixture
def sandbox(tmp_path: Path) -> _DiskSandbox:
    (tmp_path / "shots").mkdir()
    (tmp_path / "shots" / "home.png").write_bytes(PNG)
    (tmp_path / "shots" / "photo.jpg").write_bytes(JPEG)
    (tmp_path / "shots" / "pic.webp").write_bytes(WEBP)
    (tmp_path / "notes.txt").write_text("not an image")
    (tmp_path / "shots" / "disguised.png").write_bytes(b"MZ not really a png")
    return _DiskSandbox(tmp_path)


@pytest.mark.parametrize(
    ("name", "mime"),
    [("home.png", "image/png"), ("photo.jpg", "image/jpeg"), ("pic.webp", "image/webp")],
)
async def test_each_supported_format_is_queued_with_its_real_type(
    sandbox: _DiskSandbox, name: str, mime: str
) -> None:
    queue: list[AttachmentRef] = []
    message = await images.load_image(sandbox, "s", f"shots/{name}", queue)
    assert message.startswith(f"Loaded {name}")
    assert [(a.mime, a.is_image) for a in queue] == [(mime, True)]
    assert queue[0].data


async def test_the_file_name_is_not_trusted(sandbox: _DiskSandbox) -> None:
    queue: list[AttachmentRef] = []
    message = await images.load_image(sandbox, "s", "shots/disguised.png", queue)
    assert message.startswith("Error: ") and "not a PNG" in message and queue == []


async def test_paths_outside_the_workspace_and_missing_files_are_refused(
    sandbox: _DiskSandbox,
) -> None:
    queue: list[AttachmentRef] = []
    assert (await images.load_image(sandbox, "s", "/etc/passwd", queue)).startswith("Error:")
    assert (await images.load_image(sandbox, "s", "../outside.png", queue)).startswith("Error:")
    assert "no file" in await images.load_image(sandbox, "s", "shots/gone.png", queue)
    assert queue == []


async def test_only_the_latest_images_are_kept_and_repeats_are_not_duplicated(
    sandbox: _DiskSandbox, tmp_path: Path
) -> None:
    queue: list[AttachmentRef] = []
    for index in range(6):
        (tmp_path / f"{index}.png").write_bytes(PNG + bytes([index]))
        await images.load_image(sandbox, "s", f"{index}.png", queue)
    assert [a.name for a in queue] == ["2.png", "3.png", "4.png", "5.png"]
    await images.load_image(sandbox, "s", "5.png", queue)
    assert [a.name for a in queue].count("5.png") == 1


# --- through the orchestrator -------------------------------------------------------------


class _ScriptedGateway(LLMGatewayProtocol):
    def __init__(self) -> None:
        self.requests: list[ModelRequest] = []

    async def chat_stream(self, request: ModelRequest) -> AsyncIterator[StreamChunk]:
        self.requests.append(request)
        seen_tool_results = [m for m in request.messages if m.role == "tool"]
        if not seen_tool_results:
            yield StreamChunk(
                tool_call_delta={
                    "index": 0,
                    "id": "call_1",
                    "function": {
                        "name": "view_image",
                        "arguments": json.dumps({"path": "shots/home.png"}),
                    },
                }
            )
            return
        yield StreamChunk(text_delta="It is a login page.")
        yield StreamChunk(is_finished=True, finish_reason="stop")

    async def check_budget(self, tenant_org_id: str, tenant_user_id: str) -> bool:
        return True

    async def record_usage(self, *args: Any, **kwargs: Any) -> None:
        return None


async def _run_turn(registry: ToolRegistry, gateway: _ScriptedGateway, session_id: str, **kw: Any):
    orchestrator = AsyncReActOrchestrator(gateway=gateway, registry=registry)
    async for _ in orchestrator.process_user_turn(session_id, "look at the page", **kw):
        pass
    return orchestrator


async def test_the_model_sees_the_image_on_its_next_step_and_it_is_never_stored(
    sandbox: _DiskSandbox,
) -> None:
    gateway = _ScriptedGateway()
    registry = ToolRegistry(driver=sandbox, session_id="sess_img")
    orchestrator = await _run_turn(registry, gateway, "sess_img")

    first, second = gateway.requests[0], gateway.requests[1]
    assert all(not m.attachments for m in first.messages)
    attached = [a for m in second.messages for a in (m.attachments or [])]
    assert [(a.name, a.is_image) for a in attached] == [("home.png", True)]
    assert all(m.attachments is None for m in orchestrator._session_histories["sess_img"])


# --- capabilities --------------------------------------------------------------------------


def _names(registry: ToolRegistry) -> set[str]:
    return {definition.name for definition in registry.get_tool_definitions()}


async def test_browser_tools_need_a_sandbox_that_declares_the_capability() -> None:
    registry = ToolRegistry(driver=FakeSandboxDriver(), session_id="s")
    assert "browser_inspect" in _names(registry), "unknown capabilities hide nothing"

    queue: asyncio.Queue[Any] = asyncio.Queue()
    without = ExecutionContext(
        session_id="s", event_queue=queue, parent_model="m", sandbox_capabilities=frozenset()
    )
    token = current_execution_ctx.set(without)
    try:
        assert "browser_inspect" not in _names(registry)
        assert "view_image" in _names(registry), "viewing is not a sandbox capability"
        blocked = await registry.call_tool("browser_inspect", {"url": "http://localhost:3000/"})
        assert blocked.startswith("Blocked:") and "'browser' capability" in blocked
    finally:
        current_execution_ctx.reset(token)

    with_browser = ExecutionContext(
        session_id="s",
        event_queue=queue,
        parent_model="m",
        sandbox_capabilities=frozenset({"browser"}),
    )
    token = current_execution_ctx.set(with_browser)
    try:
        assert "browser_inspect" in _names(registry)
    finally:
        current_execution_ctx.reset(token)


def test_the_turn_policy_carries_capabilities() -> None:
    assert TurnPolicy().sandbox_capabilities is None
    assert TurnPolicy(sandbox_capabilities=frozenset({"browser"})).sandbox_capabilities == {
        "browser"
    }

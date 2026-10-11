"""Unit tests for current-turn-only attachment dispatch.

Attachments must ride exactly one model request — the first dispatch of the turn that
carried them. Re-sending them on later ReAct steps or later turns would both waste
tokens and push volatile bytes into the provider's cached prefix, so these tests pin
the request shape rather than the UI.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from agent_core.orchestrator import AsyncReActOrchestrator
from agent_core.tools.registry import ToolRegistry
from sandbox_driver.fake_driver import FakeSandboxDriver

from specifications.interfaces.agent import SystemPrompt
from specifications.interfaces.llm import (
    AttachmentRef,
    LLMGatewayProtocol,
    ModelRequest,
    StreamChunk,
)


class RecordingGateway(LLMGatewayProtocol):
    """Gateway that records every request and always answers with a short final text."""

    def __init__(self) -> None:
        self.requests: list[ModelRequest] = []

    async def chat_stream(self, request: ModelRequest) -> AsyncIterator[StreamChunk]:
        self.requests.append(request)
        yield StreamChunk(text_delta="done")
        yield StreamChunk(
            is_finished=True,
            finish_reason="stop",
            token_usage={"prompt_tokens": 1, "completion_tokens": 1},
        )

    async def check_budget(self, tenant_org_id: str, tenant_user_id: str) -> bool:
        return True

    async def record_usage(
        self,
        tenant_org_id: str,
        tenant_user_id: str,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        cache_read_tokens: int = 0,
        cache_creation_tokens: int = 0,
    ) -> None:
        return None


def _attachment() -> AttachmentRef:
    return AttachmentRef(
        name="server.log",
        mime="text/plain",
        sha256="b" * 64,
        size=11,
        workspace_path=f"/workspace/.attachments/{'b' * 64}/server.log",
        is_image=False,
        text="ERROR boom",
    )


def _orchestrator(gateway: RecordingGateway, session_id: str) -> AsyncReActOrchestrator:
    registry = ToolRegistry(driver=FakeSandboxDriver(), session_id=session_id)
    return AsyncReActOrchestrator(gateway=gateway, registry=registry)


async def _drain(
    orchestrator: AsyncReActOrchestrator,
    session_id: str,
    prompt: str,
    *,
    attachments: list[AttachmentRef] | None = None,
    system_prompt_parts: SystemPrompt | None = None,
) -> None:
    async for _ in orchestrator.process_user_turn(
        session_id, prompt, attachments=attachments, system_prompt_parts=system_prompt_parts
    ):
        pass


@pytest.mark.asyncio
async def test_attachment_rides_only_the_first_dispatch() -> None:
    gateway = RecordingGateway()
    orchestrator = _orchestrator(gateway, "sess_attach_one")
    attachment = _attachment()

    await _drain(orchestrator, "sess_attach_one", "read the log", attachments=[attachment])

    assert len(gateway.requests) == 1
    user_messages = [m for m in gateway.requests[0].messages if m.role == "user"]
    assert user_messages[-1].attachments == [attachment]
    # The stored conversation must stay plain so the bytes can never be replayed.
    stored_users = [
        m for m in orchestrator._session_histories["sess_attach_one"] if m.role == "user"
    ]
    assert all(message.attachments is None for message in stored_users)


@pytest.mark.asyncio
async def test_later_turn_does_not_resend_attachments() -> None:
    gateway = RecordingGateway()
    orchestrator = _orchestrator(gateway, "sess_attach_two")

    await _drain(orchestrator, "sess_attach_two", "first", attachments=[_attachment()])
    gateway.requests.clear()
    await _drain(orchestrator, "sess_attach_two", "second")

    assert len(gateway.requests) == 1
    assert all(message.attachments is None for message in gateway.requests[0].messages)


@pytest.mark.asyncio
async def test_cached_prefix_is_byte_identical_across_turns_with_attachments() -> None:
    """Invariant 1: the cacheable stable prefix never absorbs per-turn attachment bytes."""
    gateway = RecordingGateway()
    orchestrator = _orchestrator(gateway, "sess_attach_cache")
    parts = SystemPrompt(stable="STABLE PREFIX", dynamic="volatile suffix")

    await _drain(
        orchestrator,
        "sess_attach_cache",
        "first",
        attachments=[_attachment()],
        system_prompt_parts=parts,
    )
    await _drain(
        orchestrator,
        "sess_attach_cache",
        "second",
        attachments=[_attachment()],
        system_prompt_parts=parts,
    )

    first_prefix = gateway.requests[0].messages[0]
    second_prefix = gateway.requests[1].messages[0]
    assert first_prefix.cacheable is True
    assert first_prefix.content == second_prefix.content
    # No attachment ever leaks into the stable prefix.
    assert first_prefix.attachments is None
    assert second_prefix.attachments is None

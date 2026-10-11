"""Central memory binding.

Long-term memory is bound once at composition and resolves the tenant from the turn's
execution context, so the web handler, the Slack assistant, webhooks and subagents all
share one implementation. This pins that behaviour (and the no-context degradation).
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from agent_core.context import ExecutionContext, current_execution_ctx
from api.services import memory as mem


class _RecordingRegistry:
    def __init__(self) -> None:
        self.remember: Any = None
        self.recall: Any = None

    def set_memory_callbacks(self, *, remember: Any, recall: Any) -> None:
        self.remember = remember
        self.recall = recall


class _FakeService:
    def __init__(self, db: Any, *, policy: Any = None, redactor: Any = None) -> None:
        self.db = db
        self.captured: dict[str, Any] = {}

    async def remember(self, **kwargs: Any) -> mem.RememberOutcome:
        self.captured["remember"] = kwargs
        return mem.RememberOutcome(status="created")

    async def recall(self, **kwargs: Any) -> list[mem.MemoryRecord]:
        self.captured["recall"] = kwargs
        return []


@pytest.mark.asyncio
async def test_callbacks_resolve_the_tenant_from_the_execution_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created: list[_FakeService] = []

    def _factory(db: Any, **kwargs: Any) -> _FakeService:
        service = _FakeService(db, **kwargs)
        created.append(service)
        return service

    monkeypatch.setattr(mem, "MemoryService", _factory)
    registry = _RecordingRegistry()
    mem.bind_context_memory_callbacks(registry, object())  # type: ignore[arg-type]

    ctx = ExecutionContext(
        session_id="s1",
        event_queue=asyncio.Queue(),
        parent_model="test-model",
        tenant_org_id="org_x",
        tenant_user_id="user_y",
    )
    token = current_execution_ctx.set(ctx)
    try:
        await registry.remember("I prefer tabs", "preference", "user")
        await registry.recall("tabs")
    finally:
        current_execution_ctx.reset(token)

    # Each call builds a fresh tenant-scoped service; aggregate what they captured.
    captured: dict[str, Any] = {}
    for service in created:
        captured.update(service.captured)
    assert captured["remember"]["org_id"] == "org_x"
    assert captured["remember"]["user_id"] == "user_y"
    assert captured["remember"]["source_session_id"] == "s1"
    assert captured["recall"]["org_id"] == "org_x"


@pytest.mark.asyncio
async def test_callbacks_degrade_without_a_turn_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(mem, "MemoryService", _FakeService)
    registry = _RecordingRegistry()
    mem.bind_context_memory_callbacks(registry, object())  # type: ignore[arg-type]

    # No execution context set (e.g. an out-of-band tool call).
    remember_result = await registry.remember("x", "fact", "user")
    recall_result = await registry.recall("x")

    assert "active turn" in remember_result
    assert recall_result == ""

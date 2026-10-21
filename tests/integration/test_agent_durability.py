"""Durable agent state and an orphan-safe tool protocol.

The model's own conversation (tool calls and results included) is persisted, so a
restarted process, a fork or another replica continues with full context; a turn that is
cancelled or crashes mid-batch can never leave a transcript that providers reject.
"""

from __future__ import annotations

import asyncio
import json
import random
from collections.abc import AsyncIterator
from typing import Any

import pytest
from agent_core.context import ContextManager
from agent_core.orchestrator import AsyncReActOrchestrator
from agent_core.tools.registry import ToolRegistry
from agent_core.transcript import (
    INTERRUPTED_TOOL_RESULT,
    RecordingList,
    repair_tool_pairs,
    slice_user_turns,
)
from api.services.transcript import InMemoryTranscriptStore
from api.store import InMemorySessionStore
from api.turns.runner import execute_turn_background

from specifications.interfaces.llm import (
    ChatMessage,
    LLMGatewayProtocol,
    StreamChunk,
    ToolDefinition,
)
from specifications.interfaces.session import SessionRecord

ORG = "org_durable"


def assert_valid_pairing(messages: list[ChatMessage]) -> None:
    """Every assistant tool call is answered, in order, and no tool result is orphaned."""
    index = 0
    while index < len(messages):
        message = messages[index]
        assert message.role != "tool", f"orphan tool result at {index}"
        index += 1
        if message.role == "assistant" and message.tool_calls:
            wanted = [call["id"] for call in message.tool_calls]
            answered = []
            while index < len(messages) and messages[index].role == "tool":
                answered.append(messages[index].tool_call_id)
                index += 1
            assert answered == wanted, f"tool calls {wanted} answered by {answered}"


class RecordingGateway(LLMGatewayProtocol):
    """Calls `bash_exec`-free tools on turn one, answers on later turns; records requests."""

    def __init__(self, script: list[tuple[str, dict[str, Any]]] | None = None) -> None:
        self.requests: list[list[ChatMessage]] = []
        self.script = script if script is not None else [("think", {"thought": "plan"})]

    async def chat_stream(self, request: Any) -> AsyncIterator[StreamChunk]:
        self.requests.append(list(request.messages))
        results = [m for m in request.messages if m.role == "tool"]
        last_user = [m for m in request.messages if m.role == "user"][-1].content or ""
        if "first" in last_user and len(results) < len(self.script):
            name, arguments = self.script[len(results)]
            yield StreamChunk(
                tool_call_delta={
                    "index": 0,
                    "id": f"call_{len(results)}",
                    "function": {"name": name, "arguments": json.dumps(arguments)},
                }
            )
            return
        yield StreamChunk(text_delta="ok")

    async def check_budget(self, tenant_org_id: str, tenant_user_id: str) -> bool:
        return True

    async def record_usage(self, *args: Any, **kwargs: Any) -> None:
        return None


async def _run(orchestrator: AsyncReActOrchestrator, session_id: str, text: str) -> list[Any]:
    return [
        event
        async for event in orchestrator.process_user_turn(
            session_id, text, tenant_org_id=ORG, tenant_user_id="u"
        )
    ]


def _orchestrator(
    store: InMemoryTranscriptStore, gateway: LLMGatewayProtocol
) -> AsyncReActOrchestrator:
    return AsyncReActOrchestrator(gateway=gateway, registry=ToolRegistry(), transcript_store=store)


@pytest.mark.asyncio
async def test_a_restarted_process_continues_with_the_full_tool_context() -> None:
    store = InMemoryTranscriptStore()
    await _run(_orchestrator(store, RecordingGateway()), "s1", "first request")

    restarted_gateway = RecordingGateway()
    await _run(_orchestrator(store, restarted_gateway), "s1", "second request")

    seen = restarted_gateway.requests[-1]
    roles = [m.role for m in seen if m.role != "system"]
    assert roles == ["user", "assistant", "tool", "assistant", "user"]
    assert any(m.tool_calls for m in seen), "earlier tool call lost after restart"
    assert [m.content for m in seen if m.role == "user"] == ["first request", "second request"]
    assert_valid_pairing(seen)


@pytest.mark.asyncio
async def test_fork_carries_the_parents_context_up_to_the_chosen_turn() -> None:
    store = InMemoryTranscriptStore()
    orchestrator = _orchestrator(store, RecordingGateway())
    await _run(orchestrator, "parent", "first request")
    await _run(orchestrator, "parent", "second request")

    copied = await store.copy_prefix("parent", "child", ORG, user_turns=1)
    assert copied > 0

    gateway = RecordingGateway()
    await _run(_orchestrator(store, gateway), "child", "branch request")
    users = [m.content for m in gateway.requests[-1] if m.role == "user"]
    assert users == ["first request", "branch request"]
    assert_valid_pairing(gateway.requests[-1])


@pytest.mark.asyncio
async def test_cancel_mid_batch_leaves_no_orphan_tool_calls() -> None:
    store = InMemoryTranscriptStore()
    registry = ToolRegistry()
    started = asyncio.Event()

    async def hang() -> str:
        started.set()
        await asyncio.sleep(60)
        return "never"

    registry.register_dynamic_tool(
        ToolDefinition(name="hang", description="Never returns.", parameters_json_schema={}),
        hang,
    )
    gateway = RecordingGateway(script=[("hang", {})])
    orchestrator = AsyncReActOrchestrator(
        gateway=gateway, registry=registry, transcript_store=store
    )

    async def consume() -> None:
        async for _ in orchestrator.process_user_turn(
            "s2", "first request", tenant_org_id=ORG, tenant_user_id="u"
        ):
            pass

    task = asyncio.create_task(consume())
    await asyncio.wait_for(started.wait(), timeout=5)
    await orchestrator.cancel_turn("s2")
    await asyncio.wait_for(asyncio.gather(task, return_exceptions=True), timeout=10)

    persisted = await store.load("s2", ORG)
    assert_valid_pairing(persisted)
    assert any(m.content == INTERRUPTED_TOOL_RESULT for m in persisted if m.role == "tool")

    follow_up = RecordingGateway()
    await _run(_orchestrator(store, follow_up), "s2", "second request")
    assert_valid_pairing(follow_up.requests[-1])


@pytest.mark.asyncio
async def test_invalid_tool_arguments_become_an_error_result_not_a_crash() -> None:
    class BadArgsGateway(RecordingGateway):
        async def chat_stream(self, request: Any) -> AsyncIterator[StreamChunk]:
            self.requests.append(list(request.messages))
            if not any(m.role == "tool" for m in request.messages):
                yield StreamChunk(
                    tool_call_delta={
                        "index": 0,
                        "id": None,  # providers may omit ids
                        "function": {"name": "think", "arguments": "{not json"},
                    }
                )
                return
            yield StreamChunk(text_delta="recovered")

    gateway = BadArgsGateway()
    store = InMemoryTranscriptStore()
    events = await _run(_orchestrator(store, gateway), "s3", "first request")

    completed = [e for e in events if e.event_type == "tool_completed"]
    assert completed and completed[0].payload["status"] == "error"
    persisted = await store.load("s3", ORG)
    assert_valid_pairing(persisted)
    call = next(m for m in persisted if m.tool_calls).tool_calls[0]  # type: ignore[index]
    assert call["id"], "a missing provider id must be replaced consistently"


@pytest.mark.asyncio
async def test_provider_failure_is_a_failed_turn_not_an_answer() -> None:
    class DownGateway(RecordingGateway):
        async def chat_stream(self, request: Any) -> AsyncIterator[StreamChunk]:
            yield StreamChunk(is_finished=True, finish_reason="error", error="provider is down")

    events = await _run(_orchestrator(InMemoryTranscriptStore(), DownGateway()), "s4", "hello")
    statuses = [e.payload for e in events if e.event_type == "status"]
    assert any(p.get("status") == "error" and "provider is down" in p["error"] for p in statuses)
    assert not [e for e in events if e.event_type == "thought" and not e.payload["is_reasoning"]]


class _Bus:
    def __init__(self) -> None:
        self.events: list[Any] = []

    async def publish(self, event: Any) -> None:
        self.events.append(event)


@pytest.mark.asyncio
async def test_turn_handler_records_a_provider_failure_as_error() -> None:
    class DownGateway(RecordingGateway):
        async def chat_stream(self, request: Any) -> AsyncIterator[StreamChunk]:
            yield StreamChunk(is_finished=True, finish_reason="error", error="provider is down")

    store = InMemorySessionStore()
    await store.create_session(
        SessionRecord(
            session_id="s5", tenant_org_id="default_org", tenant_user_id="dev_user", title="t"
        )
    )
    bus = _Bus()
    orchestrator = _orchestrator(InMemoryTranscriptStore(), DownGateway())
    await execute_turn_background(
        session_id="s5",
        prompt="hello",
        orchestrator=orchestrator,
        event_bus=bus,  # type: ignore[arg-type]
        driver=None,
        registry=None,
        store=store,
    )
    kinds = [(e.event_type.value, e.payload.get("status")) for e in bus.events]
    assert ("error", None) in kinds
    assert ("status_changed", "error") in kinds
    assert ("status_changed", "completed") not in kinds
    record = await store.get_session("s5")
    assert record is not None
    assert "provider is down" in (record.conversation_history[-1].content or "")


@pytest.mark.asyncio
async def test_resident_conversations_are_bounded_and_evicted_ones_rehydrate() -> None:
    store = InMemoryTranscriptStore()
    orchestrator = _orchestrator(store, RecordingGateway())
    orchestrator.max_cached_sessions = 2
    for n in range(5):
        await _run(orchestrator, f"s{n}", "first request")
    assert len(orchestrator._session_histories) <= 2

    gateway = RecordingGateway()
    follow = _orchestrator(store, gateway)
    await _run(follow, "s0", "second request")
    assert [m.content for m in gateway.requests[-1] if m.role == "user"] == [
        "first request",
        "second request",
    ]


def _sample_transcript() -> list[ChatMessage]:
    messages: list[ChatMessage] = [ChatMessage(role="user", content="start")]
    for turn in range(6):
        ids = [f"c{turn}_{k}" for k in range(1 + turn % 3)]
        messages.append(
            ChatMessage(
                role="assistant",
                tool_calls=[
                    {"id": i, "type": "function", "function": {"name": "think", "arguments": "{}"}}
                    for i in ids
                ],
            )
        )
        messages.extend(ChatMessage(role="tool", content=f"r{i}", tool_call_id=i) for i in ids)
        messages.append(ChatMessage(role="assistant", content=f"step {turn}"))
        messages.append(ChatMessage(role="user", content=f"next {turn}"))
    return messages


def test_repair_makes_every_truncation_point_valid() -> None:
    transcript = _sample_transcript()
    for cut in range(len(transcript) + 1):
        assert_valid_pairing(repair_tool_pairs(transcript[:cut]))
        assert_valid_pairing(repair_tool_pairs(transcript[cut:]))


def test_repair_drops_orphan_results_and_fills_missing_ones() -> None:
    broken = [
        ChatMessage(role="tool", content="orphan", tool_call_id="x"),
        ChatMessage(role="user", content="hi"),
        ChatMessage(
            role="assistant",
            tool_calls=[
                {"id": "a", "type": "function", "function": {"name": "t", "arguments": "{}"}},
                {"id": "b", "type": "function", "function": {"name": "t", "arguments": "{}"}},
            ],
        ),
        ChatMessage(role="tool", content="only b", tool_call_id="b"),
    ]
    repaired = repair_tool_pairs(broken)
    assert_valid_pairing(repaired)
    assert [m.content for m in repaired if m.role == "tool"] == [INTERRUPTED_TOOL_RESULT, "only b"]


@pytest.mark.asyncio
async def test_compaction_never_splits_a_tool_call_from_its_result() -> None:
    manager = ContextManager()
    rng = random.Random(7)
    for _ in range(40):
        transcript = _sample_transcript()
        keep = rng.randint(1, 5)
        compacted = await manager.compact_with_summary(
            transcript, None, recent_turns_to_preserve=keep
        )
        assert_valid_pairing(compacted)


@pytest.mark.asyncio
async def test_model_summary_replaces_the_compacted_middle() -> None:
    manager = ContextManager()

    async def summarize(request: list[ChatMessage]) -> str:
        return (
            f"## Objective\n- summary of {len(request)} messages\n\n## Next Move\n1. carry on"
            + "\n- detail" * 20
        )

    compacted = await manager.compact_with_summary(
        _sample_transcript(), summarize, recent_turns_to_preserve=2
    )
    assert any("summary of" in (m.content or "") for m in compacted if m.role == "user")
    assert all(m.role != "system" for m in compacted[1:]), "only the leading message is system"
    assert_valid_pairing(compacted)

    async def broken(_: list[ChatMessage]) -> str:
        raise RuntimeError("summarizer down")

    fallback = await manager.compact_with_summary(
        _sample_transcript(), broken, recent_turns_to_preserve=2
    )
    bridge = "\n".join(m.content or "" for m in fallback if m.role == "user")
    assert "Compacted" in bridge and "Rebuilt from the tool history" in bridge


def test_recording_list_tracks_appends_but_not_compaction_rewrites() -> None:
    conversation = RecordingList()
    conversation.append(ChatMessage(role="user", content="a"))
    conversation.append(ChatMessage(role="assistant", content="b"))
    assert len(conversation.take_pending()) == 2
    del conversation[:]
    conversation.extend([ChatMessage(role="system", content="bridge")])
    conversation[:] = repair_tool_pairs(conversation)
    assert conversation.pending == []


def test_slice_user_turns() -> None:
    transcript = _sample_transcript()
    assert slice_user_turns(transcript, None) == transcript
    assert sum(1 for m in slice_user_turns(transcript, 2) if m.role == "user") == 2
    assert_valid_pairing(slice_user_turns(transcript, 3))

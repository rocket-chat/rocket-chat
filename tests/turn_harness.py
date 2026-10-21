"""Run one real turn in memory: real orchestrator, turn engine, event bus and store.

Only the model is scripted and the sandbox is the fake driver, so a test can configure an
organization with ``save_org_config`` and observe what the turn actually did: the requests the
model received, the commands the sandbox ran, the events a client would see.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from agent_core.orchestrator import AsyncReActOrchestrator
from agent_core.summarizer import FLATTENED_SUMMARY_SYSTEM
from agent_core.tools.registry import ToolRegistry
from api.db.session import current_tenant_org_id
from api.events import AsyncIOEventBus
from api.services.compaction import InMemoryCompactionStore
from api.services.transcript import InMemoryTranscriptStore
from api.store import InMemorySessionStore
from api.turns.context import TurnSelection
from api.turns.runner import execute_turn_background
from sandbox_driver.fake_driver import FakeSandboxDriver

from specifications.interfaces.llm import LLMGatewayProtocol, StreamChunk, current_model_scope
from specifications.interfaces.session import SessionRecord

SCRIPTED_SUMMARY = (
    "## Objective\n- the scripted task\n\n## Work State\n### Completed\n- scripted steps\n\n"
    "## Next Move\n1. continue the script\n\n## Important Context\n- scripted summary"
)


def _is_summary_request(request: Any) -> bool:
    if getattr(request, "tool_choice", None) == "none":
        return True
    messages = getattr(request, "messages", None) or []
    return bool(messages) and messages[0].content == FLATTENED_SUMMARY_SYSTEM


@dataclass
class ScriptedModel(LLMGatewayProtocol):
    """Calls the scripted tools in order, then answers with ``final_text``."""

    script: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    final_text: str = "All done."
    requests: list[Any] = field(default_factory=list)
    scopes: list[Any] = field(default_factory=list)
    issued: int = 0
    # Replies that are tool-call markup written as text, returned (in small pieces, as a
    # provider streams them) before the script starts.
    leaks: list[str] = field(default_factory=list)
    leaked: int = 0
    steps: list[Any] = field(default_factory=list)
    step_index: int = 0
    summary_text: str = SCRIPTED_SUMMARY
    summary_requests: int = 0

    @property
    def last_request(self) -> Any:
        return self.requests[-1] if self.requests else None

    def request_notes(self, index: int = -1) -> str:
        if not self.requests or abs(index) > len(self.requests):
            return ""
        req = self.requests[index]
        if not getattr(req, "messages", None):
            return ""
        last = req.messages[-1]
        return (last.content or "") if last.role == "user" else ""

    async def chat_stream(self, request: Any) -> AsyncIterator[StreamChunk]:
        self.requests.append(request)
        self.scopes.append(current_model_scope.get())
        if _is_summary_request(request):
            # A compaction's summary, not a step: it consumes no step of the script.
            self.summary_requests += 1
            yield StreamChunk(text_delta=self.summary_text, is_finished=True)
            return
        if self.leaked < len(self.leaks):
            text = self.leaks[self.leaked]
            self.leaked += 1
            for start in range(0, len(text), 5):
                yield StreamChunk(text_delta=text[start : start + 5])
            return

        if self.steps and self.step_index < len(self.steps):
            step = self.steps[self.step_index]
            self.step_index += 1
            if isinstance(step, tuple):
                kind = step[0]
                if kind == "call":
                    name = step[1]
                    arguments = step[2]
                    usage = step[3] if len(step) > 3 else None
                    yield StreamChunk(
                        tool_call_delta={
                            "index": 0,
                            "id": f"call_{self.step_index}",
                            "function": {
                                "name": name,
                                "arguments": json.dumps(arguments)
                                if isinstance(arguments, dict)
                                else str(arguments),
                            },
                        }
                    )
                    yield StreamChunk(
                        is_finished=True, finish_reason="tool_calls", token_usage=usage
                    )
                    return
                if kind == "think_and_call":
                    # Reasoning, then a tool call, in one reply.
                    yield StreamChunk(reasoning_delta=step[1])
                    yield StreamChunk(
                        tool_call_delta={
                            "index": 0,
                            "id": f"call_{self.step_index}",
                            "function": {"name": step[2], "arguments": json.dumps(step[3])},
                        }
                    )
                    yield StreamChunk(is_finished=True, finish_reason="tool_calls")
                    return
                if kind == "say_and_call":
                    # Text, then a tool call, in one reply (as a model's preface to a step).
                    yield StreamChunk(text_delta=step[1])
                    yield StreamChunk(
                        tool_call_delta={
                            "index": 0,
                            "id": f"call_{self.step_index}",
                            "function": {"name": step[2], "arguments": json.dumps(step[3])},
                        }
                    )
                    yield StreamChunk(is_finished=True, finish_reason="tool_calls")
                    return
                if kind == "think":
                    finish_reason = step[1]
                    reasoning_text = (
                        step[2] if len(step) > 2 else "Analyzing deep reasoning trace... " * 20
                    )
                    usage = step[3] if len(step) > 3 else None
                    yield StreamChunk(reasoning_delta=reasoning_text)
                    yield StreamChunk(
                        is_finished=True, finish_reason=finish_reason, token_usage=usage
                    )
                    return
                if kind == "say":
                    text = step[1]
                    usage = step[2] if len(step) > 2 else None
                    yield StreamChunk(text_delta=text)
                    yield StreamChunk(is_finished=True, finish_reason="stop", token_usage=usage)
                    return
                if kind == "empty":
                    finish_reason = step[1]
                    usage = step[2] if len(step) > 2 else None
                    yield StreamChunk(
                        is_finished=True, finish_reason=finish_reason, token_usage=usage
                    )
                    return

        if request.tools and self.issued < len(self.script):
            name, arguments = self.script[self.issued]
            self.issued += 1
            yield StreamChunk(
                tool_call_delta={
                    "index": 0,
                    "id": f"call_{self.issued}",
                    "function": {"name": name, "arguments": json.dumps(arguments)},
                }
            )
            return
        yield StreamChunk(text_delta=self.final_text)

    async def check_budget(self, tenant_org_id: str, tenant_user_id: str) -> bool:
        return True

    async def record_usage(self, *args: Any, **kwargs: Any) -> None:
        return None


@dataclass
class TurnOutcome:
    session: SessionRecord
    events: list[Any]
    model: ScriptedModel
    driver: FakeSandboxDriver

    @property
    def last_request(self) -> Any:
        return self.model.last_request

    def request_notes(self, index: int = -1) -> str:
        return self.model.request_notes(index)

    def events_of(self, event_type: str) -> list[Any]:
        return [e for e in self.events if e.event_type.value == event_type]

    def text_of_events(self) -> str:
        return json.dumps([e.payload for e in self.events], default=str)


async def run_turn(
    org: str,
    *,
    script: list[tuple[str, dict[str, Any]]] | None = None,
    steps: list[Any] | None = None,
    prompt: str = "Please go ahead.",
    metadata: dict[str, Any] | None = None,
    driver: FakeSandboxDriver | None = None,
    user: str = "u1",
    final_text: str = "All done.",
    git_engine: Any = None,
    leaks: list[str] | None = None,
    store: InMemorySessionStore | None = None,
    selection: TurnSelection | None = None,
    model_choice: str | None = None,
    synthetic_prompt: bool = False,
    transcript_store: Any = None,
    compaction_store: Any = None,
) -> TurnOutcome:
    """Run a web-cockpit style turn for ``org`` and return what happened."""
    session_id = f"session_{org}"
    store, bus = store or InMemorySessionStore(), AsyncIOEventBus()
    driver = driver or FakeSandboxDriver()
    model = ScriptedModel(
        script=script or [],
        steps=steps or [],
        final_text=final_text,
        leaks=leaks or [],
    )
    registry = ToolRegistry(driver=driver, session_id=session_id)
    if git_engine is not None:
        registry.bind_git_engine(git_engine)
    orchestrator = AsyncReActOrchestrator(
        gateway=model,
        registry=registry,
        transcript_store=transcript_store or InMemoryTranscriptStore(),
        compaction_store=compaction_store or InMemoryCompactionStore(),
    )
    token = current_tenant_org_id.set(org)
    try:
        # A second turn on the same store continues the session instead of starting it over.
        if await store.get_session(session_id) is None:
            await store.create_session(
                SessionRecord(
                    session_id=session_id,
                    tenant_org_id=org,
                    tenant_user_id=user,
                    title="harness",
                    metadata=metadata or {},
                )
            )
        await execute_turn_background(
            session_id=session_id,
            prompt=prompt,
            orchestrator=orchestrator,
            event_bus=bus,
            driver=driver,
            registry=registry,
            store=store,
            git_engine=git_engine,
            selection=selection,
            model=model_choice,
            synthetic_prompt=synthetic_prompt,
        )
        events, _ = bus.replay_since(session_id, 0)
        record = await store.get_session(session_id)
        assert record is not None
        return TurnOutcome(session=record, events=events, model=model, driver=driver)
    finally:
        current_tenant_org_id.reset(token)

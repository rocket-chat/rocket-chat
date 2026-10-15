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
from agent_core.tools.registry import ToolRegistry
from api.db.session import current_tenant_org_id
from api.events import AsyncIOEventBus
from api.services.transcript import InMemoryTranscriptStore
from api.store import InMemorySessionStore
from api.turns.runner import execute_turn_background
from sandbox_driver.fake_driver import FakeSandboxDriver

from specifications.interfaces.llm import LLMGatewayProtocol, StreamChunk, current_model_scope
from specifications.interfaces.session import SessionRecord


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

    async def chat_stream(self, request: Any) -> AsyncIterator[StreamChunk]:
        self.requests.append(request)
        self.scopes.append(current_model_scope.get())
        if self.leaked < len(self.leaks):
            text = self.leaks[self.leaked]
            self.leaked += 1
            for start in range(0, len(text), 5):
                yield StreamChunk(text_delta=text[start : start + 5])
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

    def events_of(self, event_type: str) -> list[Any]:
        return [e for e in self.events if e.event_type.value == event_type]

    def text_of_events(self) -> str:
        return json.dumps([e.payload for e in self.events], default=str)


async def run_turn(
    org: str,
    *,
    script: list[tuple[str, dict[str, Any]]] | None = None,
    prompt: str = "Please go ahead.",
    metadata: dict[str, Any] | None = None,
    driver: FakeSandboxDriver | None = None,
    user: str = "u1",
    final_text: str = "All done.",
    git_engine: Any = None,
    leaks: list[str] | None = None,
    store: InMemorySessionStore | None = None,
) -> TurnOutcome:
    """Run a web-cockpit style turn for ``org`` and return what happened."""
    session_id = f"session_{org}"
    store, bus = store or InMemorySessionStore(), AsyncIOEventBus()
    driver = driver or FakeSandboxDriver()
    model = ScriptedModel(script=script or [], final_text=final_text, leaks=leaks or [])
    registry = ToolRegistry(driver=driver, session_id=session_id)
    if git_engine is not None:
        registry.bind_git_engine(git_engine)
    orchestrator = AsyncReActOrchestrator(
        gateway=model, registry=registry, transcript_store=InMemoryTranscriptStore()
    )
    await store.create_session(
        SessionRecord(
            session_id=session_id,
            tenant_org_id=org,
            tenant_user_id=user,
            title="harness",
            metadata=metadata or {},
        )
    )
    token = current_tenant_org_id.set(org)
    try:
        await execute_turn_background(
            session_id=session_id,
            prompt=prompt,
            orchestrator=orchestrator,
            event_bus=bus,
            driver=driver,
            registry=registry,
            store=store,
            git_engine=git_engine,
        )
    finally:
        current_tenant_org_id.reset(token)
    events, _ = bus.replay_since(session_id, 0)
    record = await store.get_session(session_id)
    assert record is not None
    return TurnOutcome(session=record, events=events, model=model, driver=driver)

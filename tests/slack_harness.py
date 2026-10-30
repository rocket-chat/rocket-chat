"""Test harness: a SlackAssistant wired to a scripted orchestrator through the real event bus.

In production the Slack assistant starts a turn through the shared turn pipeline and renders
the events that pipeline publishes on the session bus. These tests exercise Slack's own
concerns (admission, rendering, gates) with a scripted orchestrator standing in for the
model: ``OrchestratorPipeline`` plays the role of ``run_guarded_turn``, translating the
orchestrator's events onto a real ``AsyncIOEventBus`` exactly as the turn handler does.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from typing import Any

from api.events import AsyncIOEventBus
from slack_assistant.assistant import SlackAssistant

from specifications.interfaces.agent import (
    AgentBinding,
    AgentEvent,
    AgentOrchestratorProtocol,
    SystemPrompt,
    TurnOverlay,
)
from specifications.interfaces.events import EventType, SessionEvent
from specifications.interfaces.llm import ChatMessage
from specifications.interfaces.session import SessionStoreProtocol

_DIRECT = {
    "tool_call": EventType.TOOL_STARTED,
    "file_diff": EventType.FILE_DIFF,
    "interactive_question": EventType.DECISION_REQUIRED,
    "status": EventType.STATUS_CHANGED,
}


def to_bus_event(session_id: str, event: AgentEvent) -> SessionEvent | None:
    """Mirror of the translation ``execute_turn_background`` applies to orchestrator events."""
    if event.event_type == "thought":
        if event.payload.get("is_reasoning"):
            return None
        return SessionEvent(session_id, EventType.TOKEN, {"delta": event.payload.get("delta", "")})
    if event.event_type == "approval_required":
        return SessionEvent(
            session_id,
            EventType.APPROVAL_REQUIRED,
            {"policy_decision": "require_human", **event.payload},
        )
    if event.event_type == "tool_call":
        raw = event.payload.get("arguments", "{}")
        import json

        arguments = raw if isinstance(raw, dict) else json.loads(raw or "{}")
        return SessionEvent(
            session_id, EventType.TOOL_STARTED, {**event.payload, "arguments": arguments}
        )
    mapped = _DIRECT.get(event.event_type)
    return SessionEvent(session_id, mapped, dict(event.payload)) if mapped else None


class OrchestratorPipeline:
    """Runs a turn on ``orchestrator`` and publishes it on the bus like the real handler."""

    def __init__(
        self,
        orchestrator: AgentOrchestratorProtocol,
        bus: AsyncIOEventBus,
        binding: AgentBinding | None = None,
        store: SessionStoreProtocol | None = None,
    ) -> None:
        self.orchestrator = orchestrator
        self.bus = bus
        self.binding = binding
        self.store = store
        self.calls: list[dict[str, Any]] = []
        # One writer per session, as ``run_guarded_turn``'s session lock: a second message
        # waits for the turn before it.
        self._locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    async def __call__(
        self,
        *,
        session_id: str,
        prompt: str,
        overlay: TurnOverlay,
        author_user_id: str,
        message_id: str | None = None,
    ) -> None:
        self.calls.append(
            {
                "session_id": session_id,
                "prompt": prompt,
                "overlay": overlay,
                "author": author_user_id,
            }
        )
        async with self._locks[session_id]:
            await self._run(session_id, prompt, overlay, author_user_id, message_id)

    async def _store(self, session_id: str, message: ChatMessage) -> None:
        if self.store is None:
            return
        record = await self.store.get_session(session_id)
        if record is not None:
            await self.store.mutate_session(
                session_id,
                record.tenant_org_id,
                lambda r: r.conversation_history.append(message),
            )

    async def _run(
        self,
        session_id: str,
        prompt: str,
        overlay: TurnOverlay,
        author_user_id: str,
        message_id: str | None,
    ) -> None:
        await self._store(session_id, ChatMessage(role="user", content=prompt))
        await self.bus.publish(
            SessionEvent(
                session_id,
                EventType.USER_MESSAGE,
                {
                    "message_id": message_id or "m",
                    "role": "user",
                    "content": prompt,
                    "author_user_id": author_user_id,
                },
            )
        )
        await self.bus.publish(
            SessionEvent(
                session_id,
                EventType.STATUS_CHANGED,
                {
                    "status": "running",
                    "message": "Analyzing instruction...",
                    "turn_start": True,
                    **({"message_id": message_id} if message_id else {}),
                },
            )
        )
        answer: list[str] = []
        binding = self.binding
        persona = binding.system_prompt if binding and binding.system_prompt else ""
        tools = binding.whitelisted_tools if binding else None
        if overlay.tool_scope is not None:
            tools = overlay.tool_scope(tools)
        try:
            async for event in self.orchestrator.process_user_turn(
                session_id,
                prompt,
                model=binding.model if binding else None,
                system_prompt_parts=SystemPrompt(
                    stable=f"{persona}\n{overlay.stable_suffix}".strip(), dynamic=overlay.dynamic
                ),
                whitelisted_tools=tools,
                denied_tools=overlay.denied_tools,
                max_turns=binding.max_turns if binding else None,
                enabled_toolsets=overlay.enabled_toolsets,
            ):
                translated = to_bus_event(session_id, event)
                if translated is None:
                    continue
                if translated.event_type is EventType.TOOL_STARTED:
                    answer.clear()
                elif translated.event_type is EventType.TOKEN:
                    answer.append(str(translated.payload.get("delta", "")))
                await self.bus.publish(translated)
        except Exception as err:
            await self.bus.publish(SessionEvent(session_id, EventType.ERROR, {"error": str(err)}))
            await self.bus.publish(
                SessionEvent(
                    session_id,
                    EventType.STATUS_CHANGED,
                    {"status": "error", "message": f"The turn failed: {err}", "final": True},
                )
            )
            return
        # As the runner: the answer is stored, then the closing event says so.
        await self._store(session_id, ChatMessage(role="assistant", content="".join(answer)))
        await self.bus.publish(
            SessionEvent(
                session_id,
                EventType.STATUS_CHANGED,
                {"status": "completed", "message": "Agent turn complete.", "final": True},
            )
        )


def make_slack(
    orchestrator: AgentOrchestratorProtocol,
    *,
    binding: AgentBinding | None = None,
    **kwargs: Any,
) -> SlackAssistant:
    """A SlackAssistant whose turns run ``orchestrator`` through the shared-pipeline contract."""
    bus = AsyncIOEventBus()
    pipeline = OrchestratorPipeline(orchestrator, bus, binding, kwargs.get("session_store"))
    assistant = SlackAssistant(
        orchestrator=orchestrator, event_bus=bus, run_turn_fn=pipeline, **kwargs
    )
    assistant.pipeline = pipeline  # type: ignore[attr-defined]
    return assistant

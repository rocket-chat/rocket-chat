"""Slack runs on the same turn pipeline as the web cockpit.

These use the real orchestrator, turn handler, event bus and session store; only the model
is scripted. They prove a Slack thread is a first-class session (persisted, persona-bound,
tool-scoped), and that questions/approvals can be answered only by the person who asked.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import AsyncMock

import pytest
from agent_core.orchestrator import AsyncReActOrchestrator
from agent_core.tools.registry import ToolRegistry
from api.events import AsyncIOEventBus
from api.services.metrics import build_backpressure
from api.services.settings import save_org_config
from api.services.transcript import InMemoryTranscriptStore
from api.store import InMemorySessionStore
from api.turns.tasks import run_guarded_turn
from slack_assistant.assistant import SlackAssistant
from slack_assistant.config import SlackAssistantSettings
from slack_assistant.policy import SlackOrgPolicy

from specifications.interfaces.agent import TurnOverlay
from specifications.interfaces.llm import LLMGatewayProtocol, StreamChunk
from specifications.interfaces.session import SessionSource

THREAD = ("C1", "1.0")
SESSION_ID = "slack_C1_1_0"


@dataclass
class ScriptedModel(LLMGatewayProtocol):
    """Replays tool calls then a final answer built from the tool results it saw."""

    script: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    requests: list[Any] = field(default_factory=list)
    gate: asyncio.Event | None = None
    started: asyncio.Event = field(default_factory=asyncio.Event)
    final_text: str | None = None

    def agent_requests(self) -> list[Any]:
        """Requests of the ReAct loop (they carry tools); auxiliary calls (titles, summaries) do not."""
        return [r for r in self.requests if r.tools]

    async def chat_stream(self, request: Any) -> AsyncIterator[StreamChunk]:
        self.requests.append(request)
        self.started.set()
        if self.gate is not None:
            await self.gate.wait()
        results = [m.content for m in request.messages if m.role == "tool"]
        if len(results) < len(self.script):
            name, arguments = self.script[len(results)]
            yield StreamChunk(
                tool_call_delta={
                    "index": 0,
                    "id": f"call_{len(results)}",
                    "function": {"name": name, "arguments": json.dumps(arguments)},
                }
            )
            return
        yield StreamChunk(
            text_delta=self.final_text or "DONE " + " | ".join(str(r) for r in results)
        )

    async def check_budget(self, tenant_org_id: str, tenant_user_id: str) -> bool:
        return True

    async def record_usage(self, *args: Any, **kwargs: Any) -> None:
        return None


class Stack:
    def __init__(
        self, model: ScriptedModel, policy: SlackOrgPolicy | None = None, app: Any = None
    ) -> None:
        self.model = model
        self.bus = AsyncIOEventBus()
        self.store = InMemorySessionStore()
        self.registry = ToolRegistry()
        self.orchestrator = AsyncReActOrchestrator(
            gateway=model, registry=self.registry, transcript_store=InMemoryTranscriptStore()
        )
        self.gate, self.locks = build_backpressure()
        self.say = AsyncMock()
        org_policy = policy or SlackOrgPolicy()

        async def resolve_policy(_org: str) -> SlackOrgPolicy:
            return org_policy

        async def run_turn(
            *, session_id: str, prompt: str, overlay: TurnOverlay, author_user_id: str
        ) -> None:
            await run_guarded_turn(
                session_id=session_id,
                prompt=prompt,
                orchestrator=self.orchestrator,
                event_bus=self.bus,
                driver=None,
                registry=self.registry,
                store=self.store,
                author_user_id=author_user_id,
                concurrency_gate=self.gate,
                session_locks=self.locks,
                overlay=overlay,
            )

        self.assistant = SlackAssistant(
            orchestrator=self.orchestrator,
            settings=SlackAssistantSettings(
                slack_user_turns_per_minute=0, slack_channel_turns_per_minute=0
            ),
            session_store=self.store,
            tool_registry=self.registry,
            event_bus=self.bus,
            run_turn_fn=run_turn,
            resolve_slack_policy_fn=resolve_policy,
            app=app,
        )
        self.assistant._resolve_slack_user_name = AsyncMock(return_value="Alice")  # type: ignore[method-assign]

    def send(self, text: str = "<@UBOT> go", user: str = "U1", thread_ts: str = THREAD[1]) -> Any:
        return asyncio.create_task(
            self.assistant.process_incoming_request(
                channel_id=THREAD[0],
                thread_ts=thread_ts,
                raw_text=text,
                say_fn=self.say,
                user_id=user,
                channel_type="channel",
                team_id="T1",
            )
        )

    def posted(self) -> str:
        return " ".join(json.dumps(c.kwargs, default=str) for c in self.say.call_args_list)


def _gate_body(value: str, user: str) -> dict[str, Any]:
    return {
        "actions": [{"value": value}],
        "channel": {"id": THREAD[0]},
        "container": {"thread_ts": THREAD[1]},
        "user": {"id": user},
    }


async def _wait_for(predicate: Any, seconds: float = 5.0) -> None:
    async with asyncio.timeout(seconds):
        while not predicate():  # noqa: ASYNC110 - polling a plain predicate in a test
            await asyncio.sleep(0.01)


@pytest.mark.asyncio
async def test_a_slack_thread_is_a_persisted_cockpit_visible_session() -> None:
    stack = Stack(ScriptedModel())
    await stack.send()

    record = await stack.store.get_session(SESSION_ID)
    assert record is not None
    assert record.source == SessionSource.SLACK
    assert record.metadata["toolsets"] == ["slack"]
    # A shared channel thread belongs to the workspace, never to the member who spoke first,
    # so no member's credentials are used in it.
    assert record.tenant_user_id == "slack_global"
    assert record.is_shared
    roles = [m.role for m in record.conversation_history]
    assert roles == ["user", "assistant"]
    assert "[Speaker: Alice (<@U1>)]" in (record.conversation_history[0].content or "")
    assert "DONE" in stack.posted()


@pytest.mark.asyncio
async def test_uploaded_files_reach_the_model_as_attachments() -> None:
    from specifications.interfaces.llm import AttachmentRef

    async def ingest(**kwargs: Any) -> AttachmentRef:
        return AttachmentRef(
            name=str(kwargs["name"]),
            mime="text/plain",
            sha256="abc",
            size=11,
            workspace_path="/workspace/.attachments/abc/server.log",
            text="boom: error",
        )

    class Downloader:
        async def download(self, file: Any) -> bytes:
            return b"boom: error"

    model = ScriptedModel()
    stack = Stack(model)
    stack.assistant._ingest_attachment_fn = ingest
    stack.assistant._file_downloader = Downloader()  # type: ignore[assignment]
    await asyncio.wait_for(
        stack.assistant.process_incoming_request(
            channel_id=THREAD[0],
            thread_ts=THREAD[1],
            raw_text="<@UBOT> read this",
            say_fn=stack.say,
            user_id="U1",
            channel_type="channel",
            team_id="T1",
            files=[
                {"id": "F1", "name": "server.log", "mimetype": "text/plain", "url_private": "u"}
            ],
        ),
        5,
    )
    first = model.agent_requests()[0]
    attached = [a for m in first.messages for a in (m.attachments or [])]
    assert [a.name for a in attached] == ["server.log"]


@pytest.mark.asyncio
async def test_the_org_persona_and_tool_scope_apply_to_slack_turns() -> None:
    model = ScriptedModel()
    stack = Stack(
        model,
        SlackOrgPolicy(
            default_agent_persona="persona-security", allowed_tools=("file_read", "web_search")
        ),
    )
    await stack.send()

    request = model.agent_requests()[0]
    system_text = "\n".join(m.content or "" for m in request.messages if m.role == "system")
    assert "Security Auditor persona" in system_text  # the bound persona's prompt
    assert "Slack thread" in system_text  # the Slack overlay on top of it
    # persona whitelist (file_read, web_search, web_fetch) intersected with the org's Slack scope
    assert {t.name for t in request.tools} == {"file_read", "web_search"}


@pytest.mark.asyncio
async def test_only_the_requester_may_approve_and_a_stranger_is_refused() -> None:
    model = ScriptedModel(
        script=[
            (
                "request_approval",
                {"action_type": "deploy", "description": "ship it", "risk_level": "high"},
            )
        ]
    )
    stack = Stack(model)
    task = stack.send(user="U1")
    await _wait_for(lambda: "approve_action" in stack.posted())

    action_id = next(iter(stack.assistant._gate_owners))
    reply = AsyncMock()
    await stack.assistant._resolve_approval(
        _gate_body(action_id, "U_STRANGER"), reply, approved=True
    )
    assert "only the person who asked" in reply.call_args.kwargs["text"]
    assert not task.done(), "a stranger's click must not release the turn"

    await stack.assistant._resolve_approval(_gate_body(action_id, "U1"), reply, approved=True)
    await asyncio.wait_for(task, 5)
    assert "Approved by the user" in stack.posted()
    assert action_id not in stack.assistant._gate_owners


@pytest.mark.asyncio
async def test_only_the_requester_may_answer_a_question() -> None:
    model = ScriptedModel(
        script=[("ask_question", {"question": "Which?", "options": ["fast", "safe"]})]
    )
    stack = Stack(model)
    task = stack.send(user="U1")
    await _wait_for(lambda: "question_option_select" in stack.posted())

    question_id = next(iter(stack.assistant._gate_owners))
    reply = AsyncMock()
    await stack.assistant._resolve_question(_gate_body(f"{question_id}:fast", "U_STRANGER"), reply)
    assert "only the person who asked" in reply.call_args.kwargs["text"]
    assert not task.done()

    await stack.assistant._resolve_question(_gate_body(f"{question_id}:safe", "U1"), reply)
    await asyncio.wait_for(task, 5)
    assert "User response: safe" in stack.posted()


@pytest.mark.setting("governance.approval_policy")
@pytest.mark.asyncio
async def test_org_approval_policy_decides_without_a_slack_prompt() -> None:
    await save_org_config(
        None, "default_org", "governance", {"approval_policy": {"deny": ["high"]}}, []
    )
    model = ScriptedModel(
        script=[
            (
                "request_approval",
                {"action_type": "deploy", "description": "d", "risk_level": "high"},
            )
        ]
    )
    stack = Stack(model)
    await asyncio.wait_for(stack.send(), 5)

    assert "approve_action" not in stack.posted(), "policy-decided gates are not shown to humans"
    assert "Denied by the user" in stack.posted()


@pytest.mark.asyncio
async def test_messages_in_one_thread_run_one_at_a_time() -> None:
    model = ScriptedModel()
    model.gate = asyncio.Event()
    stack = Stack(model)
    first = stack.send("<@UBOT> first")
    await asyncio.wait_for(model.started.wait(), 5)
    second = stack.send("<@UBOT> second")
    await asyncio.sleep(0.2)
    assert len(model.agent_requests()) == 1, "the second message must wait for the first turn"

    model.gate.set()
    await asyncio.wait_for(asyncio.gather(first, second), 10)
    assert len(model.agent_requests()) == 2
    record = await stack.store.get_session(SESSION_ID)
    assert record is not None
    assert [m.role for m in record.conversation_history].count("user") == 2


@pytest.mark.asyncio
async def test_an_agent_allowed_every_tool_keeps_the_slack_tools() -> None:
    """The default agent's whitelist is ``["*"]``: Slack must not intersect with the wildcard
    itself, which left the model with no tools at all (it then wrote its calls as text)."""
    model = ScriptedModel()
    stack = Stack(model, SlackOrgPolicy(default_agent_persona="persona-general"))
    await stack.send()

    assert model.requests and model.requests[0].tools, "the turn reached the model with tools"
    names = {t.name for t in model.requests[0].tools}
    assert {"web_search", "file_read", "bash_exec"} <= names
    assert not names & {"git_push", "git_open_pr", "git_revert"}, "Slack's blocklist still applies"


async def _tools_for_dm(*, linked: bool, restrict: bool) -> set[str]:
    model = ScriptedModel()
    stack = Stack(
        model,
        SlackOrgPolicy(default_agent_persona="persona-general", restrict_linked_members=restrict),
    )

    async def resolve(slack_user: str, email: str | None, org: str) -> tuple[str, str] | None:
        return ("member-alice", org) if linked else None

    stack.assistant.resolve_user_fn = resolve  # type: ignore[assignment]
    await stack.assistant.process_incoming_request(
        channel_id="D1",
        thread_ts="1.1",
        raw_text="push my branch",
        say_fn=stack.say,
        user_id="U1",
        channel_type="im",
        team_id="T1",
    )
    assert model.requests and model.requests[0].tools
    return {t.name for t in model.requests[0].tools}


@pytest.mark.asyncio
@pytest.mark.setting("slack.restrict_linked_members")
async def test_a_linked_members_own_request_is_not_held_to_the_anonymous_blocklist() -> None:
    """The blocklist is for anyone in the workspace; a linked member's DM runs as them (they
    were refused git_push while the generic GitHub API wrote with their account)."""
    publishing = {"git_push", "git_open_pr"}

    assert publishing <= await _tools_for_dm(linked=True, restrict=False)
    assert not publishing & await _tools_for_dm(linked=True, restrict=True)
    assert not publishing & await _tools_for_dm(linked=False, restrict=False)


@pytest.mark.asyncio
async def test_an_agent_allowlist_does_not_take_back_a_linked_members_tools() -> None:
    """The Slack bot's agent listed its tools: the blocked ones were also removed from that
    list, so a linked member stayed blocked after the denied list was lifted."""
    from slack_assistant.assistant import _Admitted

    stack = Stack(ScriptedModel())
    agent_tools = ["git_push", "git_open_pr", "file_read"]

    async def scope(*, linked: bool, restrict: bool = False) -> set[str]:
        admitted = _Admitted(
            prompt="push",
            title_text="push",
            team_scope="T1",
            is_dm=False,
            session_user_id="member-alice" if linked else "guest-U1",
            session_org_id="org",
            rocket_user_id="member-alice" if linked else "guest-U1",
            org_policy=SlackOrgPolicy(restrict_linked_members=restrict),
            private=True,
            linked=linked,
        )
        overlay = await stack.assistant._build_overlay(
            channel_id="C1", thread_ts="1.1", admitted=admitted, attachment_note="", attachments=()
        )
        assert overlay.tool_scope is not None
        return set(overlay.tool_scope(agent_tools) or []) - set(overlay.denied_tools or ())

    assert await scope(linked=True) == set(agent_tools)
    assert await scope(linked=True, restrict=True) == {"file_read"}
    assert await scope(linked=False) == {"file_read"}

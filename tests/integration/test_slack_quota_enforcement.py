"""Integration tests: Slack hardening rejects over-limit turns before dispatch.

An over-limit or blocked request is answered with an
ephemeral reply and the orchestrator is never called. Also exercises attachment
wiring into the prompt's dynamic suffix.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock

import pytest
from api.services.quota import (
    InMemoryWorkspaceQuotaStore,
    SlackWorkspaceQuotaService,
    WorkspaceQuotaState,
)
from slack_assistant.config import SlackAssistantSettings
from slack_assistant.file_ingestion import SlackUploadedFile
from slack_assistant.guardrails import QuotaDecision, tool_enforcement_message
from slack_harness import make_slack

from specifications.interfaces.agent import (
    AgentEvent,
    AgentOrchestratorProtocol,
    SystemPrompt,
)
from specifications.interfaces.llm import AttachmentRef, ToolDefinition


class FakeClock:
    def __init__(self, start: float = 0.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class RecordingOrchestrator(AgentOrchestratorProtocol):
    """Records dispatch attempts; optionally streams scripted events or raises."""

    def __init__(self, events: list[AgentEvent] | None = None, fail: bool = False) -> None:
        self.events = events or []
        self.fail = fail
        self.calls = 0
        self.last_whitelisted_tools: list[str] | None = None
        self.last_denied_tools: frozenset[str] | None = None
        self.last_system_prompt: SystemPrompt | None = None
        self.last_model: str | None = None
        self.last_max_turns: int | None = None

    def process_user_turn(
        self,
        session_id: str,
        user_message: str,
        model: str | None = None,
        system_prompt: str | None = None,
        system_prompt_parts: SystemPrompt | None = None,
        whitelisted_tools: list[str] | None = None,
        tenant_org_id: str = "default_org",
        tenant_user_id: str = "dev_user",
        max_turns: int | None = None,
        **kwargs: object,
    ) -> AsyncIterator[AgentEvent]:
        self.calls += 1
        self.last_whitelisted_tools = whitelisted_tools
        self.last_denied_tools = kwargs.get("denied_tools")  # type: ignore[assignment]
        self.last_system_prompt = system_prompt_parts
        self.last_model = model
        self.last_max_turns = max_turns

        async def _gen() -> AsyncIterator[AgentEvent]:
            if self.fail:
                raise RuntimeError("orchestrator exploded")
            for event in self.events:
                yield event

        return _gen()

    async def submit_human_approval(self, session_id: str, action_id: str, approved: bool) -> None:
        pass

    async def submit_question_answer(
        self,
        session_id: str,
        question_id: str,
        selected_options: list[str],
        custom_text: str | None = None,
    ) -> None:
        pass

    async def cancel_turn(self, session_id: str) -> None:
        pass


class FakeRegistry:
    """Minimal registry exposing a catalog for allowlist resolution."""

    def __init__(self, names: list[str]) -> None:
        self._names = names
        self._dynamic_tools: dict[str, Any] = {}

    def register_dynamic_tool(self, *args: Any, **kwargs: Any) -> None:
        pass

    def get_tool_definitions(self) -> list[ToolDefinition]:
        return [
            ToolDefinition(name=name, description="", parameters_json_schema={})
            for name in self._names
        ]


class DenyingGate:
    def __init__(self, decision: QuotaDecision) -> None:
        self._decision = decision
        self.calls = 0

    async def evaluate(self, **kwargs: Any) -> QuotaDecision:
        self.calls += 1
        return self._decision

    def counters(self) -> dict[str, int]:
        return {"quota_denied": self.calls}


async def test_over_limit_turn_is_rejected_without_an_orchestrator_call() -> None:
    orchestrator = RecordingOrchestrator()
    gate = DenyingGate(
        QuotaDecision(
            False, "turns_per_hour", "🚫 Workspace quota exceeded.", retry_after_seconds=42
        )
    )
    assistant = make_slack(orchestrator=orchestrator, quota_gate=gate)
    say_mock = _async_recorder()
    ephemeral_mock = _async_recorder()

    await assistant.process_incoming_request(
        channel_id="C1",
        thread_ts="1.0",
        raw_text="<@UBOT> do work",
        say_fn=say_mock,
        user_id="U1",
        team_id="T1",
        ephemeral_fn=ephemeral_mock,
    )

    assert orchestrator.calls == 0
    assert say_mock.call_count == 0
    assert ephemeral_mock.call_count == 1
    assert "quota exceeded" in ephemeral_mock.call_args.kwargs["text"]
    assert gate.calls == 1


async def test_token_bucket_rate_limit_rejects_the_second_turn() -> None:
    orchestrator = RecordingOrchestrator()
    settings = SlackAssistantSettings(
        slack_user_turns_per_minute=1,
        slack_channel_turns_per_minute=1,
    )
    assistant = make_slack(orchestrator=orchestrator, settings=settings, clock=FakeClock(0.0))
    say_mock = _async_recorder()
    ephemeral_mock = _async_recorder()

    await assistant.process_incoming_request(
        channel_id="C1",
        thread_ts="1.0",
        raw_text="<@UBOT> first",
        say_fn=say_mock,
        user_id="U1",
        team_id="T1",
        ephemeral_fn=ephemeral_mock,
    )
    await assistant.process_incoming_request(
        channel_id="C1",
        thread_ts="1.0",
        raw_text="<@UBOT> second",
        say_fn=say_mock,
        user_id="U1",
        team_id="T1",
        ephemeral_fn=ephemeral_mock,
    )

    assert orchestrator.calls == 1
    assert ephemeral_mock.call_count == 1
    assert "Rate limit" in ephemeral_mock.call_args.kwargs["text"]
    assert assistant.quota_counters()["rate_limit_user_denied"] == 1


async def test_failure_breaker_pauses_after_consecutive_errors() -> None:
    orchestrator = RecordingOrchestrator(fail=True)
    settings = SlackAssistantSettings(
        slack_max_consecutive_failures=1,
        slack_failure_cooldown_seconds=300,
        slack_user_turns_per_minute=100,
        slack_channel_turns_per_minute=100,
    )
    assistant = make_slack(orchestrator=orchestrator, settings=settings, clock=FakeClock(0.0))
    say_mock = _async_recorder()
    ephemeral_mock = _async_recorder()

    await assistant.process_incoming_request(
        channel_id="C1",
        thread_ts="1.0",
        raw_text="<@UBOT> first",
        say_fn=say_mock,
        user_id="U1",
        team_id="T1",
        ephemeral_fn=ephemeral_mock,
    )
    assert orchestrator.calls == 1

    await assistant.process_incoming_request(
        channel_id="C1",
        thread_ts="1.0",
        raw_text="<@UBOT> second",
        say_fn=say_mock,
        user_id="U1",
        team_id="T1",
        ephemeral_fn=ephemeral_mock,
    )
    assert orchestrator.calls == 1  # breaker stopped the retry
    assert ephemeral_mock.call_count == 1


async def test_workspace_quota_gate_wired_through_in_memory_store() -> None:
    orchestrator = RecordingOrchestrator()
    clock = FakeClock(500.0)
    store = InMemoryWorkspaceQuotaStore()
    store.set_state(
        "T1",
        WorkspaceQuotaState(turns_per_hour=1, hour_window_start=clock(), turns_this_hour=1),
    )
    service = SlackWorkspaceQuotaService(in_memory_store=store, clock=clock)
    assistant = make_slack(orchestrator=orchestrator, quota_gate=service, clock=clock)
    say_mock = _async_recorder()
    ephemeral_mock = _async_recorder()

    await assistant.process_incoming_request(
        channel_id="C1",
        thread_ts="1.0",
        raw_text="<@UBOT> hello",
        say_fn=say_mock,
        user_id="U1",
        team_id="T1",
        ephemeral_fn=ephemeral_mock,
    )

    assert orchestrator.calls == 0
    assert ephemeral_mock.call_count == 1
    assert service.counters()["quota_denied"] == 1


async def test_allowing_gate_does_not_block_the_turn() -> None:
    """Regression: the real gate returns an allowed decision, not None.

    Returning that decision from `_pre_dispatch_guard` made every Slack turn look like a
    denial, so the assistant replied with the generic policy message and never dispatched.
    """
    orchestrator = RecordingOrchestrator()
    gate = DenyingGate(QuotaDecision(True))  # a gate that allows
    assistant = make_slack(orchestrator=orchestrator, quota_gate=gate)
    say_mock = _async_recorder()
    ephemeral_mock = _async_recorder()

    await assistant.process_incoming_request(
        channel_id="C1",
        thread_ts="1.0",
        raw_text="<@UBOT> hello",
        say_fn=say_mock,
        user_id="U1",
        team_id="T1",
        ephemeral_fn=ephemeral_mock,
    )

    assert orchestrator.calls == 1
    assert ephemeral_mock.call_count == 0


async def test_denial_without_a_message_names_the_reason() -> None:
    orchestrator = RecordingOrchestrator()
    gate = DenyingGate(QuotaDecision(False, "budget", ""))
    assistant = make_slack(orchestrator=orchestrator, quota_gate=gate)
    ephemeral_mock = _async_recorder()

    await assistant.process_incoming_request(
        channel_id="C1",
        thread_ts="1.0",
        raw_text="<@UBOT> hello",
        say_fn=_async_recorder(),
        user_id="U1",
        team_id="T1",
        ephemeral_fn=ephemeral_mock,
    )

    assert orchestrator.calls == 0
    text = ephemeral_mock.call_args.kwargs["text"]
    assert "budget" in text
    assert "workspace policy" in text


async def test_blocked_tool_is_excluded_from_the_whitelist_and_message_matches() -> None:
    registry = FakeRegistry(["file_read", "bash_exec", "git_revert", "git_open_pr"])
    orchestrator = RecordingOrchestrator()
    settings = SlackAssistantSettings(
        slack_allowed_tools="*", slack_blocked_tools="git_revert,git_open_pr"
    )
    assistant = make_slack(orchestrator=orchestrator, settings=settings, tool_registry=registry)

    await assistant.process_incoming_request(
        channel_id="C1",
        thread_ts="1.0",
        raw_text="<@UBOT> undo everything",
        say_fn=_async_recorder(),
        user_id="U1",
        team_id="T1",
    )

    # A wildcard stays unrestricted (MCP tools only exist once the turn starts); the blocked
    # tools are denied apart, and the registry refuses them whatever the allowlist.
    assert orchestrator.last_whitelisted_tools is None
    assert orchestrator.last_denied_tools == frozenset({"git_revert", "git_open_pr"})
    # The enforcement hook in ToolRegistry.call_tool returns this exact text.
    assert "git_revert" in tool_enforcement_message("git_revert")


async def test_attachments_are_delegated_and_noted_in_the_dynamic_prompt() -> None:
    orchestrator = RecordingOrchestrator()
    ingested: list[str] = []

    async def ingest_attachment_fn(**kwargs: Any) -> AttachmentRef:
        ingested.append(str(kwargs["name"]))
        return AttachmentRef(
            name=str(kwargs["name"]),
            mime=str(kwargs["mime"]),
            sha256="abc123",
            size=9,
            workspace_path=f"/workspace/.attachments/abc123/{kwargs['name']}",
        )

    class Downloader:
        async def download(self, file: SlackUploadedFile) -> bytes:
            return b"log bytes"

    assistant = make_slack(
        orchestrator=orchestrator,
        ingest_attachment_fn=ingest_attachment_fn,
        file_downloader=Downloader(),
    )

    await assistant.process_incoming_request(
        channel_id="C1",
        thread_ts="1.0",
        raw_text="<@UBOT> read this log",
        say_fn=_async_recorder(),
        user_id="U1",
        team_id="T1",
        files=[{"id": "F1", "name": "server.log", "mimetype": "text/plain", "url_private": "u"}],
    )

    assert ingested == ["server.log"]
    assert orchestrator.last_system_prompt is not None
    rendered = orchestrator.last_system_prompt.render()
    assert "server.log" in rendered
    assert "/workspace/.attachments/abc123/server.log" in rendered


def _async_recorder() -> Any:
    return AsyncMock()


@pytest.mark.asyncio
async def test_sanity_rate_limiter_default_settings_do_not_block_first_turn() -> None:
    orchestrator = RecordingOrchestrator()
    assistant = make_slack(orchestrator=orchestrator)
    await assistant.process_incoming_request(
        channel_id="C1",
        thread_ts="1.0",
        raw_text="<@UBOT> hi",
        say_fn=_async_recorder(),
        user_id="U1",
        team_id="T1",
    )
    assert orchestrator.calls == 1

"""The Slack thread experience end to end: the streamed reply, stopping, links, files, gates."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from test_slack_shared_pipeline import THREAD, ScriptedModel, Stack, _gate_body, _wait_for

from specifications.interfaces.events import EventType, SessionEvent

SESSION_ID = "slack_C1_1_0"


def _live(stack: Stack, **policy: Any) -> AsyncMock:
    """Make the stack behave as a connected workspace with a recording Slack client."""
    client = AsyncMock()
    client.chat_postMessage.return_value = {"ts": "9.9"}
    stack.assistant.app = SimpleNamespace(client=client)  # type: ignore[assignment]
    stack.assistant._slack_api_ready = True
    stack.assistant.settings = stack.assistant.settings.model_copy(
        update={"slack_web_base_url": "https://rocket.example"}
    )
    return client


@pytest.mark.asyncio
async def test_a_turn_streams_its_steps_and_writes_the_answer_into_the_same_reply() -> None:
    stack = Stack(
        ScriptedModel(script=[("think", {"thought": "plan"})], final_text="All done here.")
    )
    client = _live(stack)
    client.chat_startStream.return_value = {"ts": "9.9"}

    await stack.send()

    start = client.chat_startStream.call_args.kwargs
    assert start["thread_ts"] == THREAD[1] and start["task_display_mode"] == "timeline"
    assert (start["recipient_user_id"], start["recipient_team_id"]) == ("U1", "T1")
    assert start["chunks"][0]["type"] == "task_update"
    assert start["chunks"][0]["status"] == "in_progress"
    done = client.chat_appendStream.call_args.kwargs["chunks"][0]
    assert done["id"] == start["chunks"][0]["id"] and done["status"] == "complete"
    stop = client.chat_stopStream.call_args.kwargs
    assert stop["ts"] == "9.9" and stop["markdown_text"] == "All done here."
    footer = json.dumps(stop["blocks"])
    assert f"https://rocket.example/chat/{SESSION_ID}" in footer and "Done in" in footer
    assert "stop_turn" not in footer, "no Stop button: the slash command stops a turn"
    stack.say.assert_not_awaited()
    client.chat_postMessage.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_turn_without_tools_answers_with_a_plain_reply() -> None:
    stack = Stack(ScriptedModel(final_text="Hello there."))
    client = _live(stack)

    await stack.send()

    client.chat_startStream.assert_not_awaited()
    assert "Hello there." in stack.say.call_args.kwargs["text"]


@pytest.mark.asyncio
async def test_a_workspace_without_streaming_still_gets_the_answer() -> None:
    stack = Stack(
        ScriptedModel(script=[("think", {"thought": "plan"})], final_text="All done here.")
    )
    client = _live(stack)
    client.chat_startStream.side_effect = RuntimeError("not_allowed_token_type")

    await stack.send()

    assert "All done here." in stack.say.call_args.kwargs["text"]
    client.chat_stopStream.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_long_answer_is_summarised_and_attached_as_a_file() -> None:
    long_answer = "Section.\n\n" + ("Details about the investigation. " * 400)
    stack = Stack(ScriptedModel(final_text=long_answer))
    client = _live(stack)

    await stack.send()

    upload = client.files_upload_v2.call_args.kwargs
    assert upload["filename"] == "answer.md" and upload["content"] == long_answer.strip()
    assert len(stack.posted()) < len(long_answer) / 2, "the thread gets a short head, not 14 KB"


@pytest.mark.asyncio
async def test_a_large_change_is_attached_as_a_diff_file() -> None:
    model = ScriptedModel(final_text="Refactored.", gate=asyncio.Event())
    stack = Stack(model)
    client = _live(stack)
    task = stack.send()
    await asyncio.wait_for(model.started.wait(), 5)
    await stack.bus.publish(
        SessionEvent(
            session_id=SESSION_ID,
            event_type=EventType.FILE_DIFF,
            payload={
                "path": "big.py",
                "diff_content": "\n".join(f"+line {i}" for i in range(200)),
                "additions": 200,
                "deletions": 0,
                "is_new_file": True,
            },
        )
    )
    assert model.gate is not None
    model.gate.set()
    await asyncio.wait_for(task, 5)

    upload = client.files_upload_v2.call_args.kwargs
    assert upload["filename"] == "changes.diff" and "+line 199" in upload["content"]
    assert "big.py" in stack.posted()


@pytest.mark.asyncio
async def test_only_the_requester_can_stop_a_turn() -> None:
    model = ScriptedModel(gate=asyncio.Event())
    stack = Stack(model)
    _live(stack)
    stack.assistant.orchestrator.cancel_turn = AsyncMock()  # type: ignore[method-assign]
    task = stack.send(user="U1")
    await asyncio.wait_for(model.started.wait(), 5)

    try:
        stranger = await stack.assistant._stop_text({"channel_id": THREAD[0], "user_id": "U2"})
        assert stranger == "You have nothing running in this channel."
        stack.assistant.orchestrator.cancel_turn.assert_not_called()

        await stack.assistant._stop_text({"channel_id": THREAD[0], "user_id": "U1"})
        stack.assistant.orchestrator.cancel_turn.assert_awaited_once_with(SESSION_ID)
    finally:
        assert model.gate is not None
        model.gate.set()
        await asyncio.wait_for(task, 5)


@pytest.mark.asyncio
async def test_a_gate_can_be_answered_after_the_process_forgot_it() -> None:
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

    stack.assistant._gate_owners.clear()  # a restart, or the click landing on another replica
    reply = AsyncMock()
    await stack.assistant._resolve_approval(
        _gate_body(action_id, "U_STRANGER"), reply, approved=True
    )
    assert "only the person who asked" in reply.call_args.kwargs["text"]
    assert not task.done()

    await stack.assistant._resolve_approval(_gate_body(action_id, "U1"), reply, approved=True)
    await asyncio.wait_for(task, 5)
    assert "Approved by the user" in stack.posted()


@pytest.mark.asyncio
async def test_a_question_whose_turn_died_continues_the_work_in_the_thread() -> None:
    stack = Stack(ScriptedModel())
    await stack.send()  # records U1 as the session's requester
    asked: list[tuple[Any, ...]] = []

    async def stored_question(*args: Any) -> str | None:
        asked.append(args)
        return "You asked 'which db?'; the user chose Postgres. Carry on."

    stack.assistant._answer_stored_question_fn = stored_question
    continued = asyncio.Event()

    async def fake_process(**kwargs: Any) -> None:
        stack.continued_with = kwargs  # type: ignore[attr-defined]
        continued.set()

    stack.assistant.process_incoming_request = fake_process  # type: ignore[method-assign]
    reply = AsyncMock()
    await stack.assistant._resolve_question(_gate_body("q_old:Postgres", "U1"), reply)
    await asyncio.wait_for(continued.wait(), 5)

    assert asked == [(SESSION_ID, "q_old", ["Postgres"], None)]
    assert "picking the work back up" in reply.call_args.kwargs["text"]
    assert stack.continued_with["raw_text"].startswith("You asked")  # type: ignore[attr-defined]
    assert stack.continued_with["user_id"] == "U1"  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_a_channel_profile_sets_persona_rules_and_tools() -> None:
    from slack_assistant.policy import ChannelProfile, SlackOrgPolicy

    model = ScriptedModel()
    policy = SlackOrgPolicy(
        channel_profiles={
            THREAD[0]: ChannelProfile(
                agent_persona="persona-security",
                instructions="Answer in French.",
                allowed_tools=("file_read",),
                sandbox_image="registry.example/sec-box:1",
            )
        }
    )
    stack = Stack(model, policy)
    await stack.send()

    request = model.agent_requests()[0]
    system_text = "\n".join(m.content or "" for m in request.messages if m.role == "system")
    assert "Security Auditor persona" in system_text and "Answer in French." in system_text
    assert {t.name for t in request.tools} == {"file_read"}
    record = await stack.store.get_session(SESSION_ID)
    assert record is not None and record.container_image == "registry.example/sec-box:1"


@pytest.mark.asyncio
async def test_browser_tools_follow_the_sandbox_the_session_uses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from api.services.sandbox_catalogue import SandboxCatalogue

    from specifications.interfaces.session import SessionRecord, SessionSource

    monkeypatch.setattr(SandboxCatalogue, "_MEMORY", {})
    plain_model = ScriptedModel()
    await Stack(plain_model).send()
    assert "browser_inspect" not in {t.name for t in plain_model.agent_requests()[0].tools}

    catalogue = SandboxCatalogue(None)
    box = await catalogue.create(
        "default_org", name="Browser", image="registry.example/browser:1", capabilities=["browser"]
    )
    browser_model = ScriptedModel()
    stack = Stack(browser_model)
    await stack.store.create_session(
        SessionRecord(
            session_id=SESSION_ID,
            tenant_org_id="default_org",
            tenant_user_id="slack_global",
            title="t",
            source=SessionSource.SLACK,
            container_image=box.image,
            metadata={"sandbox_id": box.id, "toolsets": ["slack"]},
        )
    )
    await stack.send()
    assert "browser_inspect" in {t.name for t in browser_model.agent_requests()[0].tools}


@pytest.mark.asyncio
async def test_a_failed_turn_says_why_in_the_thread() -> None:
    stack = Stack(ScriptedModel(final_text="never"))
    _live(stack)

    async def broken(*_: Any, **__: Any) -> None:
        raise RuntimeError("model refused")

    stack.assistant._run_shared_turn = broken  # type: ignore[method-assign]
    await stack.send()

    assert "model refused" in stack.say.call_args.kwargs["text"]

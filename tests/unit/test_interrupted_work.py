"""What a turn did before its process died is shown in the session and handed to the next turn."""

from __future__ import annotations

import json
from typing import Any

import pytest
from api.db.store import TurnTraceRecord
from api.services import interrupted_work as work_mod
from api.services import queued_messages
from api.services.interrupted_work import (
    NO_RESULT,
    SAVED_KEY,
    _save_one,
    build_interrupted_work,
    history_entries,
    resume_notice,
)
from api.services.transcript import InMemoryTranscriptStore
from api.services.turn_resume import DEFAULT_AUTO_RESUME_ATTEMPTS
from api.store import InMemorySessionStore

from specifications.interfaces.llm import ChatMessage
from specifications.interfaces.session import SessionRecord

ORG, SESSION = "org_interrupted", "session_interrupted"
START, END = 1_000, 1_900


def _trace(calls: list[dict[str, Any]]) -> TurnTraceRecord:
    return TurnTraceRecord(
        id="trace-1",
        session_id=SESSION,
        org_id=ORG,
        turn_index=1,
        started_at=START,
        ended_at=END,
        status="interrupted",
        tool_calls=calls,
    )


def _done(call_id: str, name: str, seq: int, reasoning: str = "") -> dict[str, Any]:
    call = {
        "id": call_id,
        "name": name,
        "arguments": {"path": "a.py"},
        "result": "ok",
        "status": "completed",
        "seq": seq,
        "created_at": START + seq,
    }
    return {**call, "reasoning": reasoning} if reasoning else call


def _assistant(
    at: int, content: str, calls: list[tuple[str, str, dict[str, Any]]], reasoning: str = ""
) -> ChatMessage:
    return ChatMessage(
        role="assistant",
        content=content,
        reasoning=reasoning or None,
        tool_calls=[
            {"id": cid, "type": "function", "function": {"name": n, "arguments": json.dumps(a)}}
            for cid, n, a in calls
        ]
        or None,
        created_at=at,
    )


TRANSCRIPT = [
    ChatMessage(role="user", content="Fix the bug", created_at=START),
    _assistant(START + 1, "Reading the file.", [("c1", "file_read", {"path": "a.py"})], "first"),
    ChatMessage(role="tool", content="ok", tool_call_id="c1", created_at=START + 2),
    _assistant(
        START + 3,
        "",
        [("c2", "bash_exec", {"command": "make test"})],
        "I should run the tests now, then look at the failures one by one.",
    ),
]


def test_finished_calls_come_from_the_trace_and_the_running_one_from_the_transcript() -> None:
    work = build_interrupted_work(_trace([_done("c1", "file_read", 1, "first")]), TRANSCRIPT)

    assert [call["id"] for call in work.executed] == ["c1"]
    assert [call["id"] for call in work.in_flight] == ["c2"]
    assert work.in_flight[0]["status"] == "interrupted" and work.in_flight[0]["result"] == NO_RESULT
    assert work.in_flight[0]["arguments"] == {"command": "make test"}
    assert "run the tests now" in work.in_flight[0]["reasoning"], "its reasoning is kept with it"
    assert work.text == "Reading the file."


def test_the_history_entry_carries_every_call_and_says_what_happened() -> None:
    work = build_interrupted_work(_trace([_done("c1", "file_read", 1)]), TRANSCRIPT)

    assistant, note = history_entries(work)

    assert assistant.role == "assistant" and [c["id"] for c in assistant.tool_calls or []] == [
        "c1",
        "c2",
    ]
    assert note.role == "system" and "restarted" in (note.content or "")


def test_calls_the_history_already_holds_are_not_shown_twice() -> None:
    earlier = [
        ChatMessage(role="assistant", content="", tool_calls=[{"id": "c1", "name": "file_read"}])
    ]

    work = build_interrupted_work(_trace([_done("c1", "file_read", 1)]), TRANSCRIPT, earlier)

    assert work.executed == [], "a message sent mid-turn had saved the turn so far"
    assert [call["id"] for call in work.in_flight] == ["c2"]


def test_a_turn_that_did_nothing_leaves_only_the_note() -> None:
    work = build_interrupted_work(_trace([]), [TRANSCRIPT[0]])

    entries = history_entries(work)

    assert work.is_empty and [e.role for e in entries] == ["system"]


def test_the_resume_notice_says_where_the_last_turn_stopped_and_stays_short() -> None:
    work = build_interrupted_work(_trace([_done("c1", "file_read", 1)]), TRANSCRIPT)

    notice = resume_notice("The server restarted.", work)

    assert notice.startswith("The server restarted.")
    assert "completed 1 tool calls" in notice and "file_read (completed)" in notice
    assert (
        "`bash_exec`" in notice
        and "make test" in notice
        and "check whether it took effect" in notice
    )
    assert "run the tests now" in notice, "the thought the step was in the middle of"
    assert len(notice) < 1_500
    assert resume_notice("base", None) == "base"


class _NoRunningTurn:
    async def running_turn(self, session_id: str, org_id: str) -> None:
        return None


async def test_saving_puts_the_work_in_the_session_once_and_frees_it() -> None:
    sessions, transcripts = InMemorySessionStore(), InMemoryTranscriptStore()
    await sessions.create_session(
        SessionRecord(
            session_id=SESSION,
            tenant_org_id=ORG,
            tenant_user_id="u1",
            title="t",
            conversation_history=[ChatMessage(role="user", content="Fix the bug")],
            is_processing=True,
        )
    )
    await transcripts.append(SESSION, ORG, TRANSCRIPT)
    trace = _trace([_done("c1", "file_read", 1)])

    first = await _save_one(trace, sessions, transcripts, _NoRunningTurn())  # type: ignore[arg-type]
    again = await _save_one(trace, sessions, transcripts, _NoRunningTurn())  # type: ignore[arg-type]

    record = await sessions.get_session(SESSION, ORG)
    assert record is not None
    assert (first, again) == (True, False)
    assert [m.role for m in record.conversation_history] == ["user", "assistant", "system"]
    assert record.is_processing is False, "no turn runs, so the session is not busy"
    assert record.metadata[SAVED_KEY] == ["trace-1"]


async def test_a_message_is_not_queued_for_a_turn_that_is_not_running(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sessions = InMemorySessionStore()
    await sessions.create_session(
        SessionRecord(
            session_id=SESSION,
            tenant_org_id=ORG,
            tenant_user_id="u1",
            title="t",
            is_processing=True,  # left set by a process that died
        )
    )

    class _Orchestrator:
        def is_running(self, session_id: str) -> bool:
            return False

    async def none(*args: Any, **kwargs: Any) -> None:
        return None

    async def running(*args: Any, **kwargs: Any) -> object:
        return object()

    monkeypatch.setattr(queued_messages, "session_running_turn", none)
    assert not await queued_messages._turn_running(
        sessions, _Orchestrator(), object(), ORG, SESSION
    )

    monkeypatch.setattr(queued_messages, "session_running_turn", running)
    assert await queued_messages._turn_running(sessions, _Orchestrator(), object(), ORG, SESSION)

    # Without a database the flag is all there is.
    assert await queued_messages._turn_running(sessions, _Orchestrator(), None, ORG, SESSION)


def test_two_resume_attempts_by_default() -> None:
    assert DEFAULT_AUTO_RESUME_ATTEMPTS == 2
    assert work_mod.STALE_FLAG_SECONDS > 0


def _long_turn(calls: int) -> list[ChatMessage]:
    """A transcript of ``calls`` finished calls, every one inside the interrupted turn's window."""
    messages = [ChatMessage(role="user", content="Fix the bug", created_at=START)]
    for index in range(calls):
        call_id = f"x{index}"
        messages.append(
            _assistant(START + 1, "", [(call_id, "bash_exec", {"command": f"echo {index}"})])
        )
        messages.append(
            ChatMessage(role="tool", content="ok", tool_call_id=call_id, created_at=START + 2)
        )
    return messages


def test_a_call_with_a_result_in_the_transcript_is_never_in_flight() -> None:
    # The trace knows none of them (it holds only the last two), yet every one has its result.
    work = build_interrupted_work(_trace([_done("c1", "file_read", 1)]), _long_turn(200))

    assert work.in_flight == []


def test_the_notice_does_not_grow_with_the_calls_that_have_no_result() -> None:
    work = work_mod.InterruptedWork(
        trace_id="t",
        executed=[_done(f"e{i}", "bash_exec", i) for i in range(200)],
        in_flight=[
            {
                "id": f"f{i}",
                "name": "bash_exec",
                "arguments": {"command": "x" * 5_000},
                "reasoning": "r" * 5_000,
            }
            for i in range(200)
        ],
        reasoning="t" * 50_000,
    )

    notice = resume_notice("The server restarted.", work)

    assert len(notice) <= work_mod.NOTICE_MAX_CHARS
    assert notice.count("Stopped while running") == work_mod.NOTICE_MAX_IN_FLIGHT
    assert "195 earlier calls also have no recorded result" in notice
    assert "completed 200 tool calls" in notice


def test_a_message_loaded_from_the_database_keeps_the_time_it_was_written() -> None:
    from api.db.models import SessionMessageModel
    from api.services.transcript import _from_row

    row = SessionMessageModel(
        id="m", session_id=SESSION, org_id=ORG, seq=1, role="assistant", content="hi",
        created_at=START + 5,
    )  # fmt: skip

    assert _from_row(row).created_at == START + 5


async def test_the_in_memory_transcript_keeps_it_too() -> None:
    transcripts = InMemoryTranscriptStore()
    await transcripts.append(SESSION, ORG, [ChatMessage(role="user", content="a", created_at=42)])

    assert [m.created_at for m in await transcripts.load(SESSION, ORG)] == [42]

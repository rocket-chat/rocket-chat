"""Unit tests for turn-trace assembly.

The accumulator is the only place where the API layer's per-turn telemetry becomes a
durable row, so it is tested against a synthetic orchestrator event stream — the shape
the real route feeds it — rather than through the orchestrator itself.
"""

from __future__ import annotations

from pathlib import Path

from api.services.turn_trace import (
    INTERRUPTED_TURN_ERROR,
    MAX_REASONING_SUMMARY_CHARS,
    TURN_STATUS_COMPLETED,
    TURN_STATUS_ERROR,
    TURN_STATUS_INTERRUPTED,
    TURN_STATUS_RUNNING,
    TurnTraceAccumulator,
    user_turn_index,
)

from specifications.interfaces.llm import ChatMessage

MIGRATION = Path(__file__).resolve().parents[2] / "migrations" / "versions" / "003_turn_traces.py"

TOOL_CALL = {
    "id": "call_1",
    "name": "bash_exec",
    "arguments": {"command": "pytest -q"},
    "result": "3 passed",
    "status": "completed",
    "duration_ms": 412,
}
APPROVAL = {
    "action_id": "ap_3f9c21ab",
    "action_type": "git_revert_hard",
    "command_or_path": "Discard uncommitted work",
    "risk_level": "critical",
}


def _accumulator() -> TurnTraceAccumulator:
    return TurnTraceAccumulator(
        session_id="sess-1", org_id="org_a", turn_index=2, started_at=1_700_000_000
    )


def test_accumulates_tool_calls_diffs_and_approvals() -> None:
    """A synthetic event stream lands in the trace verbatim, in order."""
    trace = _accumulator()
    trace.record_tool_call(TOOL_CALL)
    trace.record_file_diff({"path": "src/auth.py", "additions": 3, "deletions": 1})
    trace.record_approval_request(APPROVAL)
    trace.record_tool_call({**TOOL_CALL, "id": "call_2", "name": "git_status"})

    finished = trace.finish(status=TURN_STATUS_COMPLETED, reasoning="Inspecting the failing test.")

    assert finished.session_id == "sess-1"
    assert finished.org_id == "org_a"
    assert finished.turn_index == 2
    assert finished.started_at == 1_700_000_000
    assert finished.ended_at >= finished.started_at
    assert [call["name"] for call in finished.tool_calls] == ["bash_exec", "git_status"]
    assert finished.tool_calls[0]["duration_ms"] == 412
    assert finished.diff_paths == ["src/auth.py"]
    assert finished.approvals == [APPROVAL]
    assert finished.reasoning_summary == "Inspecting the failing test."
    assert finished.status == TURN_STATUS_COMPLETED
    assert finished.error is None
    assert finished.tokens == {}


def test_repeated_diff_for_one_path_is_recorded_once() -> None:
    """A file rewritten twice in a turn is one entry, not two."""
    trace = _accumulator()
    trace.record_file_diff({"path": "src/auth.py"})
    trace.record_file_diff({"path": "src/auth.py"})
    trace.record_file_diff({"path": "src/session.py"})

    assert trace.finish(status=TURN_STATUS_COMPLETED).diff_paths == [
        "src/auth.py",
        "src/session.py",
    ]


def test_reasoning_summary_is_bounded() -> None:
    """Reasoning is an anchor, not a transcript: the stored summary is clamped."""
    trace = _accumulator()
    finished = trace.finish(
        status=TURN_STATUS_COMPLETED,
        reasoning="x" * (MAX_REASONING_SUMMARY_CHARS + 500),
    )

    assert finished.reasoning_summary is not None
    assert finished.reasoning_summary.startswith("x" * MAX_REASONING_SUMMARY_CHARS)
    assert "truncated" in finished.reasoning_summary
    assert len(finished.reasoning_summary) < MAX_REASONING_SUMMARY_CHARS + 200


def test_blank_reasoning_is_stored_as_absent() -> None:
    """A turn with no reasoning stream stores NULL rather than an empty string."""
    assert (
        _accumulator().finish(status=TURN_STATUS_COMPLETED, reasoning="  \n").reasoning_summary
        is None
    )


def test_failed_turn_keeps_its_error_and_partial_telemetry() -> None:
    """An errored turn still yields a usable trace, tagged with the failure."""
    trace = _accumulator()
    trace.record_tool_call(TOOL_CALL)

    finished = trace.finish(status=TURN_STATUS_ERROR, error="sandbox unavailable")

    assert finished.status == TURN_STATUS_ERROR
    assert finished.error == "sandbox unavailable"
    assert [call["name"] for call in finished.tool_calls] == ["bash_exec"]


def test_user_turn_index_counts_accepted_instructions() -> None:
    """Turn numbering follows the conversation, so overlapping turns cannot collide."""
    history = [
        ChatMessage(role="user", content="first"),
        ChatMessage(role="assistant", content="done"),
        ChatMessage(role="user", content="second"),
    ]

    assert user_turn_index([]) == 1
    assert user_turn_index(history) == 2


def test_each_finished_trace_has_a_distinct_identifier() -> None:
    trace_a = _accumulator().finish(status=TURN_STATUS_COMPLETED)
    trace_b = _accumulator().finish(status=TURN_STATUS_COMPLETED)

    assert trace_a.id.startswith("trace_")
    assert trace_a.id != trace_b.id


def test_start_and_finish_address_the_same_running_row() -> None:
    """The running marker and the terminal write must target one row, not two."""
    trace = _accumulator()

    running = trace.start_record()
    finished = trace.finish(status=TURN_STATUS_COMPLETED)

    assert running.id == finished.id
    assert running.status == TURN_STATUS_RUNNING
    assert running.ended_at is None
    assert finished.status == TURN_STATUS_COMPLETED
    assert finished.ended_at is not None


def test_interrupted_status_is_distinct_from_terminal_states() -> None:
    """Recovery writes a status the cockpit can tell apart to offer a retry."""
    assert TURN_STATUS_INTERRUPTED not in {TURN_STATUS_RUNNING, TURN_STATUS_COMPLETED}
    assert TURN_STATUS_RUNNING != TURN_STATUS_ERROR
    assert INTERRUPTED_TURN_ERROR


def test_migration_003_declares_tenant_isolation() -> None:
    """The migration must ship the RLS policy the isolation test exercises.

    CI provisions the schema from SQLAlchemy metadata and never runs Alembic, so the
    policy statement is asserted here — a table that silently loses its tenant policy
    would otherwise only show up as a production data leak.
    """
    source = MIGRATION.read_text(encoding="utf-8")

    assert 'revision: str = "003_turn_traces"' in source
    assert 'down_revision: str | None = "002_user_and_org_scoping"' in source
    assert "CREATE POLICY tenant_isolation_turn_traces ON turn_traces" in source
    assert "USING (org_id = current_setting('app.current_org_id', true))" in source
    assert "ALTER TABLE turn_traces FORCE ROW LEVEL SECURITY;" in source
    assert "DROP POLICY IF EXISTS tenant_isolation_turn_traces ON turn_traces;" in source


def test_attachments_are_recorded_by_reference_not_content() -> None:
    from specifications.interfaces.llm import AttachmentRef

    trace = TurnTraceAccumulator(session_id="s", org_id="o", turn_index=1)
    trace.record_attachments(
        [
            AttachmentRef(
                name="shot.png",
                mime="image/png",
                sha256="x",
                size=2048,
                workspace_path="/workspace/.attachments/shot.png",
                is_image=True,
                data=b"\x89PNG-bytes",
                attachment_id="att_9",
            )
        ]
    )

    finished = trace.finish(status=TURN_STATUS_COMPLETED)

    assert finished.attachments == [
        {"id": "att_9", "name": "shot.png", "mime": "image/png", "size": 2048}
    ]
    assert "PNG-bytes" not in repr(finished)

"""Loop and stall detection steer an autonomous agent without ever pausing it for a person."""

from __future__ import annotations

import asyncio

from tests.turn_harness import run_turn


async def test_repeated_failures_in_auto_mode_warn_the_agent_without_asking_anyone() -> None:
    script = [("bash_exec", {"command": "frobnicate --all", "cwd": "/workspace"})] * 6

    outcome = await asyncio.wait_for(
        run_turn("org_auto_progress", script=script, metadata={"permission_mode": "auto"}),
        timeout=30,
    )

    kinds = {getattr(event, "event_type", "") for event in outcome.events}
    assert not {"interactive_question", "decision_required"} & kinds
    sent = " ".join(m.content or "" for r in outcome.model.requests if r.tools for m in r.messages)
    assert "Progress warning" in sent
    assert outcome.session.conversation_history[-1].content == "All done."


async def test_a_resent_checklist_is_a_no_op_and_never_reads_as_a_stall() -> None:
    """Re-sending an unchanged checklist counted as "no progress", and the stall warning asked
    for yet another update: agents spent most of their steps re-sending a two-item list."""
    tasks = [
        {"id": "1", "title": "Fix the build", "status": "in_progress"},
        {"id": "2", "title": "Push", "status": "pending"},
    ]
    script: list[tuple[str, dict[str, object]]] = []
    for index in range(80):
        script += [("update_task_checklist", {"tasks": tasks}), ("list_dir", {"path": f"d{index}"})]

    outcome = await run_turn("org_checklist_resend", script=script)

    sent = " ".join(m.content or "" for r in outcome.model.requests if r.tools for m in r.messages)
    assert "Stall Warning" not in sent and "without updating any checklist" not in sent
    assert sent.count("Checklist unchanged") >= 70
    kinds = {getattr(event, "event_type", "") for event in outcome.events}
    assert "interactive_question" not in kinds

"""A long turn compacts more than once and still has the person's first request, word for word."""

from __future__ import annotations

from api.services.settings import save_org_config

from tests.turn_harness import SCRIPTED_SUMMARY, run_turn

REQUEST = "Implement SVC-21926, and never re-consume an event twice"


async def test_a_turn_that_compacts_twice_keeps_the_request_and_a_checked_summary() -> None:
    org = "long_turn_compaction"
    await save_org_config(None, org, "models", {"context_windows": {"*": 8_000}}, [])
    script = [("bash_exec", {"command": f"echo step {n}", "cwd": "/workspace"}) for n in range(60)]

    outcome = await run_turn(org, prompt=REQUEST, script=script)

    compactions = outcome.events_of("context_compacted")
    assert len(compactions) >= 2, "the window forces more than one compaction"
    assert all(e.payload["summary_status"] == "model" for e in compactions)
    assert outcome.model.summary_requests >= 2
    steps = [r for r in outcome.model.requests if r.tools and r.tool_choice != "none"]
    last = steps[-1]
    assert any(REQUEST in (m.content or "") for m in last.messages), "the request survives"
    assert sum(SCRIPTED_SUMMARY in (m.content or "") for m in last.messages) == 1, "one checkpoint"

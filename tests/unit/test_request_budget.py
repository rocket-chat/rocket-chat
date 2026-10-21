"""What every request of a coding turn pays before the conversation starts.

Each of a long turn's steps re-sends the system prompt and every tool definition, so a few
thousand characters added here cost hundreds of thousands of tokens over a session, and a model
choosing among more tools chooses worse. ``tests/golden/request_budget.json`` holds the reviewed
figures: the names of the tools sent, and the size of the prompt and of their definitions. A
change to the tool set fails until the snapshot is updated; growth beyond ``TOLERANCE`` fails
until it is reviewed. Regenerate with ``UPDATE_PROMPT_SNAPSHOTS=1 uv run pytest
tests/unit/test_request_budget.py``.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict
from pathlib import Path
from typing import Any

from llm_gateway.tokens import request_tokens

from tests.turn_harness import run_turn

SNAPSHOT = Path(__file__).parents[1] / "golden" / "request_budget.json"
# Room for wording changes; a new section or a new tool needs a reviewed snapshot.
TOLERANCE = 1.10


async def _first_request_budget() -> dict[str, Any]:
    outcome = await run_turn("org_request_budget", steps=[("say", "Done.")])
    request = outcome.model.requests[0]
    system = [m for m in request.messages if m.role == "system"]
    tools = list(request.tools or [])
    return {
        "tools": sorted(t.name for t in tools),
        "tool_definition_chars": sum(len(json.dumps(asdict(t), sort_keys=True)) for t in tools),
        "system_prompt_chars": sum(len(m.content or "") for m in system),
        "overhead_tokens": request_tokens(request.model, system, tools),
    }


async def test_the_fixed_part_of_a_request_stays_within_its_reviewed_budget() -> None:
    current = await _first_request_budget()
    if os.getenv("UPDATE_PROMPT_SNAPSHOTS"):
        SNAPSHOT.write_text(json.dumps(current, indent=2) + "\n")
    reviewed = json.loads(SNAPSHOT.read_text())

    assert current["tools"] == reviewed["tools"], "the tools sent changed: update the snapshot"
    for figure in ("tool_definition_chars", "system_prompt_chars", "overhead_tokens"):
        assert current[figure] <= reviewed[figure] * TOLERANCE, (
            f"{figure} grew from {reviewed[figure]} to {current[figure]}"
        )

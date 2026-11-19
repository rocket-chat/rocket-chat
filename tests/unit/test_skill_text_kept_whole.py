"""A skill's instructions reach the model whole up to the per-skill limit: they are the
instructions it must follow, so they are never offloaded to a file like a large tool output."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from agent_core.standing import MAX_SKILL_CHARS
from api.services.skills_lookup import _memory_skills
from turn_harness import run_turn


@pytest.fixture(autouse=True)
def _clean() -> Iterator[None]:
    _memory_skills.clear()
    yield
    _memory_skills.clear()


async def test_a_long_skill_is_given_whole_and_not_offloaded() -> None:
    org = "skill_whole"
    rules = "".join(f"- rule {n}: filter on partitions first.\n" for n in range(450))
    body = f"# Warehouse\n{rules}TRAPS-AT-THE-END"
    assert 6_000 < len(body) < MAX_SKILL_CHARS
    _memory_skills.setdefault(org, {})["s1"] = {
        "name": "warehouse",
        "description": "How to query",
        "body": body,
        "scope": "org",
        "is_active": True,
    }

    outcome = await run_turn(
        org, script=[("execute_skill", {"skill_name": "warehouse", "arguments": {}})]
    )

    last = [r for r in outcome.model.requests if r.tools][-1]
    result = next(m.content or "" for m in last.messages if m.role == "tool")
    assert "rule 300:" in result and "TRAPS-AT-THE-END" in result
    assert ".rocket/outputs/" not in result and "cut:" not in result, "the skill was cut"

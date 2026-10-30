"""The agent is told which skills exist; it cannot load what it does not know by name."""

from __future__ import annotations

from typing import Any

import pytest
from api.services.settings import save_org_config
from api.services.skills_lookup import _memory_skills, list_visible_skills
from turn_harness import run_turn


@pytest.fixture(autouse=True)
def _clean_skills() -> Any:
    _memory_skills.clear()
    yield
    _memory_skills.clear()


def _skill(org: str, skill_id: str, name: str, **extra: Any) -> None:
    _memory_skills.setdefault(org, {})[skill_id] = {
        "name": name,
        "description": f"How to {name}",
        "scope": "org",
        "is_active": True,
        **extra,
    }


def _system_text(outcome: Any) -> str:
    return "\n".join(
        str(m.content) for m in outcome.model.requests[0].messages if m.role == "system"
    )


async def test_visible_skills_are_listed_in_name_order_and_respect_the_persona_list() -> None:
    _skill("org_a", "s1", "zeta")
    _skill("org_a", "s2", "alpha")
    _skill("org_a", "s3", "mine", scope="user", user_id="u1")
    _skill("org_a", "s4", "theirs", scope="user", user_id="someone_else")
    _skill("org_a", "s5", "off", is_active=False)
    _skill("org_b", "s6", "other_org")

    await save_org_config(None, "org_a", "general", {"starter_skills": []}, [])
    everything = await list_visible_skills(None, "org_a", "u1")
    restricted = await list_visible_skills(None, "org_a", "u1", allowed_ids=["s1"])

    assert [name for name, _ in everything] == ["alpha", "mine", "zeta"]
    assert [name for name, _ in restricted] == ["zeta"]


async def test_a_turn_tells_the_model_which_skills_it_can_load() -> None:
    _skill("org_skill_prompt", "s1", "signoz-logs-traces")

    outcome = await run_turn("org_skill_prompt")

    text = _system_text(outcome)
    assert "[Skills]" in text and "`signoz-logs-traces`: How to signoz-logs-traces" in text
    assert "execute_skill" in text


async def test_without_skills_the_prompt_has_no_skills_section() -> None:
    await save_org_config(None, "org_no_skills", "general", {"starter_skills": []}, [])
    outcome = await run_turn("org_no_skills")

    assert "[Skills]" not in _system_text(outcome)

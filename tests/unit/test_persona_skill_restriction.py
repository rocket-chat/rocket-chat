"""A persona that lists skills may run only those."""

from __future__ import annotations

import asyncio

import pytest
from api.services import skills_lookup


@pytest.fixture(autouse=True)
def _skills(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        skills_lookup,
        "_memory_skills",
        {
            "org_a": {
                "sk_lint": {"name": "lint_fix", "scope": "org", "runtime": "text", "body": "x"},
                "sk_deploy": {"name": "deploy", "scope": "org", "runtime": "text", "body": "y"},
                "sk_mine": {
                    "name": "mine",
                    "scope": "user",
                    "user_id": "user_a",
                    "runtime": "text",
                    "body": "z",
                },
            },
            "org_b": {"sk_other": {"name": "deploy", "scope": "org", "runtime": "text"}},
        },
    )


def _find(name: str, allowed: tuple[str, ...] = (), user: str = "user_a", org: str = "org_a"):
    return asyncio.run(
        skills_lookup._find_visible_skill(None, org, user, name, allowed_ids=allowed)
    )


def test_a_persona_without_skills_sees_everything_the_caller_can() -> None:
    assert _find("deploy") is not None
    assert _find("mine") is not None


def test_a_persona_with_skills_runs_only_the_attached_ones() -> None:
    attached = ("sk_lint",)
    assert _find("lint_fix", attached) is not None
    assert _find("deploy", attached) is None
    assert _find("mine", attached) is None


def test_restriction_never_widens_visibility() -> None:
    assert _find("mine", ("sk_mine",), user="user_b") is None
    assert _find("deploy", ("sk_other",), org="org_a") is None

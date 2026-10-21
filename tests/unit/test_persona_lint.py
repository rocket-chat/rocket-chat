"""An agent's prompt is checked for size and for tools and arguments that do not exist."""

from __future__ import annotations

from typing import Any

import pytest
from api.services.persona_lint import MAX_PERSONA_CHARS, lint_persona

TOOLS = {
    "wait_for": ["target", "identifier", "timeout_seconds", "sha"],
    "git_push": ["branch", "force_with_lease", "path"],
    "workspace_status": [],
}


def _kinds(prompt: str) -> list[str]:
    return [w.kind for w in lint_persona(prompt, TOOLS)]


def test_a_short_prompt_with_real_tools_has_nothing_to_say() -> None:
    assert (
        _kinds("Use `git_push`, then `wait_for(ci_status, sha=abc)`. Run `workspace_status`.") == []
    )


def test_a_wrong_argument_an_unknown_tool_and_a_long_prompt_are_reported() -> None:
    # What a real persona taught its agent: a parameter wait_for does not have.
    prompt = "Use the native `wait_for` tool: `wait_for(ci_status, pr=<url_or_number>)`."
    warnings = lint_persona(prompt + " Then call `wait_until_green`.", TOOLS)

    assert [w.kind for w in warnings] == ["unknown_argument", "unknown_tool"]
    assert "`wait_for` has no `pr` parameter" in warnings[0].message
    assert "`sha`" in warnings[0].message
    assert _kinds("x" * (MAX_PERSONA_CHARS + 1)) == ["too_long"]


def test_code_identifiers_that_are_not_tool_like_are_left_alone() -> None:
    assert _kinds("Wrap errors with `fmt_errorf` and call `context_with_timeout(ctx, d)`.") == []


@pytest.fixture
def client() -> Any:
    from api.main import app
    from fastapi.testclient import TestClient

    with TestClient(app) as test_client:
        yield test_client


def _headers(role: str) -> dict[str, str]:
    return {"X-Tenant-Org-Id": "org_lint", "X-Tenant-User-Id": "u1", "X-User-Role": role}


def test_a_member_lints_a_prompt_against_the_platforms_tools(client: Any) -> None:
    reply = client.post(
        "/v1/agents/lint",
        json={"system_prompt": "Wait with `wait_for(ci_status, pr=42)`."},
        headers=_headers("developer"),
    )

    assert reply.status_code == 200
    (warning,) = reply.json()["warnings"]
    assert warning["kind"] == "unknown_argument" and "`pr`" in warning["message"]


def test_a_read_only_member_cannot_use_the_agent_editor_check(client: Any) -> None:
    reply = client.post(
        "/v1/agents/lint", json={"system_prompt": "hello"}, headers=_headers("readonly")
    )
    assert reply.status_code == 403

"""Variables every command of a session gets: the sandbox type's and the ones the agent set."""

from __future__ import annotations

from typing import Any

import pytest
from api.services.sandbox_catalogue import SandboxCatalogue
from api.services.session_env import AGENT_ENV_KEY
from api.store import InMemorySessionStore
from fastapi.testclient import TestClient
from sandbox_driver.fake_driver import FakeSandboxDriver

from tests.turn_harness import run_turn

ADMIN = {"X-Tenant-Org-Id": "env_org", "X-Tenant-User-Id": "env_admin", "X-User-Role": "admin"}
MEMBER = {**ADMIN, "X-Tenant-User-Id": "env_member", "X-User-Role": "developer"}
GO_BOX: dict[str, Any] = {
    "name": "Go",
    "image": "registry.example/go:1",
    "env": [
        {"name": "GOPRIVATE", "value": "github.com/acme/*"},
        {"name": "NPM_TOKEN", "value": "npm_s3cret", "secret": True},
    ],
}


@pytest.fixture(autouse=True)
def _fresh_catalogue(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(SandboxCatalogue, "_MEMORY", {})


def _box(client: TestClient, headers: dict[str, str], box_id: str) -> dict[str, Any]:
    boxes = client.get("/v1/sandboxes", headers=headers).json()["sandboxes"]
    return next(b for b in boxes if b["id"] == box_id)


def test_a_secret_variable_is_write_only_and_stored_encrypted(client: TestClient) -> None:
    created = client.post("/v1/admin/sandboxes", json=GO_BOX, headers=ADMIN)
    assert created.status_code == 200, created.text
    assert created.json()["env"] == [
        {"name": "GOPRIVATE", "value": "github.com/acme/*", "secret": False},
        {"name": "NPM_TOKEN", "value": None, "secret": True},
    ]
    (stored,) = [e for e in SandboxCatalogue._MEMORY.values() if e.name == "Go"]
    assert "npm_s3cret" not in str(stored.env), "the secret is sealed at rest"
    assert _box(client, MEMBER, stored.id)["env"] == [], "members never see the variables"


def test_a_secret_left_blank_keeps_its_value_and_a_new_one_needs_a_value(
    client: TestClient,
) -> None:
    box_id = client.post("/v1/admin/sandboxes", json=GO_BOX, headers=ADMIN).json()["id"]
    sealed = SandboxCatalogue._MEMORY[box_id].env[1]["value"]
    kept = client.patch(
        f"/v1/admin/sandboxes/{box_id}",
        json={"env": [{"name": "NPM_TOKEN", "value": None, "secret": True}]},
        headers=ADMIN,
    )
    assert kept.status_code == 200 and SandboxCatalogue._MEMORY[box_id].env[0]["value"] == sealed
    refused = client.patch(
        f"/v1/admin/sandboxes/{box_id}",
        json={"env": [{"name": "OTHER_TOKEN", "value": "", "secret": True}]},
        headers=ADMIN,
    )
    assert refused.status_code == 422 and "OTHER_TOKEN" in refused.json()["detail"]


@pytest.mark.parametrize("name", ["PATH", "GIT_ASKPASS", "ROCKET_X", "1BAD", "A-B"])
def test_variables_the_platform_owns_or_bad_names_are_refused(
    client: TestClient, name: str
) -> None:
    body = {"name": "Bad", "image": "b:1", "env": [{"name": name, "value": "x"}]}
    refused = client.post("/v1/admin/sandboxes", json=body, headers=ADMIN)
    assert refused.status_code == 422 and name in refused.json()["detail"]


async def test_the_sandbox_types_variables_reach_every_command_but_not_the_model() -> None:
    entry = await SandboxCatalogue(None).create("env_turn", **GO_BOX)
    outcome = await run_turn(
        "env_turn",
        script=[("bash_exec", {"command": "go mod download", "cwd": "/workspace"})],
        metadata={"sandbox_id": entry.id},
    )
    env = outcome.driver.exec_envs[-1]
    assert env["GOPRIVATE"] == "github.com/acme/*" and env["NPM_TOKEN"] == "npm_s3cret"
    prompt = _agent_prompts(outcome)
    assert "GOPRIVATE, NPM_TOKEN" in prompt and "npm_s3cret" not in prompt


async def test_a_variable_the_agent_sets_reaches_later_commands_and_later_turns() -> None:
    store = InMemorySessionStore()
    first = await run_turn(
        "env_agent",
        store=store,
        script=[
            ("env_set", {"name": "GOFLAGS", "value": "-mod=mod"}),
            ("bash_exec", {"command": "go build ./...", "cwd": "/workspace"}),
        ],
    )
    assert first.driver.exec_envs[-1]["GOFLAGS"] == "-mod=mod"
    assert first.session.metadata[AGENT_ENV_KEY] == {"GOFLAGS": "-mod=mod"}

    # A new API process: nothing in the driver, the session record carries the variable.
    second = await run_turn(
        "env_agent",
        script=[("bash_exec", {"command": "go test ./...", "cwd": "/workspace"})],
        driver=FakeSandboxDriver(),
        store=store,
    )
    assert second.driver.exec_envs[-1]["GOFLAGS"] == "-mod=mod"
    assert "set by you (env_set): GOFLAGS" in _agent_prompts(second)


async def test_unsetting_a_variable_removes_it_from_commands_and_the_session() -> None:
    outcome = await run_turn(
        "env_unset",
        script=[
            ("env_set", {"name": "GOFLAGS", "value": "-mod=mod"}),
            ("env_unset", {"name": "GOFLAGS"}),
            ("bash_exec", {"command": "go build ./...", "cwd": "/workspace"}),
        ],
    )
    assert "GOFLAGS" not in outcome.driver.exec_envs[-1]
    assert AGENT_ENV_KEY not in outcome.session.metadata


async def test_the_agent_cannot_replace_what_the_platform_sets() -> None:
    outcome = await run_turn(
        "env_reserved", script=[("env_set", {"name": "GIT_ASKPASS", "value": "/tmp/x"})]
    )
    assert "set by the platform" in outcome.text_of_events()
    assert AGENT_ENV_KEY not in outcome.session.metadata


def _agent_prompts(outcome: Any) -> str:
    """Everything the agent's own requests carried (not the title or suggestion calls)."""
    return "\n".join(str(m.content) for r in outcome.model.requests if r.tools for m in r.messages)

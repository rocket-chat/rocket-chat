"""Checkpoint list / create / rewind endpoints."""

from __future__ import annotations

from typing import Any

import pytest
from api.main import app
from fastapi.testclient import TestClient
from sandbox_driver.fake_driver import FakeSandboxDriver

from specifications.interfaces.sandbox import ExecResult, WorkspaceSpec

OWNER = {"X-Tenant-Org-Id": "test_org", "X-Tenant-User-Id": "ck_owner", "X-User-Role": "developer"}
VIEWER = {**OWNER, "X-Tenant-User-Id": "ck_viewer", "X-User-Role": "readonly"}
LISTING = (
    "rocket-checkpoint-2-turn\tbbb2222\t1700000200\nrocket-checkpoint-1\taaa1111\t1700000100\n"
)


class _RepoDriver(FakeSandboxDriver):
    def __init__(self) -> None:
        super().__init__()
        self.commands: list[str] = []

    async def exec_command(self, session_id: str, command: str, *a: Any, **k: Any) -> ExecResult:
        self.commands.append(command)
        if command.startswith("git rev-parse --git-dir"):
            return ExecResult(0, ".git\n", "", 1)
        if command.startswith("git rev-parse --show-toplevel"):
            return ExecResult(0, "/workspace\n", "", 1)
        if command.startswith("git for-each-ref"):
            return ExecResult(0, LISTING, "", 1)
        if command.startswith("git tag -l"):
            return ExecResult(0, "2\n", "", 1)
        if "commit-tree" in command:
            return ExecResult(0, "ccc3333\n", "", 1)
        if command.startswith(("git checkout", "git reset")):
            return ExecResult(0, "", "", 1)
        return await super().exec_command(session_id, command, *a, **k)


@pytest.fixture
def session_id(client: TestClient) -> str:
    created = client.post(
        "/v1/sessions", json={"title": "ckpt", "is_shared": True}, headers=OWNER
    ).json()
    return str(created["session_id"])


async def _start(driver: _RepoDriver, session_id: str) -> None:
    await driver.ensure_workspace(
        WorkspaceSpec(
            session_id=session_id, tenant_org_id="o", tenant_user_id="u", container_image="i"
        )
    )
    await driver.start_sandbox(session_id)


@pytest.fixture
def driver(client: TestClient, session_id: str) -> _RepoDriver:
    import asyncio

    repo = _RepoDriver()
    asyncio.run(_start(repo, session_id))
    app.state.sandbox_driver = repo
    return repo


def test_without_a_running_sandbox_the_list_is_unavailable_not_an_error(
    client: TestClient, session_id: str
) -> None:
    app.state.sandbox_driver = _RepoDriver()
    body = client.get(f"/v1/sessions/{session_id}/checkpoints", headers=OWNER).json()
    assert body == {"checkpoints": [], "available": False, "repository": None}


def test_checkpoints_are_listed_newest_first(
    client: TestClient, session_id: str, driver: _RepoDriver
) -> None:
    body = client.get(f"/v1/sessions/{session_id}/checkpoints", headers=OWNER).json()
    assert body["available"] is True
    assert [c["name"] for c in body["checkpoints"]] == [
        "rocket-checkpoint-2-turn",
        "rocket-checkpoint-1",
    ]


def test_a_checkpoint_can_be_created_on_demand(
    client: TestClient, session_id: str, driver: _RepoDriver
) -> None:
    response = client.post(
        f"/v1/sessions/{session_id}/checkpoints",
        json={"label": "before the refactor"},
        headers=OWNER,
    )
    assert response.status_code == 200
    assert any("commit-tree" in c and "before-the-refactor" in c for c in driver.commands)


def test_rewind_restores_only_known_checkpoints(
    client: TestClient, session_id: str, driver: _RepoDriver
) -> None:
    unknown = client.post(
        f"/v1/sessions/{session_id}/checkpoints/rewind",
        json={"reference": "main", "mode": "hard"},
        headers=OWNER,
    )
    assert unknown.status_code == 404
    assert not any(c.startswith("git reset") for c in driver.commands)

    files = client.post(
        f"/v1/sessions/{session_id}/checkpoints/rewind",
        json={"reference": "rocket-checkpoint-1"},
        headers=OWNER,
    )
    assert files.status_code == 200
    assert any("checkout 'rocket-checkpoint-1' -- ." in c for c in driver.commands)

    hard = client.post(
        f"/v1/sessions/{session_id}/checkpoints/rewind",
        json={"reference": "rocket-checkpoint-2-turn", "mode": "hard"},
        headers=OWNER,
    )
    assert hard.status_code == 200
    assert any("reset --hard 'rocket-checkpoint-2-turn'" in c for c in driver.commands)


def test_a_reference_that_is_a_shell_payload_is_refused(
    client: TestClient, session_id: str, driver: _RepoDriver
) -> None:
    response = client.post(
        f"/v1/sessions/{session_id}/checkpoints/rewind",
        json={"reference": "x; rm -rf /"},
        headers=OWNER,
    )
    assert response.status_code == 404
    assert not any("rm -rf" in c for c in driver.commands)


def test_a_read_only_member_may_look_but_not_change(
    client: TestClient, session_id: str, driver: _RepoDriver
) -> None:
    assert client.get(f"/v1/sessions/{session_id}/checkpoints", headers=VIEWER).status_code == 200
    assert (
        client.post(
            f"/v1/sessions/{session_id}/checkpoints/rewind",
            json={"reference": "rocket-checkpoint-1"},
            headers=VIEWER,
        ).status_code
        == 403
    )
    assert (
        client.post(f"/v1/sessions/{session_id}/checkpoints", json={}, headers=VIEWER).status_code
        == 403
    )


def test_changing_needs_a_running_sandbox(client: TestClient, session_id: str) -> None:
    app.state.sandbox_driver = _RepoDriver()
    response = client.post(
        f"/v1/sessions/{session_id}/checkpoints/rewind",
        json={"reference": "rocket-checkpoint-1"},
        headers=OWNER,
    )
    assert response.status_code == 409

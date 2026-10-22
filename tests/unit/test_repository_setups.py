"""Organization-defined setups run before the first command in a matching folder, once."""

from __future__ import annotations

from typing import Any

import pytest
from api.services.repository_setups import (
    RepositorySetup,
    RepositorySetupStore,
    _memory_setups,
    matching_folders,
)
from sandbox_driver.fake_driver import FakeSandboxDriver

from specifications.interfaces.sandbox import ExecResult
from tests.turn_harness import run_turn

PER_SERVICE = RepositorySetup(
    id="s1",
    repository="acme/backend-services",
    folder="*",
    command="vendir sync -l && make base",
)
ROOT = RepositorySetup(id="s2", repository="acme/*", folder="", command="go mod download")


def test_a_pattern_folder_applies_to_the_top_level_folder_a_command_runs_in() -> None:
    found = matching_folders([PER_SERVICE, ROOT], "acme/backend-services", "history/service")

    assert [(s.id, folder) for s, folder in found] == [("s2", ""), ("s1", "history")]
    assert matching_folders([PER_SERVICE], "acme/backend-services", "") == []
    assert matching_folders([PER_SERVICE], "acme/other", "history") == []
    disabled = PER_SERVICE.model_copy(update={"enabled": False})
    assert matching_folders([disabled], "acme/backend-services", "history") == []


class _Sandbox(FakeSandboxDriver):
    """A sandbox with backend-services cloned, recording what ran where."""

    def __init__(self, setup_fails: bool = False) -> None:
        super().__init__()
        self.ran: list[tuple[str, str | None]] = []
        self.markers: dict[str, str] = {}
        self.setup_fails = setup_fails

    async def exec_command(self, session_id: str, command: str, *a: Any, **k: Any) -> ExecResult:
        workdir = k.get("workdir")
        if command == "git remote get-url origin":
            return ExecResult(0, "https://github.com/acme/backend-services.git\n", "", 1)
        if command.startswith("cat /workspace/.rocket/setup-"):
            marker = command.split()[1]
            return ExecResult(
                0 if marker in self.markers else 1, self.markers.get(marker, ""), "", 1
            )
        if command.startswith("mkdir -p /workspace/.rocket && printf"):
            marker = command.rsplit("> ", 1)[1]
            self.markers[marker] = command.split("printf %s ", 1)[1].split(" >")[0].strip("'")
            return ExecResult(0, "", "", 1)
        if "vendir sync" in command:
            self.ran.append((command, workdir))
            if self.setup_fails:
                return ExecResult(2, "", "protoc failed: chdir pb: no such file or directory", 1)
            return ExecResult(0, "done", "", 1)
        self.ran.append((command, workdir))
        return ExecResult(0, "ok", "", 1)


def _tool_results(outcome: Any) -> list[str]:
    last = [r for r in outcome.model.requests if r.tools][-1]
    return [m.content or "" for m in last.messages if m.role == "tool"]


@pytest.fixture(autouse=True)
def setups() -> Any:
    _memory_setups.clear()
    yield
    _memory_setups.clear()


async def test_the_setup_runs_once_before_the_first_command_in_the_service() -> None:
    org = "org_repo_setup"
    await RepositorySetupStore(None).save(org, PER_SERVICE)
    driver = _Sandbox()
    cwd = "/workspace/backend-services/history"

    outcome = await run_turn(
        org,
        driver=driver,
        script=[
            ("bash_exec", {"command": "make test", "cwd": cwd}),
            ("bash_exec", {"command": "make build", "cwd": cwd}),
        ],
    )

    setup_runs = [r for r in driver.ran if "vendir sync" in r[0]]
    assert setup_runs == [("vendir sync -l && make base", cwd)], "once, in the service folder"
    assert [c for c, _ in driver.ran if c.startswith("make")] == ["make test", "make build"]
    results = _tool_results(outcome)
    assert results[0].startswith("[Setup of backend-services/history ran")


async def test_a_failed_setup_stops_the_command_and_shows_why() -> None:
    org = "org_repo_setup_fails"
    await RepositorySetupStore(None).save(org, PER_SERVICE)
    driver = _Sandbox(setup_fails=True)

    outcome = await run_turn(
        org,
        driver=driver,
        script=[
            ("bash_exec", {"command": "make test", "cwd": "/workspace/backend-services/history"})
        ],
    )

    assert not any(c == "make test" for c, _ in driver.ran), "the command did not run"
    results = _tool_results(outcome)
    assert "failed" in results[0] and "chdir pb" in results[0]


def _headers(role: str) -> dict[str, str]:
    return {"X-Tenant-Org-Id": "org_setups_api", "X-Tenant-User-Id": "u1", "X-User-Role": role}


def test_administrators_manage_setups_and_members_cannot() -> None:
    from api.main import app
    from fastapi.testclient import TestClient

    with TestClient(app) as client:
        body = {"repository": "Acme/API", "folder": "/svc/", "command": "make base"}
        assert (
            client.post(
                "/v1/repository-setups", json=body, headers=_headers("developer")
            ).status_code
            == 403
        )
        assert client.get("/v1/repository-setups", headers=_headers("developer")).status_code == 403

        saved = client.post("/v1/repository-setups", json=body, headers=_headers("admin")).json()
        assert (saved["repository"], saved["folder"]) == ("acme/api", "svc")
        listed = client.get("/v1/repository-setups", headers=_headers("admin")).json()
        assert [s["id"] for s in listed] == [saved["id"]]
        missing = client.delete("/v1/repository-setups/nope", headers=_headers("admin"))
        assert missing.status_code == 404
        gone = client.delete(f"/v1/repository-setups/{saved['id']}", headers=_headers("admin"))
        assert gone.status_code == 200

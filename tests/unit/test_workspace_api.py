"""Workspace browser: confined, size-limited file access for the session's person."""

from __future__ import annotations

import asyncio

import pytest
from api.main import app
from api.routers.workspace import MAX_FILE_BYTES, confine
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sandbox_driver.fake_driver import FakeSandboxDriver

from specifications.interfaces.sandbox import WorkspaceSpec

OWNER = {"X-Tenant-Org-Id": "test_org", "X-Tenant-User-Id": "ws_owner", "X-User-Role": "developer"}
VIEWER = {**OWNER, "X-Tenant-User-Id": "ws_viewer", "X-User-Role": "readonly"}
OUTSIDER = {**OWNER, "X-Tenant-User-Id": "ws_outsider"}


@pytest.mark.parametrize(
    "raw, expected",
    [
        (".", "/workspace"),
        ("", "/workspace"),
        ("src/app.py", "/workspace/src/app.py"),
        ("/workspace/src", "/workspace/src"),
        ("src/../README.md", "/workspace/README.md"),
    ],
)
def test_paths_resolve_inside_the_workspace(raw: str, expected: str) -> None:
    assert confine(raw) == expected


@pytest.mark.parametrize(
    "raw", ["..", "../etc/passwd", "/etc/passwd", "/workspace/../etc", "src/../../x"]
)
def test_paths_that_leave_the_workspace_are_refused(raw: str) -> None:
    with pytest.raises(HTTPException) as err:
        confine(raw)
    assert err.value.status_code == 400


@pytest.fixture
def session_id(client: TestClient) -> str:
    return str(
        client.post("/v1/sessions", json={"title": "ws", "is_shared": True}, headers=OWNER).json()[
            "session_id"
        ]
    )


@pytest.fixture
def driver(session_id: str) -> FakeSandboxDriver:
    fake = FakeSandboxDriver(
        seed_files={
            "README.md": "# Hello\n",
            "src/app.py": "print('hi')\n",
            "src/util/helpers.py": "x = 1\n",
            ".git/config": "[core]\n",
            "assets/logo.svg": "<svg xmlns='http://www.w3.org/2000/svg'><script>alert(1)</script></svg>",
            "bin/blob": "abc\x00def",
            "big.txt": "x" * (MAX_FILE_BYTES + 1),
        }
    )

    async def start() -> None:
        await fake.ensure_workspace(
            WorkspaceSpec(
                session_id=session_id, tenant_org_id="o", tenant_user_id="u", container_image="i"
            )
        )
        await fake.start_sandbox(session_id)

    asyncio.run(start())
    app.state.sandbox_driver = fake
    return fake


def test_a_directory_lists_folders_first_and_hides_git(
    client: TestClient, session_id: str, driver: FakeSandboxDriver
) -> None:
    root = client.get(f"/v1/sessions/{session_id}/workspace/tree", headers=OWNER).json()
    assert root["path"] == "/workspace"
    assert [(e["name"], e["is_dir"]) for e in root["entries"]] == [
        ("assets", True),
        ("bin", True),
        ("src", True),
        ("big.txt", False),
        ("README.md", False),
    ]
    inside = client.get(
        f"/v1/sessions/{session_id}/workspace/tree", params={"path": "src"}, headers=OWNER
    ).json()
    assert [(e["name"], e["is_dir"]) for e in inside["entries"]] == [
        ("util", True),
        ("app.py", False),
    ]


def test_a_file_is_read_and_unsafe_files_are_not(
    client: TestClient, session_id: str, driver: FakeSandboxDriver
) -> None:
    url = f"/v1/sessions/{session_id}/workspace/file"
    ok = client.get(url, params={"path": "src/app.py"}, headers=OWNER)
    assert ok.status_code == 200 and ok.json()["content"] == "print('hi')\n"

    assert client.get(url, params={"path": "bin/blob"}, headers=OWNER).status_code == 415
    assert client.get(url, params={"path": "big.txt"}, headers=OWNER).status_code == 413
    assert client.get(url, params={"path": "src"}, headers=OWNER).status_code == 400
    assert client.get(url, params={"path": "missing.txt"}, headers=OWNER).status_code == 404
    assert client.get(url, params={"path": "../etc/passwd"}, headers=OWNER).status_code == 400


def test_a_file_can_be_saved_by_someone_with_write_access(
    client: TestClient, session_id: str, driver: FakeSandboxDriver
) -> None:
    url = f"/v1/sessions/{session_id}/workspace/file"
    saved = client.put(
        url, json={"path": "src/app.py", "content": "print('edited')\n"}, headers=OWNER
    )
    assert saved.status_code == 200
    assert driver._files[session_id]["src/app.py"] == "print('edited')\n"

    assert client.put(url, json={"path": "../x", "content": "no"}, headers=OWNER).status_code == 400
    too_big = "x" * (MAX_FILE_BYTES + 1)
    assert (
        client.put(url, json={"path": "a.txt", "content": too_big}, headers=OWNER).status_code
        == 413
    )


def test_a_read_only_member_may_browse_but_not_save(
    client: TestClient, session_id: str, driver: FakeSandboxDriver
) -> None:
    assert (
        client.get(f"/v1/sessions/{session_id}/workspace/tree", headers=VIEWER).status_code == 200
    )
    put = client.put(
        f"/v1/sessions/{session_id}/workspace/file",
        json={"path": "README.md", "content": "defaced"},
        headers=VIEWER,
    )
    assert put.status_code == 403
    assert driver._files[session_id]["README.md"] == "# Hello\n"


def test_a_private_session_is_invisible_to_other_members(client: TestClient) -> None:
    private = client.post("/v1/sessions", json={"title": "mine"}, headers=OWNER).json()[
        "session_id"
    ]
    assert client.get(f"/v1/sessions/{private}/workspace/tree", headers=OUTSIDER).status_code == 403


def test_without_a_running_sandbox_the_answer_is_a_clear_conflict(
    client: TestClient, session_id: str
) -> None:
    app.state.sandbox_driver = FakeSandboxDriver()
    response = client.get(f"/v1/sessions/{session_id}/workspace/tree", headers=OWNER)
    assert response.status_code == 409
    assert "send a message" in response.json()["detail"]


def test_images_are_served_raw_but_sandboxed_and_nothing_else_is(
    client: TestClient, session_id: str, driver: FakeSandboxDriver
) -> None:
    url = f"/v1/sessions/{session_id}/workspace/raw"
    svg = client.get(url, params={"path": "assets/logo.svg"}, headers=OWNER)
    assert svg.status_code == 200 and svg.headers["content-type"] == "image/svg+xml"
    assert svg.headers["content-security-policy"] == "sandbox", "an SVG can carry script"
    assert svg.headers["x-content-type-options"] == "nosniff"

    assert client.get(url, params={"path": "src/app.py"}, headers=OWNER).status_code == 415
    assert client.get(url, params={"path": "assets/missing.svg"}, headers=OWNER).status_code == 404
    assert client.get(url, params={"path": "../etc/x.png"}, headers=OWNER).status_code == 400

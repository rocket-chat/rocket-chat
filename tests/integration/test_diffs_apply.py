"""HTTP integration test for per-file diff application.

The cockpit's "Apply this file" button posts one file's section of a multi-file diff; the
endpoint applies exactly that file to the session workspace through the same patch engine
the agent uses. Covered here: a new-file section lands, a non-matching path is a visible
422, and an unknown session is a 404 — all against the real ASGI surface.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
from api.main import app
from api.store import InMemorySessionStore
from httpx import ASGITransport, AsyncClient
from sandbox_driver.fake_driver import FakeSandboxDriver

HEADERS = {
    "X-Tenant-Org-Id": "default_org",
    "X-Tenant-User-Id": "dev_user",
    "X-User-Role": "developer",
}

_NEW_FILE_DIFF = (
    "--- /dev/null\n"
    "+++ b/src/added_by_diff.txt\n"
    "@@ -0,0 +1,2 @@\n"
    "+hello from a proposed diff\n"
    "+second line\n"
)


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    app.state.session_store = InMemorySessionStore()
    app.state.sandbox_driver = FakeSandboxDriver()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as async_client:
        yield async_client


async def _create_session(client: AsyncClient) -> str:
    response = await client.post("/v1/sessions", json={"title": "Diff apply"}, headers=HEADERS)
    assert response.status_code in (200, 201), response.text
    return str(response.json()["session_id"])


async def test_apply_one_file_of_a_diff_produces_the_file(client: AsyncClient) -> None:
    session_id = await _create_session(client)

    response = await client.post(
        f"/v1/sessions/{session_id}/diffs/apply",
        json={"path": "src/added_by_diff.txt", "diff_content": _NEW_FILE_DIFF},
        headers=HEADERS,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "applied"
    assert body["path"] == "src/added_by_diff.txt"

    driver: Any = app.state.sandbox_driver
    written = await driver.read_file(session_id=session_id, path="src/added_by_diff.txt")
    assert "hello from a proposed diff" in written
    assert "second line" in written


async def test_diff_with_no_section_for_the_path_is_rejected(client: AsyncClient) -> None:
    session_id = await _create_session(client)

    response = await client.post(
        f"/v1/sessions/{session_id}/diffs/apply",
        json={"path": "src/not_in_the_patch.py", "diff_content": _NEW_FILE_DIFF},
        headers=HEADERS,
    )
    assert response.status_code == 422
    assert "src/not_in_the_patch.py" in response.json()["detail"]


async def test_apply_to_unknown_session_is_not_found(client: AsyncClient) -> None:
    response = await client.post(
        "/v1/sessions/does_not_exist/diffs/apply",
        json={"path": "src/a.txt", "diff_content": _NEW_FILE_DIFF},
        headers=HEADERS,
    )
    assert response.status_code == 404

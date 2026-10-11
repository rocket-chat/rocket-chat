"""The interactive terminal: a real PTY behind the session's access rules."""

from __future__ import annotations

import json
import os
import time
import uuid
from collections.abc import Iterator

import pytest
from api.main import app
from fastapi.testclient import TestClient
from sandbox_driver.fake_driver import FakeSandboxDriver
from starlette.websockets import WebSocketDisconnect

from specifications.interfaces.sandbox import ResourceLimits, WorkspaceSpec
from specifications.interfaces.session import SessionRecord

OWNER = {
    "X-Tenant-Org-Id": "test_org",
    "X-Tenant-User-Id": "member_user",
    "X-User-Role": "developer",
}
STRANGER = {**OWNER, "X-Tenant-User-Id": "user_1"}
READONLY = {**OWNER, "X-User-Role": "readonly"}
OTHER_ORG = {
    "X-Tenant-Org-Id": "usage_org",
    "X-Tenant-User-Id": "usage_user",
    "X-User-Role": "admin",
}


@pytest.fixture
def stack(tenant_rows: None) -> Iterator[tuple[TestClient, FakeSandboxDriver, str]]:
    with TestClient(app) as client:
        driver = FakeSandboxDriver()
        app.state.sandbox_driver = driver
        session_id = f"term_{uuid.uuid4().hex[:8]}"

        async def prepare() -> None:
            await app.state.session_store.create_session(
                SessionRecord(
                    session_id=session_id,
                    tenant_org_id="test_org",
                    tenant_user_id="member_user",
                    title="t",
                )
            )
            await driver.ensure_workspace(
                WorkspaceSpec(
                    session_id, "test_org", "member_user", "img", resources=ResourceLimits()
                )
            )
            await driver.start_sandbox(session_id)

        # Run on the application's own event loop: its database engine belongs to it.
        client.portal.call(prepare)
        yield client, driver, session_id


def _drain(ws) -> list[dict]:
    """Every frame until the server closes the socket."""
    frames = []
    while True:
        frame = ws.receive()
        frames.append(frame)
        if frame["type"] == "websocket.close":
            return frames


def _first_error(ws) -> dict[str, str]:
    return json.loads(ws.receive_text())


def test_keystrokes_reach_a_shell_and_its_output_comes_back(
    stack: tuple[TestClient, FakeSandboxDriver, str],
) -> None:
    client, driver, session_id = stack
    driver.script_command(
        "echo",
        __import__("specifications.interfaces.sandbox", fromlist=["ExecResult"]).ExecResult(
            0, "hello\n", "", 1
        ),
    )

    with client.websocket_connect(f"/v1/sessions/{session_id}/terminal", headers=OWNER) as ws:
        assert ws.receive_bytes() == b"$ "
        ws.send_bytes(b"echo hi\r")
        received = b""
        while b"hello" not in received:
            received += ws.receive_bytes()

    assert b"echo hi" in received, "keystrokes are echoed by the terminal"
    assert driver.exec_calls[-1][1] == "echo hi", "the line ran in the sandbox"


def test_resizing_reaches_the_shell(stack: tuple[TestClient, FakeSandboxDriver, str]) -> None:
    client, driver, session_id = stack

    with client.websocket_connect(
        f"/v1/sessions/{session_id}/terminal?cols=100&rows=30", headers=OWNER
    ) as ws:
        ws.receive_bytes()
        assert driver.ptys[-1].size == (100, 30)
        ws.send_text(json.dumps({"type": "resize", "cols": 132, "rows": 40}))
        ws.send_text(json.dumps({"type": "ping"}))
        assert json.loads(ws.receive_text()) == {"type": "pong"}
        assert driver.ptys[-1].size == (132, 40)


def test_the_shell_exiting_ends_the_connection(
    stack: tuple[TestClient, FakeSandboxDriver, str],
) -> None:
    client, driver, session_id = stack

    with client.websocket_connect(f"/v1/sessions/{session_id}/terminal", headers=OWNER) as ws:
        ws.receive_bytes()
        ws.send_bytes(b"exit\r")
        frames = _drain(ws)
    assert any("exit" in (f.get("text") or "") for f in frames)
    assert driver.ptys[-1].closed


def test_unauthenticated_callers_are_refused(
    stack: tuple[TestClient, FakeSandboxDriver, str],
) -> None:
    _, _, session_id = stack
    # Production-like: no dev identity and no trusted headers.
    import os

    from api.main import app as application

    saved = {k: os.environ.pop(k, None) for k in ("DEV_AUTH_BYPASS", "ROCKET_TRUST_PROXY_HEADERS")}
    try:
        with pytest.raises(WebSocketDisconnect):
            with TestClient(application).websocket_connect(f"/v1/sessions/{session_id}/terminal"):
                pass
    finally:
        for key, value in saved.items():
            if value is not None:
                os.environ[key] = value


@pytest.mark.parametrize(
    "headers", [STRANGER, READONLY, OTHER_ORG], ids=["stranger", "readonly", "other-org"]
)
def test_only_writers_of_the_session_in_its_org_may_open_a_terminal(
    stack: tuple[TestClient, FakeSandboxDriver, str], headers: dict[str, str]
) -> None:
    client, driver, session_id = stack

    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(f"/v1/sessions/{session_id}/terminal", headers=headers):
            pass
    assert driver.ptys == [], "no shell was started for a refused caller"


@pytest.mark.setting("sandboxes.allow_terminal")
def test_the_org_can_disable_terminals(stack: tuple[TestClient, FakeSandboxDriver, str]) -> None:
    client, driver, session_id = stack
    saved = client.put(
        "/v1/admin/settings/sandboxes",
        json={"config": {"allow_terminal": False}},
        headers={**OWNER, "X-User-Role": "admin"},
    )
    assert saved.status_code == 200, saved.text

    with client.websocket_connect(f"/v1/sessions/{session_id}/terminal", headers=OWNER) as ws:
        assert _first_error(ws)["code"] == "disabled"
    assert driver.ptys == []


def test_a_stopped_sandbox_is_reported_not_started(
    stack: tuple[TestClient, FakeSandboxDriver, str],
) -> None:
    client, driver, session_id = stack
    client.portal.call(driver.hibernate_sandbox, session_id)

    with client.websocket_connect(f"/v1/sessions/{session_id}/terminal", headers=OWNER) as ws:
        error = _first_error(ws)
    assert error["code"] == "sandbox_not_running" and "Send a message" in error["detail"]


def test_drivers_without_a_terminal_say_so(
    stack: tuple[TestClient, FakeSandboxDriver, str],
) -> None:
    client, driver, session_id = stack

    async def no_pty(*_: object, **__: object) -> None:
        raise NotImplementedError

    driver.open_pty = no_pty  # type: ignore[method-assign]
    with client.websocket_connect(f"/v1/sessions/{session_id}/terminal", headers=OWNER) as ws:
        assert _first_error(ws)["code"] == "unsupported"


def test_each_session_has_a_cap_on_open_terminals(
    stack: tuple[TestClient, FakeSandboxDriver, str],
) -> None:
    from api.routers import terminal

    client, _, session_id = stack
    url = f"/v1/sessions/{session_id}/terminal"
    with (
        client.websocket_connect(url, headers=OWNER) as first,
        client.websocket_connect(url, headers=OWNER) as second,
    ):
        first.receive_bytes()
        second.receive_bytes()
        assert terminal.MAX_TERMINALS_PER_SESSION == 2
        with client.websocket_connect(url, headers=OWNER) as third:
            assert _first_error(third)["code"] == "too_many"


def test_idle_terminals_are_closed(
    stack: tuple[TestClient, FakeSandboxDriver, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    client, driver, session_id = stack
    monkeypatch.setenv("ROCKET_TERMINAL_IDLE_SECONDS", "0.2")

    with client.websocket_connect(f"/v1/sessions/{session_id}/terminal", headers=OWNER) as ws:
        ws.receive_bytes()
        frames = _drain(ws)
    assert any('"idle"' in (f.get("text") or "") for f in frames)
    assert driver.ptys[-1].closed


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="the audit log needs PostgreSQL")
def test_opening_and_closing_a_terminal_is_audited(
    stack: tuple[TestClient, FakeSandboxDriver, str],
) -> None:
    client, _, session_id = stack
    with client.websocket_connect(f"/v1/sessions/{session_id}/terminal", headers=OWNER) as ws:
        ws.receive_bytes()

    admin = {**OWNER, "X-User-Role": "admin"}
    summaries: list[str] = []
    for _ in range(30):  # the close is recorded just after the socket shuts
        events = client.get("/v1/admin/audit?limit=100", headers=admin).json().get("events", [])
        summaries = [e["result_summary"] for e in events if e.get("tool_name") == "terminal"]
        if any("closed the terminal" in line for line in summaries):
            break
        time.sleep(0.1)
    assert any("opened" in line for line in summaries)
    assert any("closed the terminal" in line for line in summaries)


def test_the_old_fake_terminal_input_frame_is_gone(
    stack: tuple[TestClient, FakeSandboxDriver, str],
) -> None:
    """The session socket used to broadcast typed characters to everyone as if they were output."""
    client, _, session_id = stack
    with client.websocket_connect(f"/v1/sessions/{session_id}/ws", headers=OWNER) as ws:
        ws.receive_json()  # sync
        ws.send_json({"type": "terminal_input", "data": "rm -rf /\r"})
        ws.send_json({"type": "ping"})
        assert ws.receive_json() == {"type": "pong"}, "ignored, not echoed to subscribers"

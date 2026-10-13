"""Command MCP servers end to end: registered through the API, run on a real MCP runner.

The API, the turn's tool provider and a real runner (its own server thread, real subprocesses)
are wired exactly as in a deployment, with the runner reached over its WebSocket.
"""

from __future__ import annotations

import asyncio
import pathlib
import sys
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from api.main import app
from api.services import mcp_turn
from api.store import InMemorySessionStore
from httpx import ASGITransport, AsyncClient

from tests.support.runner_thread import TOKEN, start_runner

SUPPORT = pathlib.Path(__file__).parents[1] / "support"
ENV_SERVER = f"{sys.executable} {SUPPORT / 'env_mcp_server.py'}"
ORG = "runner_org"
ADMIN = {"X-Tenant-Org-Id": ORG, "X-Tenant-User-Id": "admin_user", "X-User-Role": "admin"}
ALICE = {"X-Tenant-Org-Id": ORG, "X-Tenant-User-Id": "alice", "X-User-Role": "developer"}
BOB = {"X-Tenant-Org-Id": ORG, "X-Tenant-User-Id": "bob", "X-User-Role": "developer"}


@pytest.fixture
def runner(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    url, stop = start_runner(tmp_path)
    monkeypatch.setenv("MCP_RUNNER_URL", url)
    monkeypatch.setenv("MCP_RUNNER_TOKEN", TOKEN)
    app.state.session_store = InMemorySessionStore()
    # These tests run on the in-memory stores; another test's database must not leak in.
    monkeypatch.setattr(app.state, "db_manager", None, raising=False)
    monkeypatch.setattr(app.state, "mcp_member_tokens", None, raising=False)
    mcp_turn._mcp_servers_store.clear()
    yield url
    stop()


@pytest.fixture
async def api() -> Iterator[AsyncClient]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client


def _server(name: str, headers: list[dict[str, Any]], **auth: Any) -> dict[str, Any]:
    return {
        "name": name,
        "transport": "stdio",
        "endpoint_or_command": ENV_SERVER,
        "scope": "org",
        "auth": {"auth_type": "custom_headers", "headers": headers, **auth},
    }


async def _running(url: str) -> int:
    async with httpx.AsyncClient() as client:
        return int((await client.get(f"{url}/health")).json()["running"])


async def _whoami(user: str) -> str | None:
    provider = await mcp_turn._mcp_tools_for_turn(None, ORG, user)
    if provider is None:
        return None
    try:
        (tool,) = provider.definitions()
        return await provider.call(tool.name, {})
    finally:
        await provider.aclose()


async def test_the_runner_status_is_reported_and_absent_when_not_deployed(
    runner: str, api: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    up = (await api.get("/v1/mcp/runner", headers=ALICE)).json()
    assert up["configured"] is True and up["available"] is True

    monkeypatch.delenv("MCP_RUNNER_URL")
    off = (await api.get("/v1/mcp/runner", headers=ALICE)).json()
    assert off["configured"] is False and off["available"] is False and off["detail"]


async def test_registering_a_command_server_discovers_its_tools_on_the_runner(
    runner: str, api: AsyncClient
) -> None:
    created = await api.post(
        "/v1/mcp/servers",
        json=_server("Env", [{"name": "BASE_URL", "value": "https://grafana.example"}]),
        headers=ADMIN,
    )
    assert created.status_code in (200, 201), created.text
    body = created.json()
    assert body["status"] == "connected" and [t["name"] for t in body["tools"]] == ["whoami"]
    assert await _running(runner) == 0, "the test process is gone once the probe ends"


async def test_a_broken_command_is_reported_with_its_own_error(
    runner: str, api: AsyncClient
) -> None:
    bad = _server("Broken", [])
    bad["endpoint_or_command"] = "echo 'no such package' >&2; exit 4"
    validated = await api.post(
        "/v1/mcp/servers/validate",
        json={k: bad[k] for k in ("transport", "endpoint_or_command", "scope", "auth")},
        headers=ADMIN,
    )
    result = validated.json()
    assert result["valid"] is False and "no such package" in (result.get("error") or "")


async def test_a_member_can_use_their_own_key_and_others_keep_the_organizations(
    runner: str, api: AsyncClient
) -> None:
    created = await api.post(
        "/v1/mcp/servers",
        json=_server(
            "Grafana",
            [
                {"name": "BASE_URL", "value": "https://grafana.example"},
                {
                    "name": "API_KEY",
                    "value": "org-readonly",
                    "is_secret": True,
                    "member_override": True,
                },
            ],
        ),
        headers=ADMIN,
    )
    server_id = created.json()["id"]
    assert created.json()["overridable_env"] == ["API_KEY"]

    assert "key=org-readonly" in (await _whoami("alice") or "")

    saved = await api.put(
        f"/v1/mcp/servers/{server_id}/env", json={"values": {"API_KEY": "alice-own"}}, headers=ALICE
    )
    assert saved.status_code == 200
    assert saved.json()["variables"] == [
        {"name": "API_KEY", "has_default": True, "overridden": True}
    ]
    assert "alice-own" not in saved.text

    alice, bob = await _whoami("alice"), await _whoami("bob")
    assert "base=https://grafana.example key=alice-own" in (alice or "")
    assert "key=org-readonly" in (bob or ""), "another member keeps the organization's key"

    listed = (await api.get("/v1/mcp/servers", headers=ALICE)).json()
    assert next(s for s in listed if s["id"] == server_id)["member_env_set"] == ["API_KEY"]
    assert "alice-own" not in str(listed) and "org-readonly" not in str(listed)

    await api.put(
        f"/v1/mcp/servers/{server_id}/env", json={"values": {"API_KEY": ""}}, headers=ALICE
    )
    assert "key=org-readonly" in (await _whoami("alice") or "")


async def test_a_key_the_administrator_left_for_members_gates_the_server(
    runner: str, api: AsyncClient
) -> None:
    created = await api.post(
        "/v1/mcp/servers",
        json=_server(
            "Needs Key",
            [{"name": "API_KEY", "value": "", "is_secret": True, "member_override": True}],
        ),
        headers=ADMIN,
    )
    server_id = created.json()["id"]

    assert await _whoami("alice") is None, "no value from the member, no server"
    await api.put(
        f"/v1/mcp/servers/{server_id}/env", json={"values": {"API_KEY": "mine"}}, headers=ALICE
    )
    assert "key=mine" in (await _whoami("alice") or "")
    assert await _whoami("bob") is None, "and someone else's key is never lent out"


async def test_members_cannot_set_variables_the_administrator_did_not_open(
    runner: str, api: AsyncClient
) -> None:
    created = await api.post(
        "/v1/mcp/servers",
        json=_server("Closed", [{"name": "BASE_URL", "value": "https://x.example"}]),
        headers=ADMIN,
    )
    refused = await api.put(
        f"/v1/mcp/servers/{created.json()['id']}/env",
        json={"values": {"BASE_URL": "https://evil.example"}},
        headers=ALICE,
    )
    assert refused.status_code == 422


async def test_each_concurrent_turn_has_its_own_process_that_ends_with_the_turn(
    runner: str, api: AsyncClient
) -> None:
    await api.post(
        "/v1/mcp/servers",
        json=_server("Shared", [{"name": "API_KEY", "value": "k", "member_override": True}]),
        headers=ADMIN,
    )
    providers = [
        await mcp_turn._mcp_tools_for_turn(None, ORG, u) for u in ("alice", "bob", "carol")
    ]
    try:
        results = await asyncio.gather(
            *(p.call(p.definitions()[0].name, {}) for p in providers if p is not None)
        )
        assert len({r.split("pid=")[1] for r in results}) == 3, "three turns, three processes"
        assert await _running(runner) == 3
    finally:
        for provider in providers:
            if provider is not None:
                await provider.aclose()
    for _ in range(100):
        if await _running(runner) == 0:
            break
        await asyncio.sleep(0.05)
    assert await _running(runner) == 0, "every process ends as soon as its turn does"


async def test_an_install_command_provides_a_program_once(
    runner: str, api: AsyncClient, tmp_path: pathlib.Path
) -> None:
    runs = tmp_path / "install-runs"
    install = (
        f"echo run >> {runs}\n"
        f"printf '#!/bin/sh\\nexec {sys.executable} {SUPPORT / 'env_mcp_server.py'}\\n' > fake-mcp\n"
        "chmod +x fake-mcp\n"
    )
    body = _server("Binary", [{"name": "API_KEY", "value": "k"}], install_command=install)
    body["endpoint_or_command"] = "fake-mcp"

    created = await api.post("/v1/mcp/servers", json=body, headers=ADMIN)
    assert created.status_code in (200, 201), created.text
    assert created.json()["status"] == "connected", (created.json(), install)
    assert created.json()["install_command"] == install.strip()

    assert "key=k" in (await _whoami("alice") or "")
    assert runs.read_text().count("run") == 1, "the install script ran once, not per start"


async def test_a_program_unpacked_into_a_release_folder_is_still_found(
    runner: str, api: AsyncClient
) -> None:
    # Release tarballs such as SigNoz's unpack to <name>_linux_amd64/bin/<name>.
    install = (
        "mkdir -p nested_linux_amd64/bin\n"
        f"printf '#!/bin/sh\\nexec {sys.executable} {SUPPORT / 'env_mcp_server.py'}\\n'"
        " > nested_linux_amd64/bin/nested-mcp\n"
        "chmod +x nested_linux_amd64/bin/nested-mcp\n"
    )
    body = _server("Nested", [{"name": "API_KEY", "value": "k"}], install_command=install)
    body["endpoint_or_command"] = "nested-mcp"

    created = await api.post("/v1/mcp/servers", json=body, headers=ADMIN)

    assert created.json()["status"] == "connected", created.json()


async def test_a_failing_install_command_explains_itself(runner: str, api: AsyncClient) -> None:
    body = _server("Bad install", [], install_command="echo 'download failed' >&2; exit 9")
    body["endpoint_or_command"] = "never-installed"
    validated = await api.post(
        "/v1/mcp/servers/validate",
        json={k: body[k] for k in ("transport", "endpoint_or_command", "scope", "auth")},
        headers=ADMIN,
    )
    error = validated.json().get("error") or ""
    assert validated.json()["valid"] is False and "download failed" in error


async def test_only_administrators_register_command_servers_and_members_are_told_to_ask(
    runner: str, api: AsyncClient
) -> None:
    refused = await api.post(
        "/v1/mcp/servers", json=_server("Mine", [{"name": "A", "value": "1"}]), headers=ALICE
    )
    assert refused.status_code == 403
    assert "Administrators" in refused.json()["detail"]


async def test_changes_to_command_servers_are_audited(
    runner: str, api: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    recorded: list[dict[str, Any]] = []

    async def record(self: Any, event_type: str, **fields: Any) -> None:
        recorded.append({"event_type": event_type, **fields})

    monkeypatch.setattr("api.routers.mcp_servers.AuditService.record", record)
    created = await api.post(
        "/v1/mcp/servers",
        json=_server("Audited", [{"name": "API_KEY", "value": "", "member_override": True}]),
        headers=ADMIN,
    )
    await api.put(
        f"/v1/mcp/servers/{created.json()['id']}/env",
        json={"values": {"API_KEY": "mine"}},
        headers=ALICE,
    )

    actions = [(e["actor_user_id"], e["metadata"]["action"]) for e in recorded]
    assert actions == [("admin_user", "registered"), ("alice", "member values updated")]
    assert "mine" not in str(recorded)

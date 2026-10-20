"""Command MCP servers on PostgreSQL: members' own values are encrypted, isolated and cascade."""

from __future__ import annotations

import os
import pathlib
import sys
import uuid

import pytest
from api.db.models import McpMemberTokenModel
from api.db.session import current_tenant_org_id
from api.db.store import ensure_tenant_identity
from api.main import app
from api.services import mcp_turn
from fastapi.testclient import TestClient
from sqlalchemy import select

from tests.support.runner_thread import TOKEN, start_runner

pytestmark = pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="needs PostgreSQL")

SERVER = pathlib.Path(__file__).parents[1] / "support" / "env_mcp_server.py"
ADMIN = {"X-Tenant-Org-Id": "test_org", "X-Tenant-User-Id": "mcp_env_admin", "X-User-Role": "admin"}
MEMBER = {
    "X-Tenant-Org-Id": "test_org",
    "X-Tenant-User-Id": "mcp_env_member",
    "X-User-Role": "developer",
}
OTHER_ORG = {
    "X-Tenant-Org-Id": "usage_org",
    "X-Tenant-User-Id": "mcp_env_outsider",
    "X-User-Role": "admin",
}


def test_a_members_key_is_encrypted_used_for_their_turn_and_removed_with_the_server(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    url, stop = start_runner(tmp_path)
    monkeypatch.setenv("MCP_RUNNER_URL", url)
    monkeypatch.setenv("MCP_RUNNER_TOKEN", TOKEN)
    name = f"Grafana {uuid.uuid4().hex[:6]}"
    try:
        with TestClient(app) as client:
            manager = app.state.db_manager

            async def provision() -> None:
                async with manager.session(tenant_org_id="test_org") as session:
                    await ensure_tenant_identity(session, "test_org", "mcp_env_admin")
                    await ensure_tenant_identity(session, "test_org", "mcp_env_member")
                async with manager.session(tenant_org_id="usage_org") as session:
                    await ensure_tenant_identity(session, "usage_org", "mcp_env_outsider")

            client.portal.call(provision)
            created = client.post(
                "/v1/mcp/servers",
                json={
                    "name": name,
                    "transport": "stdio",
                    "endpoint_or_command": f"{sys.executable} {SERVER}",
                    "scope": "org",
                    "auth": {
                        "auth_type": "custom_headers",
                        "headers": [
                            {
                                "name": "API_KEY",
                                "value": "org-key",
                                "is_secret": True,
                                "member_override": True,
                            }
                        ],
                    },
                },
                headers=ADMIN,
            )
            assert created.status_code in (200, 201), created.text
            server_id = created.json()["id"]
            assert created.json()["status"] == "connected"

            assert (
                client.put(
                    f"/v1/mcp/servers/{server_id}/env",
                    json={"values": {"API_KEY": "member-secret"}},
                    headers=MEMBER,
                ).status_code
                == 200
            )
            assert (
                client.get(f"/v1/mcp/servers/{server_id}/env", headers=OTHER_ORG).status_code == 404
            )

            async def stored_and_used() -> tuple[str, str, str]:
                current_tenant_org_id.set("test_org")
                async with manager.session(tenant_org_id="test_org") as session:
                    row = (
                        await session.execute(
                            select(McpMemberTokenModel).where(
                                McpMemberTokenModel.server_id == server_id
                            )
                        )
                    ).scalar_one()
                    stored = str(row.tokens)
                outputs = []
                for user in ("mcp_env_member", "mcp_env_admin"):
                    provider = (
                        await mcp_turn._mcp_tools_for_turn(manager, "test_org", user)
                    ).provider
                    assert provider is not None
                    try:
                        mine = [d for d in provider.definitions() if "whoami" in d.name]
                        outputs.append(await provider.call(mine[0].name, {}))
                    finally:
                        await provider.aclose()
                return stored, outputs[0], outputs[1]

            stored, member_output, admin_output = client.portal.call(stored_and_used)
            assert "member-secret" not in stored, "the member's value is encrypted at rest"
            assert "key=member-secret" in member_output
            assert "key=org-key" in admin_output

            assert client.delete(f"/v1/mcp/servers/{server_id}", headers=ADMIN).status_code == 200

            async def leftovers() -> int:
                async with manager.session(tenant_org_id="test_org") as session:
                    rows = await session.execute(
                        select(McpMemberTokenModel).where(
                            McpMemberTokenModel.server_id == server_id
                        )
                    )
                    return len(rows.scalars().all())

            assert client.portal.call(leftovers) == 0, "deleting the server removes members' values"
    finally:
        app.state.mcp_member_tokens = None  # it caches the manager of the client that just closed
        stop()


def test_editing_a_server_keeps_its_stored_secret_and_refuses_a_member(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from api.db.models import McpServerEntityModel
    from api.services.mcp_turn import _cipher

    name = f"Editable {uuid.uuid4().hex[:6]}"
    server = {
        "name": name,
        "transport": "stdio",
        "endpoint_or_command": "tool serve",
        "scope": "org",
        "tools": [{"name": "echo", "description": "Echo"}],
        "auth": {
            "auth_type": "custom_headers",
            "headers": [{"name": "API_KEY", "value": "org-key", "is_secret": True}],
        },
    }
    with TestClient(app) as client:
        manager = app.state.db_manager

        async def provision() -> None:
            async with manager.session(tenant_org_id="test_org") as session:
                await ensure_tenant_identity(session, "test_org", "mcp_env_admin")
                await ensure_tenant_identity(session, "test_org", "mcp_env_member")

        async def stored_secret(server_id: str) -> str:
            async with manager.session(tenant_org_id="test_org") as session:
                entity = (
                    await session.execute(
                        select(McpServerEntityModel).where(McpServerEntityModel.id == server_id)
                    )
                ).scalar_one()
                return str(_cipher.decrypt(entity.encrypted_auth["headers"][0]["value"]))

        client.portal.call(provision)
        server_id = client.post("/v1/mcp/servers", json=server, headers=ADMIN).json()["id"]
        try:
            edit = {**server, "id": server_id, "endpoint_or_command": "tool serve --fast"}
            edit["auth"] = {
                "auth_type": "custom_headers",
                "headers": [{"name": "API_KEY", "value": None, "is_secret": True}],
            }
            saved = client.post("/v1/mcp/servers", json=edit, headers=ADMIN)
            assert saved.status_code == 200, saved.text
            assert saved.json()["endpoint_or_command"] == "tool serve --fast"
            assert client.portal.call(stored_secret, server_id) == "org-key"

            assert client.post("/v1/mcp/servers", json=edit, headers=MEMBER).status_code == 403
        finally:
            client.delete(f"/v1/mcp/servers/{server_id}", headers=ADMIN)


def test_an_edited_server_on_postgresql_still_runs_with_its_secret(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    url, stop = start_runner(tmp_path)
    monkeypatch.setenv("MCP_RUNNER_URL", url)
    monkeypatch.setenv("MCP_RUNNER_TOKEN", TOKEN)

    def server(headers: list[dict[str, object]], **extra: object) -> dict[str, object]:
        return {
            "name": f"Edited {uuid.uuid4().hex[:6]}",
            "transport": "stdio",
            "endpoint_or_command": f"{sys.executable} {SERVER}",
            "scope": "org",
            "auth": {"auth_type": "custom_headers", "headers": headers},
            **extra,
        }

    try:
        with TestClient(app) as client:
            manager = app.state.db_manager

            async def provision() -> None:
                async with manager.session(tenant_org_id="test_org") as session:
                    await ensure_tenant_identity(session, "test_org", "mcp_env_admin")

            async def runs_with() -> str:
                current_tenant_org_id.set("test_org")
                provider = (
                    await mcp_turn._mcp_tools_for_turn(manager, "test_org", "mcp_env_admin")
                ).provider
                assert provider is not None
                try:
                    mine = [d for d in provider.definitions() if "whoami" in d.name]
                    return await provider.call(mine[0].name, {})
                finally:
                    await provider.aclose()

            client.portal.call(provision)
            first = server(
                [
                    {"name": "BASE_URL", "value": "https://one.example"},
                    {"name": "API_KEY", "value": "org-key", "is_secret": True},
                ]
            )
            created = client.post("/v1/mcp/servers", json=first, headers=ADMIN)
            assert created.status_code == 200, created.text
            server_id = created.json()["id"]
            try:
                edit = {
                    **first,
                    "id": server_id,
                    "auth": {
                        "auth_type": "custom_headers",
                        "headers": [
                            {"name": "BASE_URL", "value": "https://two.example"},
                            {"name": "API_KEY", "value": None, "is_secret": True},
                        ],
                    },
                }
                saved = client.post("/v1/mcp/servers", json=edit, headers=ADMIN)
                assert saved.status_code == 200, saved.text
                assert saved.json()["status"] == "connected", saved.json()

                probed = client.post(f"/v1/mcp/servers/{server_id}/probe", headers=ADMIN)
                assert probed.json()["status"] == "connected", probed.json()
                output = client.portal.call(runs_with)
                assert "key=org-key" in output and "two.example" in output
            finally:
                client.delete(f"/v1/mcp/servers/{server_id}", headers=ADMIN)
    finally:
        app.state.mcp_member_tokens = None
        stop()

"""Roles owner/admin/member/viewer, legacy names, and capabilities per role (P2.3)."""

from __future__ import annotations

import pytest
from api.auth.capabilities import Capability, capabilities, has_capability, member_has
from api.auth.roles import (
    can_write,
    canonical_role,
    expand_roles,
    is_admin,
    primary_role,
    roles_for,
)
from api.services.settings import reset_memory_settings, save_org_config


@pytest.fixture(autouse=True)
def _clean_settings() -> None:
    reset_memory_settings()


@pytest.mark.parametrize(
    ("stored", "expanded"),
    [
        ("owner", ["owner", "admin", "member"]),
        ("admin", ["admin", "member"]),
        ("member", ["member"]),
        ("viewer", ["viewer"]),
        ("developer", ["member"]),
        ("readonly", ["viewer"]),
        ("guest", ["viewer"]),
        ("nonsense", ["viewer"]),
    ],
)
def test_stored_roles_and_their_older_names(stored: str, expanded: list[str]) -> None:
    assert roles_for(stored) == expanded


def test_claimed_roles_expand_like_stored_ones() -> None:
    assert expand_roles(["developer", "admin"]) == ["admin", "member"]
    assert expand_roles(["readonly"]) == ["viewer"]
    assert primary_role(["viewer", "owner"]) == "owner"
    assert canonical_role("Developer") == "member"


def test_viewers_cannot_write_and_guest_tokens_can() -> None:
    assert not can_write(["viewer"])
    assert can_write(["member"])
    assert can_write(["guest"])
    assert is_admin(["owner", "admin", "member"]) and not is_admin(["member"])


async def test_default_capabilities_by_role() -> None:
    member = await capabilities(None, "caps_org", ["member"])
    assert Capability.CREATE_PERSONAL_AGENTS in member
    assert Capability.BRING_OWN_KEYS in member
    assert Capability.REGISTER_MCP in member
    assert Capability.PUBLISH_AS_BOT not in member
    assert Capability.USE_BYPASS_MODE not in member
    assert Capability.MANAGE_SPEND_LIMITS not in member
    assert Capability.MANAGE_SPEND_LIMITS in await capabilities(
        None, "caps_org", ["admin", "member"]
    )
    assert await capabilities(None, "caps_org", ["viewer"]) == frozenset()


@pytest.mark.setting("access.use_bypass_mode")
async def test_owners_hold_every_capability_whatever_the_settings() -> None:
    await save_org_config(None, "caps_owner", "access", {"use_bypass_mode": []}, [])
    assert await has_capability(
        None, "caps_owner", ["owner", "admin", "member"], Capability.USE_BYPASS_MODE
    )
    assert not await has_capability(
        None, "caps_owner", ["admin", "member"], Capability.USE_BYPASS_MODE
    )
    await save_org_config(None, "caps_owner", "access", {"use_bypass_mode": ["admin"]}, [])
    assert await has_capability(None, "caps_owner", ["admin", "member"], Capability.USE_BYPASS_MODE)


@pytest.mark.setting("access.bring_own_keys")
async def test_a_capability_withdrawn_from_a_role() -> None:
    assert await member_has(None, "caps_keys", "u1", Capability.BRING_OWN_KEYS)
    await save_org_config(None, "caps_keys", "access", {"bring_own_keys": ["admin"]}, [])
    assert not await member_has(None, "caps_keys", "u1", Capability.BRING_OWN_KEYS)


@pytest.mark.setting("access.register_mcp")
async def test_registering_a_personal_mcp_server_needs_the_capability() -> None:
    from api.main import app
    from fastapi.testclient import TestClient

    member = {"X-Tenant-Org-Id": "caps_mcp", "X-Tenant-User-Id": "u1", "X-User-Role": "developer"}
    body = {
        "name": "mine",
        "transport": "http",
        "endpoint_or_command": "https://mcp.example.com/mcp",
        "scope": "user",
    }
    await save_org_config(None, "caps_mcp", "access", {"register_mcp": ["admin"]}, [])
    with TestClient(app) as client:
        app.state.db_manager = None
        refused = client.post("/v1/mcp/servers", json=body, headers=member)
    assert refused.status_code == 403 and "register_mcp" in refused.json()["detail"]

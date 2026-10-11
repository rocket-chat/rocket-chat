"""A probe reports what happened: no "assume it works" paths."""

from __future__ import annotations

import pytest
from api.services.mcp_engine import probe_and_fetch_tools


@pytest.mark.asyncio
async def test_a_working_server_is_connected_with_its_real_tools(fake_mcp_servers: object) -> None:
    status, tools, error = await probe_and_fetch_tools("http", "https://mcp.example.test/mcp")

    assert (status, error) == ("connected", None)
    assert [t["name"] for t in tools] == ["ping"]
    assert tools[0]["parameters_summary"] == "message?: string"


@pytest.mark.asyncio
@pytest.mark.parametrize("host", ["example.com", "svc.internal", "thing.test", "localhost"])
async def test_hosts_that_merely_look_internal_are_not_reported_connected(host: str) -> None:
    status, _tools, error = await probe_and_fetch_tools(
        "http", f"http://{host}:9/mcp", timeout_seconds=2
    )

    assert status in {"offline", "error"}
    assert error


@pytest.mark.asyncio
async def test_private_addresses_are_refused_not_probed() -> None:
    status, _tools, error = await probe_and_fetch_tools("sse", "http://169.254.169.254/sse")

    assert status == "error" and "non-public" in (error or "")


@pytest.mark.asyncio
async def test_a_stdio_command_is_never_executed_by_the_control_plane(tmp_path) -> None:
    """Probing a stdio connector used to run the administrator's command in the API process."""
    marker = tmp_path / "ran"
    status, tools, error = await probe_and_fetch_tools(
        "stdio", f"sh -c 'touch {marker}; sleep 5'", timeout_seconds=3
    )

    assert not marker.exists(), "the command must not run on the control plane"
    assert status == "offline" and tools == []
    assert "sandbox" in (error or "")


@pytest.mark.asyncio
async def test_an_empty_stdio_command_is_an_error() -> None:
    status, _tools, error = await probe_and_fetch_tools("stdio", "   ")
    assert status == "error" and error


@pytest.mark.asyncio
async def test_unsupported_transports_say_so() -> None:
    status, _tools, error = await probe_and_fetch_tools("websocket", "wss://example.com/mcp")

    assert status == "error" and "Unsupported" in (error or "")


def test_no_pre_seeded_builtin_servers_exist() -> None:
    from api.services import mcp_turn

    assert not any(r.get("is_builtin") for r in mcp_turn._mcp_servers_store.values())

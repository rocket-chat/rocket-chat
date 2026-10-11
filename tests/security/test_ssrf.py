"""web_fetch must not reach the platform's own network."""

from __future__ import annotations

import ipaddress

import httpx
import pytest
from agent_core.tools import safe_http
from agent_core.tools.safe_http import UnsafeURLError, is_public_address, resolve_public_target
from agent_core.tools.tier1 import web_fetch


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "10.1.2.3",
        "172.16.0.9",
        "192.168.1.1",
        "169.254.169.254",  # cloud metadata
        "100.64.0.1",  # carrier-grade NAT
        "0.0.0.0",
        "::1",
        "fe80::1",
        "fd00::1",
        "::ffff:127.0.0.1",  # IPv4-mapped loopback
        "224.0.0.1",
    ],
)
def test_non_public_addresses_are_rejected(address: str) -> None:
    assert not is_public_address(ipaddress.ip_address(address))


@pytest.mark.parametrize("address", ["8.8.8.8", "1.1.1.1", "2606:4700:4700::1111"])
def test_public_addresses_are_allowed(address: str) -> None:
    assert is_public_address(ipaddress.ip_address(address))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/",
        "http://localhost:8000/v1/sessions",
        "http://169.254.169.254/latest/meta-data/",
        "http://[::1]/",
        "http://2130706433/",  # decimal-encoded 127.0.0.1
        "file:///etc/passwd",
        "ftp://example.com/",
    ],
)
async def test_internal_targets_and_foreign_schemes_are_refused(url: str) -> None:
    with pytest.raises(UnsafeURLError):
        await resolve_public_target(url)


@pytest.mark.asyncio
async def test_web_fetch_reports_a_refusal_instead_of_connecting() -> None:
    result = await web_fetch("http://169.254.169.254/latest/meta-data/")
    assert result.startswith("Error: refusing to fetch")


@pytest.mark.asyncio
async def test_a_host_with_any_private_record_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    async def getaddrinfo(host: str, port: int, **_: object) -> list[tuple]:
        return [
            (2, 1, 6, "", ("93.184.216.34", port)),
            (2, 1, 6, "", ("10.0.0.5", port)),
        ]

    import asyncio

    loop = asyncio.get_running_loop()
    monkeypatch.setattr(loop, "getaddrinfo", getaddrinfo)
    with pytest.raises(UnsafeURLError, match="non-public"):
        await resolve_public_target("https://rebind.example/")


@pytest.mark.asyncio
async def test_redirect_to_an_internal_address_is_blocked(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_send(
        self: httpx.AsyncClient, request: httpx.Request, **_: object
    ) -> httpx.Response:
        return httpx.Response(
            302, headers={"location": "http://169.254.169.254/latest/"}, request=request
        )

    async def public_only(url: str) -> safe_http.ResolvedTarget:
        if "169.254" in url:
            raise UnsafeURLError("blocked")
        return safe_http.ResolvedTarget(host="example.com", address="93.184.216.34")

    monkeypatch.setattr(httpx.AsyncClient, "send", fake_send)
    monkeypatch.setattr(safe_http, "resolve_public_target", public_only)
    with pytest.raises(UnsafeURLError):
        await safe_http.safe_get("https://example.com/")


@pytest.mark.asyncio
async def test_connection_is_pinned_to_the_checked_address(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[httpx.Request] = []

    async def fake_send(
        self: httpx.AsyncClient, request: httpx.Request, **_: object
    ) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, content=b"ok", request=request)

    async def target(_: str) -> safe_http.ResolvedTarget:
        return safe_http.ResolvedTarget(host="example.com", address="93.184.216.34")

    monkeypatch.setattr(httpx.AsyncClient, "send", fake_send)
    monkeypatch.setattr(safe_http, "resolve_public_target", target)
    response, body = await safe_http.safe_get("https://example.com/page?q=1")

    assert body == b"ok" and response.status_code == 200
    assert seen[0].url.host == "93.184.216.34"
    assert seen[0].headers["host"] == "example.com"
    assert seen[0].extensions["sni_hostname"] == "example.com"


@pytest.mark.asyncio
async def test_private_fetch_can_be_enabled_by_the_operator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(safe_http.ALLOW_PRIVATE_ENV, "true")
    target = await resolve_public_target("http://127.0.0.1:9/")
    assert target.address == "127.0.0.1"

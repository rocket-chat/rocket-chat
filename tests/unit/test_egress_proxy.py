"""The egress proxy applies the organization's rules after DNS resolution."""

from __future__ import annotations

import asyncio
import ipaddress
from collections.abc import AsyncIterator

import pytest
from sandbox_driver import egress_proxy
from sandbox_driver.egress_proxy import Policy


def _addr(*values: str) -> list[egress_proxy.IPAddress]:
    return [ipaddress.ip_address(v) for v in values]


@pytest.mark.parametrize(
    "allow, block_internal, host, addresses, allowed",
    [
        ("", False, "anything.example", ["93.184.216.34"], True),
        ("", True, "anything.example", ["93.184.216.34"], True),
        ("", True, "internal.corp", ["10.1.2.3"], False),
        ("", True, "metadata", ["169.254.169.254"], False),
        ("", True, "loopback", ["127.0.0.1"], False),
        (
            "",
            True,
            "mixed",
            ["93.184.216.34", "192.168.1.5"],
            False,
        ),  # any internal record taints it
        ("", False, "internal.corp", ["10.1.2.3"], True),
        ("github.com", False, "github.com", ["140.82.112.3"], True),
        ("github.com", False, "GitHub.com.", ["140.82.112.3"], True),
        ("github.com", False, "evil.example", ["93.184.216.34"], False),
        ("*.pypi.org", False, "files.pypi.org", ["151.101.0.223"], True),
        ("*.pypi.org", False, "pypi.org", ["151.101.0.223"], False),
        ("*.pypi.org", False, "pypi.org.evil.example", ["93.184.216.34"], False),
        ("140.82.112.0/20", False, "any-name", ["140.82.112.3"], True),
        ("140.82.112.0/20", False, "any-name", ["93.184.216.34"], False),
        ("github.com", True, "github.com", ["10.0.0.9"], False),  # allow-listed name, internal IP
        ("", False, "nxdomain.example", [], False),
    ],
)
def test_the_policy_decision_matrix(
    allow: str, block_internal: bool, host: str, addresses: list[str], allowed: bool
) -> None:
    decision, reason = Policy.build(allow, block_internal).decide(host, _addr(*addresses))
    assert decision is allowed
    assert allowed or reason


class _Upstream:
    """A loopback server standing in for an external site: HTTP and a raw echo tunnel."""

    def __init__(self) -> None:
        self.requests: list[bytes] = []
        self.port = 0
        self._server: asyncio.Server | None = None

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        self.port = self._server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        assert self._server
        self._server.close()
        await self._server.wait_closed()

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        data = await reader.read(65536)
        self.requests.append(data)
        if data.startswith((b"GET ", b"POST ")):
            body = b"hello from upstream"
            writer.write(
                b"HTTP/1.1 200 OK\r\nContent-Length: %d\r\nConnection: close\r\n\r\n%s"
                % (len(body), body)
            )
        else:
            writer.write(b"echo:" + data)
        await writer.drain()
        writer.close()


@pytest.fixture
async def upstream() -> AsyncIterator[_Upstream]:
    server = _Upstream()
    await server.start()
    yield server
    await server.stop()


async def _proxy(policy: Policy) -> tuple[asyncio.Server, int]:
    server = await egress_proxy.serve(policy, "127.0.0.1", 0)
    return server, server.sockets[0].getsockname()[1]


async def _request(port: int, payload: bytes) -> bytes:
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(payload)
    await writer.drain()
    data = await asyncio.wait_for(reader.read(65536), 5)
    writer.close()
    return data


async def test_an_allowed_http_request_is_forwarded_in_origin_form(upstream: _Upstream) -> None:
    server, port = await _proxy(Policy.build("127.0.0.0/8", False))
    async with server:
        response = await _request(
            port,
            f"GET http://127.0.0.1:{upstream.port}/x?y=1 HTTP/1.1\r\nHost: 127.0.0.1\r\nProxy-Connection: keep-alive\r\n\r\n".encode(),
        )
    assert response.endswith(b"hello from upstream")
    forwarded = upstream.requests[0]
    assert forwarded.startswith(b"GET /x?y=1 HTTP/1.1\r\n")
    assert b"Proxy-Connection" not in forwarded and b"Connection: close" in forwarded


async def test_a_denied_request_gets_a_403_that_says_why_and_reaches_nothing(
    upstream: _Upstream,
) -> None:
    server, port = await _proxy(Policy.build("", True))  # loopback is internal
    async with server:
        response = await _request(
            port, f"GET http://127.0.0.1:{upstream.port}/ HTTP/1.1\r\nHost: x\r\n\r\n".encode()
        )
    assert response.startswith(b"HTTP/1.1 403")
    assert b"internal address" in response
    assert upstream.requests == []


async def test_connect_tunnels_only_to_allowed_destinations(upstream: _Upstream) -> None:
    server, port = await _proxy(Policy.build("127.0.0.0/8", False))
    async with server:
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.write(f"CONNECT 127.0.0.1:{upstream.port} HTTP/1.1\r\nHost: x\r\n\r\n".encode())
        await writer.drain()
        assert (await reader.readuntil(b"\r\n\r\n")).startswith(b"HTTP/1.1 200")
        writer.write(b"ping")
        await writer.drain()
        assert await asyncio.wait_for(reader.read(100), 5) == b"echo:ping"
        writer.close()

    blocked, port2 = await _proxy(Policy.build("example.org", False))
    async with blocked:
        refused = await _request(
            port2, f"CONNECT 127.0.0.1:{upstream.port} HTTP/1.1\r\n\r\n".encode()
        )
    assert refused.startswith(b"HTTP/1.1 403") and b"allowlist" in refused


async def test_the_vetted_address_is_the_one_connected_to_not_a_second_lookup(
    upstream: _Upstream, monkeypatch: pytest.MonkeyPatch
) -> None:
    lookups: list[str] = []

    async def resolve(host: str, port: int) -> list[egress_proxy.IPAddress]:
        lookups.append(host)
        return _addr("127.0.0.1")

    monkeypatch.setattr(egress_proxy, "resolve", resolve)
    server, port = await _proxy(Policy.build("app.test", False))
    async with server:
        response = await _request(
            port,
            f"GET http://app.test:{upstream.port}/ HTTP/1.1\r\nHost: app.test\r\n\r\n".encode(),
        )
    assert response.endswith(b"hello from upstream")
    assert lookups == ["app.test"], "resolved exactly once"


async def test_a_name_that_rebinds_to_an_internal_address_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def resolve(host: str, port: int) -> list[egress_proxy.IPAddress]:
        return _addr("169.254.169.254")

    monkeypatch.setattr(egress_proxy, "resolve", resolve)
    server, port = await _proxy(Policy.build("good.example", True))
    async with server:
        response = await _request(port, b"CONNECT good.example:443 HTTP/1.1\r\n\r\n")
    assert response.startswith(b"HTTP/1.1 403") and b"internal address" in response


async def test_garbage_requests_are_rejected_without_crashing_the_proxy() -> None:
    server, port = await _proxy(Policy.build("", False))
    async with server:
        assert (await _request(port, b"NOT A REQUEST\r\n\r\n")).startswith(b"HTTP/1.1 400")
        assert (await _request(port, b"GET /relative HTTP/1.1\r\n\r\n")).startswith(b"HTTP/1.1 400")
        assert (await _request(port, b"GET http://x.example/ HTTP/1.1\r\n\r\n")).startswith(
            b"HTTP/1.1 403"
        ), "an unresolvable name is refused"

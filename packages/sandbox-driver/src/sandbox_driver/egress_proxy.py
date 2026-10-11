"""An allow-listing HTTP/HTTPS proxy: the egress control for Docker sandboxes.

Docker has no per-container network policy the API can set without host privileges. A
restricted sandbox is instead attached to an *internal* network (no route out) together with
one of these proxies, which also sits on a network that does reach out. Everything that leaves
the sandbox therefore passes through here, where the organization's rules are applied to the
destination **after DNS resolution** and the vetted address is the one connected to, so a
host that rebinds to an internal address, or a name that is not on the list, is refused.

Standard library only and a single file: it is shipped into a stock Python container as
``python -c <this source>`` and configured through ``EGRESS_ALLOW`` (comma-separated CIDRs,
IPs, host names or ``*.suffix`` patterns; empty allows any public destination),
``EGRESS_BLOCK_INTERNAL`` (``1`` refuses private, loopback, link-local and metadata ranges)
and ``PORT``.
"""

from __future__ import annotations

import asyncio
import fnmatch
import ipaddress
import os
import socket
import sys
from dataclasses import dataclass, field
from urllib.parse import urlsplit

CONNECT_TIMEOUT_SECONDS = 10.0
IDLE_TIMEOUT_SECONDS = 300.0
MAX_HEAD_BYTES = 64 * 1024
MAX_CONNECTIONS = 256

IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address


def _is_internal(address: IPAddress) -> bool:
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    return not address.is_global or address.is_multicast


@dataclass(frozen=True)
class Policy:
    allow: tuple[str, ...] = ()
    block_internal: bool = False
    networks: tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...] = field(
        default=(), compare=False
    )
    hosts: tuple[str, ...] = field(default=(), compare=False)

    @classmethod
    def build(cls, allow: str, block_internal: bool) -> Policy:
        entries = tuple(part.strip().lower() for part in allow.split(",") if part.strip())
        networks, hosts = [], []
        for entry in entries:
            try:
                networks.append(ipaddress.ip_network(entry, strict=False))
            except ValueError:
                hosts.append(entry)
        return cls(entries, block_internal, tuple(networks), tuple(hosts))

    def decide(self, host: str, addresses: list[IPAddress]) -> tuple[bool, str]:
        """Whether a destination may be reached, and why not."""
        if not addresses:
            return False, "the name does not resolve"
        if self.block_internal and any(_is_internal(a) for a in addresses):
            return False, "it resolves to an internal address"
        if not self.allow:
            return True, ""
        name = host.lower().rstrip(".")
        if any(fnmatch.fnmatchcase(name, pattern) or name == pattern for pattern in self.hosts):
            return True, ""
        if any(a in network for a in addresses for network in self.networks):
            return True, ""
        return False, "it is not on the organization's egress allowlist"


async def resolve(host: str, port: int) -> list[IPAddress]:
    try:
        return [ipaddress.ip_address(host.strip("[]"))]
    except ValueError:
        pass
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(
            host, port, type=socket.SOCK_STREAM, proto=socket.IPPROTO_TCP
        )
    except socket.gaierror:
        return []
    return [ipaddress.ip_address(str(info[4][0]).split("%")[0]) for info in infos]


async def _pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while data := await asyncio.wait_for(reader.read(65536), IDLE_TIMEOUT_SECONDS):
            writer.write(data)
            await writer.drain()
    except (TimeoutError, ConnectionError, OSError):
        pass
    finally:
        try:
            writer.close()
        except OSError:
            pass


def _refuse(host: str, reason: str) -> bytes:
    body = f"Blocked by the organization's egress policy: {host} - {reason}.\n".encode()
    return (
        b"HTTP/1.1 403 Forbidden\r\nContent-Type: text/plain\r\nConnection: close\r\n"
        + f"Content-Length: {len(body)}\r\n\r\n".encode()
        + body
    )


class EgressProxy:
    def __init__(self, policy: Policy) -> None:
        self.policy = policy
        self._slots = asyncio.Semaphore(MAX_CONNECTIONS)
        self.decisions: list[tuple[str, bool]] = []

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        async with self._slots:
            try:
                await self._serve(reader, writer)
            except (ConnectionError, OSError, TimeoutError, asyncio.IncompleteReadError):
                pass
            finally:
                try:
                    writer.close()
                except OSError:
                    pass

    async def _serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), IDLE_TIMEOUT_SECONDS)
        if len(head) > MAX_HEAD_BYTES:
            return
        request_line, _, rest = head.partition(b"\r\n")
        try:
            method, target, version = request_line.decode("latin-1").split(" ", 2)
        except ValueError:
            writer.write(b"HTTP/1.1 400 Bad Request\r\nConnection: close\r\n\r\n")
            return
        if method.upper() == "CONNECT":
            host, _, port_text = target.rpartition(":")
            port = int(port_text) if port_text.isdigit() else 443
            upstream = await self._open(host, port, writer)
            if upstream is None:
                return
            writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            await writer.drain()
            await asyncio.gather(_pipe(reader, upstream[1]), _pipe(upstream[0], writer))
            return
        parts = urlsplit(target)
        if parts.scheme != "http" or not parts.hostname:
            writer.write(b"HTTP/1.1 400 Bad Request\r\nConnection: close\r\n\r\n")
            return
        port = parts.port or 80
        upstream = await self._open(parts.hostname, port, writer)
        if upstream is None:
            return
        origin = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
        lines = [
            ln
            for ln in rest.split(b"\r\n")
            if ln and not ln.lower().startswith((b"proxy-connection:", b"connection:"))
        ]
        forwarded = (
            f"{method} {origin} {version}\r\n".encode("latin-1")
            + b"\r\n".join(lines)
            + b"\r\nConnection: close\r\n\r\n"
        )
        upstream[1].write(forwarded)
        await upstream[1].drain()
        await asyncio.gather(_pipe(reader, upstream[1]), _pipe(upstream[0], writer))

    async def _open(
        self, host: str, port: int, client: asyncio.StreamWriter
    ) -> tuple[asyncio.StreamReader, asyncio.StreamWriter] | None:
        addresses = await resolve(host, port)
        allowed, reason = self.policy.decide(host, addresses)
        self.decisions.append((f"{host}:{port}", allowed))
        print(
            f"egress {'ALLOW' if allowed else 'DENY '} {host}:{port} {reason}".strip(), flush=True
        )
        if not allowed:
            client.write(_refuse(host, reason))
            await client.drain()
            return None
        # Connect to the address that was vetted, not to whatever the name resolves to next.
        for address in addresses:
            try:
                return await asyncio.wait_for(
                    asyncio.open_connection(str(address), port), CONNECT_TIMEOUT_SECONDS
                )
            except (OSError, TimeoutError):
                continue
        client.write(b"HTTP/1.1 502 Bad Gateway\r\nConnection: close\r\n\r\n")
        await client.drain()
        return None


async def serve(policy: Policy, host: str = "0.0.0.0", port: int = 3128) -> asyncio.Server:
    proxy = EgressProxy(policy)
    server = await asyncio.start_server(proxy.handle, host, port)
    server.proxy = proxy  # type: ignore[attr-defined]
    return server


def main() -> None:  # pragma: no cover - the container entrypoint
    policy = Policy.build(
        os.environ.get("EGRESS_ALLOW", ""), os.environ.get("EGRESS_BLOCK_INTERNAL") == "1"
    )
    port = int(os.environ.get("PORT", "3128"))

    async def run() -> None:
        server = await serve(policy, port=port)
        print(
            f"egress proxy listening on :{port} policy={policy.allow} "
            f"block_internal={policy.block_internal}",
            flush=True,
        )
        async with server:
            await server.serve_forever()

    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        sys.exit(0)


if __name__ == "__main__":  # pragma: no cover
    main()

"""SSRF-safe HTTP fetching for agent tools.

An agent fetches URLs chosen by a model that may be steered by untrusted page or chat content,
so a fetch must never reach the platform's own network (cloud metadata, the database, the
control plane). Every hop is resolved and checked, the connection is pinned to the address that
was checked (defeating DNS rebinding), and body size is capped.
"""

from __future__ import annotations

import asyncio
import ipaddress
import os
import socket
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

import httpx

MAX_REDIRECTS = 5
MAX_RESPONSE_BYTES = 5_000_000
ALLOW_PRIVATE_ENV = "ROCKET_WEB_FETCH_ALLOW_PRIVATE"
PROXY_ENV = "ROCKET_WEB_FETCH_PROXY"

IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address


class UnsafeURLError(ValueError):
    """The URL is malformed or points somewhere agents may not fetch from."""


@dataclass(frozen=True)
class ResolvedTarget:
    host: str
    address: str


def is_public_address(address: IPAddress) -> bool:
    """True only for globally routable addresses (not private, loopback, link-local, CGNAT...)."""
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    return address.is_global and not address.is_multicast


def private_fetch_allowed() -> bool:
    """Self-hosters may opt in to fetching internal URLs (docs sites, internal wikis)."""
    return os.getenv(ALLOW_PRIVATE_ENV, "").lower() in {"1", "true", "yes"}


async def resolve_public_target(url: str) -> ResolvedTarget:
    """Validate ``url`` and return the single address the request must connect to."""
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise UnsafeURLError(f"Only http and https URLs are supported, got '{parsed.scheme}'.")
    host = parsed.hostname
    if not host:
        raise UnsafeURLError("The URL has no host.")

    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(
            host, port, type=socket.SOCK_STREAM, proto=socket.IPPROTO_TCP
        )
    except socket.gaierror as err:
        raise UnsafeURLError(f"Could not resolve '{host}'.") from err

    addresses = [ipaddress.ip_address(str(info[4][0]).split("%")[0]) for info in infos]
    if not addresses:
        raise UnsafeURLError(f"'{host}' has no address.")
    if not private_fetch_allowed():
        # Every record must be public: a host that also resolves privately is a rebinding vector.
        blocked = [a for a in addresses if not is_public_address(a)]
        if blocked:
            raise UnsafeURLError(
                f"'{host}' resolves to a non-public address ({blocked[0]}); fetching it is blocked."
            )
    return ResolvedTarget(host=host, address=str(addresses[0]))


async def _read_capped(response: httpx.Response, max_bytes: int) -> bytes:
    body = bytearray()
    async for chunk in response.aiter_bytes():
        body.extend(chunk)
        if len(body) > max_bytes:
            return bytes(body[:max_bytes])
    return bytes(body)


async def safe_get(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    timeout_seconds: float = 15.0,
    max_bytes: int = MAX_RESPONSE_BYTES,
) -> tuple[httpx.Response, bytes]:
    """GET ``url`` following redirects manually, vetting every hop.

    Returns the final response (headers/status) and its body, truncated to ``max_bytes``.
    Raises ``UnsafeURLError`` for blocked targets and ``httpx.HTTPError`` for transport errors.
    """
    current = url
    # Behind an egress proxy the proxy performs the connection (and its own allowlisting), so
    # the address cannot be pinned here; every hop is still vetted before it is requested.
    proxy = os.getenv(PROXY_ENV, "").strip() or None
    async with httpx.AsyncClient(
        timeout=timeout_seconds, follow_redirects=False, proxy=proxy
    ) as client:
        for _ in range(MAX_REDIRECTS + 1):
            target = await resolve_public_target(current)
            parsed = urlparse(current)
            if proxy:
                request = client.build_request("GET", current, headers=headers)
            else:
                pinned = parsed._replace(
                    netloc=f"[{target.address}]" if ":" in target.address else target.address
                )
                if parsed.port:
                    pinned = pinned._replace(netloc=f"{pinned.netloc}:{parsed.port}")
                request = client.build_request(
                    "GET",
                    pinned.geturl(),
                    headers={**(headers or {}), "Host": parsed.netloc},
                    # TLS must still verify the original hostname, not the pinned address.
                    extensions={"sni_hostname": target.host},
                )
            response = await client.send(request, stream=True)
            try:
                if response.is_redirect and "location" in response.headers:
                    current = urljoin(current, response.headers["location"])
                    continue
                return response, await _read_capped(response, max_bytes)
            finally:
                await response.aclose()
    raise UnsafeURLError(f"Too many redirects (more than {MAX_REDIRECTS}).")

"""Egress enforcement for Docker sandboxes.

A restricted sandbox joins an *internal* Docker network, which has no route to anywhere, and
reaches the outside only through an allow-listing proxy (``egress_proxy``) that is attached to
both that network and the default one. One proxy serves every sandbox with the same policy.
Ordinary tools honour the standard proxy variables set here (git, pip, npm, uv, curl); a program
that opens a raw socket simply finds no route, which is the point.
"""

from __future__ import annotations

import hashlib
import inspect
import logging
import os
import time
from dataclasses import dataclass

import docker
import docker.errors

from sandbox_driver import egress_proxy
from specifications.interfaces.sandbox import WorkspaceSpec

logger = logging.getLogger(__name__)

INTERNAL_NETWORK = "rocket-sandbox-egress"
PROXY_PORT = 3128
# Lets sandboxes and their proxy reach services published on the Docker host (the API's git
# gateway) by the same name on Docker Desktop and on Linux engines.
HOST_GATEWAY = {"host.docker.internal": "host-gateway"}
POLICY_LABEL = "rocket.egress.policy"
PROXY_LABEL = "rocket.egress.proxy"
DEFAULT_PROXY_IMAGE = "python:3.12-slim"
PROXY_READY_TIMEOUT_SECONDS = 15.0


@dataclass(frozen=True)
class EgressSettings:
    """What a restricted sandbox needs to be started behind the proxy."""

    policy_id: str
    network: str
    proxy_name: str
    environment: dict[str, str]


def is_restricted(spec: WorkspaceSpec) -> bool:
    return bool(spec.egress_allowlist) or spec.block_internal_subnets


def policy_id(spec: WorkspaceSpec) -> str:
    canonical = "|".join(sorted(e.strip().lower() for e in spec.egress_allowlist if e.strip()))
    canonical += f"#{int(spec.block_internal_subnets)}"
    canonical += "#" + ",".join(sorted(spec.platform_endpoints))
    return hashlib.sha256(canonical.encode()).hexdigest()[:12]


def proxy_name(policy: str) -> str:
    return f"rocket-egress-{policy}"


def proxy_environment(name: str) -> dict[str, str]:
    url = f"http://{name}:{PROXY_PORT}"
    no_proxy = "localhost,127.0.0.1,::1"
    return {
        "HTTP_PROXY": url,
        "HTTPS_PROXY": url,
        "http_proxy": url,
        "https_proxy": url,
        "NO_PROXY": no_proxy,
        "no_proxy": no_proxy,
    }


def ensure_egress(client: docker.DockerClient, spec: WorkspaceSpec) -> EgressSettings:
    """Create (once) the internal network and this policy's proxy; return how to attach."""
    policy = policy_id(spec)
    name = proxy_name(policy)
    try:
        internal = client.networks.get(INTERNAL_NETWORK)
    except docker.errors.NotFound:
        try:
            internal = client.networks.create(
                INTERNAL_NETWORK,
                driver="bridge",
                internal=True,
                labels={"rocket.managed": "true"},
            )
        except docker.errors.APIError as err:
            if getattr(err.response, "status_code", None) != 409:
                raise
            internal = client.networks.get(INTERNAL_NETWORK)

    try:
        proxy = client.containers.get(name)
        if proxy.status != "running":
            proxy.start()
    except docker.errors.NotFound:
        image = os.getenv("ROCKET_EGRESS_PROXY_IMAGE", DEFAULT_PROXY_IMAGE)
        try:
            client.images.get(image)
        except docker.errors.ImageNotFound:
            client.images.pull(image)
        try:
            proxy = client.containers.run(
                image=image,
                name=name,
                command=["python", "-c", inspect.getsource(egress_proxy)],
                detach=True,
                environment={
                    "EGRESS_ALLOW": ",".join(spec.egress_allowlist),
                    "EGRESS_BLOCK_INTERNAL": "1" if spec.block_internal_subnets else "0",
                    "EGRESS_PINNED": ",".join(spec.platform_endpoints),
                    "PORT": str(PROXY_PORT),
                },
                extra_hosts=HOST_GATEWAY,
                labels={"rocket.managed": "true", PROXY_LABEL: policy},
                cap_drop=["ALL"],
                security_opt=["no-new-privileges:true"],
                read_only=True,
                user="65534:65534",
                mem_limit="256m",
                pids_limit=256,
                restart_policy={"Name": "always"},
            )
            internal.connect(proxy, aliases=[name])
        except docker.errors.APIError as err:
            if getattr(err.response, "status_code", None) != 409:
                raise
        _wait_until_listening(client, name)
    else:
        if not any(n == INTERNAL_NETWORK for n in proxy.attrs["NetworkSettings"]["Networks"]):
            internal.connect(proxy, aliases=[name])
    return EgressSettings(policy, INTERNAL_NETWORK, name, proxy_environment(name))


def _wait_until_listening(client: docker.DockerClient, name: str) -> None:
    """Block until a new proxy accepts connections (its start-up line), or a short deadline.

    The proxy's Python takes a moment to start; a sandbox whose first command ran before that
    found nothing on port 3128 and failed. Runs in the driver's worker thread, never on the loop.
    """
    deadline = time.monotonic() + PROXY_READY_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        try:
            if b"egress proxy listening" in client.containers.get(name).logs(tail=20):
                return
        except docker.errors.NotFound:
            pass
        time.sleep(0.1)
    logger.warning(
        "Egress proxy %s did not report ready within %ss", name, PROXY_READY_TIMEOUT_SECONDS
    )


def remove_unused_proxies(client: docker.DockerClient) -> list[str]:
    """Remove proxies no sandbox container refers to any more."""
    in_use = {
        c.labels.get(POLICY_LABEL)
        for c in client.containers.list(all=True, filters={"label": POLICY_LABEL})
    }
    removed: list[str] = []
    for proxy in client.containers.list(all=True, filters={"label": PROXY_LABEL}):
        if proxy.labels.get(PROXY_LABEL) not in in_use:
            proxy.remove(force=True)
            removed.append(proxy.name or proxy.short_id)
    return removed

"""Per-sandbox egress policy for Kubernetes.

An organization can restrict where its sandboxes may connect (``governance.egress``). Kubernetes
NetworkPolicies are *additive*, so a restricted sandbox must not also match the namespace-wide
policy that lets sandboxes reach the internet: the chart's namespace policy excludes pods labelled
``rocket.chat/egress=restricted``, and the driver gives each such pod its own policy that allows
DNS plus the allowlisted destinations only.

Entries may be CIDR blocks, single IPs or host names. Host names are resolved once, when the
sandbox starts: a policy cannot name a host, and a CDN-fronted host can change address later. For
host-based rules that track DNS, use a CNI with FQDN policies (e.g. Cilium) instead.
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import os
import socket
from collections.abc import Iterable
from dataclasses import dataclass

from kubernetes_asyncio import client

from specifications.interfaces.sandbox import WorkspaceSpec

logger = logging.getLogger(__name__)

EGRESS_LABEL = "rocket.chat/egress"
EGRESS_RESTRICTED = "restricted"
DNS_PORT = 53


async def resolve_destinations(entries: Iterable[str]) -> list[str]:
    """CIDR strings for every allowlist entry that is a network, an IP or a resolvable host."""
    cidrs: list[str] = []
    for raw in entries:
        entry = raw.strip()
        if not entry:
            continue
        try:
            cidrs.append(str(ipaddress.ip_network(entry, strict=False)))
            continue
        except ValueError:
            pass
        try:
            infos = await asyncio.get_running_loop().getaddrinfo(
                entry, None, type=socket.SOCK_STREAM
            )
        except socket.gaierror:
            # Unresolvable means unreachable under this policy: the safe reading of a typo.
            logger.warning("Egress allowlist host '%s' did not resolve; it stays blocked.", entry)
            continue
        for info in infos:
            address = ipaddress.ip_address(str(info[4][0]).split("%")[0])
            cidrs.append(f"{address}/{address.max_prefixlen}")
    return sorted(set(cidrs))


# Never reachable from a sandbox that is told to stay off internal networks: RFC 1918 ranges
# and the cloud metadata endpoint (which would hand out the node's credentials).
INTERNAL_RANGES = ("169.254.169.254/32", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")


@dataclass(frozen=True)
class PlatformPeer:
    """The platform's own pods a sandbox must reach whatever its policy (the git gateway).

    A NetworkPolicy is enforced after a Service's address is translated to a pod's, so the rule
    names the API pods (namespace and labels) and their container port, never the Service.
    """

    namespace: str
    labels: dict[str, str]
    port: int

    @classmethod
    def from_env(cls) -> PlatformPeer | None:
        namespace = os.getenv("ROCKET_PLATFORM_NAMESPACE", "").strip()
        raw_labels = os.getenv("ROCKET_PLATFORM_POD_LABELS", "")
        labels = dict(
            pair.split("=", 1) for pair in raw_labels.split(",") if "=" in pair and pair.strip()
        )
        port = os.getenv("ROCKET_PLATFORM_POD_PORT", "8000").strip()
        if not namespace or not labels or not port.isdigit():
            return None
        return cls(namespace, {k.strip(): v.strip() for k, v in labels.items()}, int(port))

    def rule(self) -> client.V1NetworkPolicyEgressRule:
        return client.V1NetworkPolicyEgressRule(
            to=[
                client.V1NetworkPolicyPeer(
                    namespace_selector=client.V1LabelSelector(
                        match_labels={"kubernetes.io/metadata.name": self.namespace}
                    ),
                    pod_selector=client.V1LabelSelector(match_labels=self.labels),
                )
            ],
            ports=[client.V1NetworkPolicyPort(protocol="TCP", port=self.port)],
        )


def is_restricted(spec: WorkspaceSpec) -> bool:
    """Whether the organization's governance asks for a per-sandbox policy at all."""
    return bool(spec.egress_allowlist) or spec.block_internal_subnets


def build_egress_policy(
    *,
    name: str,
    namespace: str,
    session_label: str,
    cidrs: list[str],
    labels: dict[str, str],
    internet_except_internal: bool = False,
    platform_peer: PlatformPeer | None = None,
) -> client.V1NetworkPolicy:
    """A NetworkPolicy allowing one pod DNS plus connections to ``cidrs``, nothing else.

    With ``internet_except_internal`` (no explicit allowlist) the pod may reach the public
    internet but none of the private ranges. ``platform_peer`` is always reachable.
    """
    rules = [
        client.V1NetworkPolicyEgressRule(
            ports=[
                client.V1NetworkPolicyPort(protocol="UDP", port=DNS_PORT),
                client.V1NetworkPolicyPort(protocol="TCP", port=DNS_PORT),
            ]
        )
    ]
    if platform_peer is not None:
        rules.append(platform_peer.rule())
    if internet_except_internal:
        rules.append(
            client.V1NetworkPolicyEgressRule(
                to=[
                    client.V1NetworkPolicyPeer(
                        ip_block=client.V1IPBlock(cidr="0.0.0.0/0", _except=list(INTERNAL_RANGES))
                    )
                ]
            )
        )
    elif cidrs:
        rules.append(
            client.V1NetworkPolicyEgressRule(
                to=[
                    client.V1NetworkPolicyPeer(ip_block=client.V1IPBlock(cidr=cidr))
                    for cidr in cidrs
                ]
            )
        )
    return client.V1NetworkPolicy(
        metadata=client.V1ObjectMeta(name=name, namespace=namespace, labels=labels),
        spec=client.V1NetworkPolicySpec(
            pod_selector=client.V1LabelSelector(match_labels={"agent.session.id": session_label}),
            policy_types=["Egress"],
            egress=rules,
        ),
    )

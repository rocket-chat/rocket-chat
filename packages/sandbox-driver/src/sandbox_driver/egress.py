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
import socket
from collections.abc import Iterable

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
) -> client.V1NetworkPolicy:
    """A NetworkPolicy allowing one pod DNS plus connections to ``cidrs``, nothing else.

    With ``internet_except_internal`` (no explicit allowlist) the pod may reach the public
    internet but none of the private ranges.
    """
    rules = [
        client.V1NetworkPolicyEgressRule(
            ports=[
                client.V1NetworkPolicyPort(protocol="UDP", port=DNS_PORT),
                client.V1NetworkPolicyPort(protocol="TCP", port=DNS_PORT),
            ]
        )
    ]
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

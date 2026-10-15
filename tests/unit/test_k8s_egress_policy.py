"""Organization egress restrictions are enforced per sandbox on Kubernetes."""

from __future__ import annotations

import shutil
import socket
import subprocess
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
import yaml
from kubernetes_asyncio.client.exceptions import ApiException
from sandbox_driver import egress
from sandbox_driver.egress import EGRESS_LABEL, build_egress_policy, resolve_destinations
from sandbox_driver.k8s_driver import K8sSandboxDriver

from specifications.interfaces.sandbox import ResourceLimits, WorkspaceSpec

REPO_ROOT = Path(__file__).resolve().parents[2]


def _spec(allowlist: list[str] | None = None) -> WorkspaceSpec:
    return WorkspaceSpec(
        session_id="sess_egress",
        tenant_org_id="org_a",
        tenant_user_id="user_a",
        container_image="python:3.12-slim",
        resources=ResourceLimits(),
        egress_allowlist=allowlist or [],
    )


def _driver(spec: WorkspaceSpec) -> tuple[K8sSandboxDriver, MagicMock, MagicMock]:
    core_v1 = MagicMock()
    core_v1.api_client = MagicMock()
    for name in (
        "read_namespaced_persistent_volume_claim",
        "create_namespaced_persistent_volume_claim",
        "read_namespaced_pod",
        "create_namespaced_pod",
        "delete_namespaced_pod",
        "delete_namespaced_persistent_volume_claim",
    ):
        setattr(core_v1, name, AsyncMock())
    core_v1.read_namespaced_persistent_volume_claim.return_value = MagicMock()
    running = MagicMock()
    running.status.phase = "Running"
    running.spec.node_name = "node-1"
    core_v1.read_namespaced_pod.side_effect = [ApiException(status=404), running]
    networking = MagicMock()
    networking.create_namespaced_network_policy = AsyncMock()
    networking.replace_namespaced_network_policy = AsyncMock()
    networking.delete_namespaced_network_policy = AsyncMock()
    driver = K8sSandboxDriver(
        namespace="agent-sandboxes",
        core_v1_api=core_v1,
        ws_core_v1_api=MagicMock(),
        networking_v1_api=networking,
    )
    driver._specs[spec.session_id] = spec
    return driver, core_v1, networking


@pytest.mark.asyncio
async def test_networks_and_addresses_pass_through_and_hosts_are_resolved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def getaddrinfo(host: str, port: object, **_: object) -> list[tuple]:
        assert host == "pypi.example"
        return [
            (2, 1, 6, "", ("151.101.0.223", 0)),
            (10, 1, 6, "", ("2a04:4e42::223%eth0", 0, 0, 0)),
        ]

    loop = __import__("asyncio").get_running_loop()
    monkeypatch.setattr(loop, "getaddrinfo", getaddrinfo)

    cidrs = await resolve_destinations(["10.20.0.0/16", "203.0.113.7", "pypi.example", "  "])

    assert cidrs == sorted(
        {"10.20.0.0/16", "203.0.113.7/32", "151.101.0.223/32", "2a04:4e42::223/128"}
    )


@pytest.mark.asyncio
async def test_an_unresolvable_host_stays_blocked(monkeypatch: pytest.MonkeyPatch) -> None:
    async def failing(host: str, port: object, **_: object) -> list[tuple]:
        raise socket.gaierror("no such host")

    monkeypatch.setattr(__import__("asyncio").get_running_loop(), "getaddrinfo", failing)

    assert await resolve_destinations(["typo.example"]) == []


def test_the_policy_allows_dns_and_the_listed_destinations_only() -> None:
    policy = build_egress_policy(
        name="np-session-x",
        namespace="agent-sandboxes",
        session_label="sess-x",
        cidrs=["10.1.0.0/16", "1.2.3.4/32"],
        labels={"a": "b"},
    )

    assert policy.spec.pod_selector.match_labels == {"agent.session.id": "sess-x"}
    assert policy.spec.policy_types == ["Egress"]
    dns, destinations = policy.spec.egress
    assert {(p.protocol, p.port) for p in dns.ports} == {("UDP", 53), ("TCP", 53)}
    assert dns.to is None, "DNS rule carries ports only"
    assert [peer.ip_block.cidr for peer in destinations.to] == ["10.1.0.0/16", "1.2.3.4/32"]


def test_an_empty_allowlist_after_resolution_means_dns_only() -> None:
    policy = build_egress_policy(name="n", namespace="ns", session_label="s", cidrs=[], labels={})

    assert len(policy.spec.egress) == 1


@pytest.mark.asyncio
async def test_a_restricted_sandbox_gets_its_policy_before_its_pod_and_a_label() -> None:
    spec = _spec(["203.0.113.0/24"])
    driver, core_v1, networking = _driver(spec)
    order: list[str] = []
    networking.create_namespaced_network_policy.side_effect = lambda **_: order.append("policy")
    core_v1.create_namespaced_pod.side_effect = lambda **_: order.append("pod")

    await driver.start_sandbox(spec.session_id)

    assert order == ["policy", "pod"], "the pod must never run unrestricted, even briefly"
    pod = core_v1.create_namespaced_pod.call_args.kwargs["body"]
    assert pod.metadata.labels[EGRESS_LABEL] == "restricted"
    policy = networking.create_namespaced_network_policy.call_args.kwargs["body"]
    assert policy.metadata.name == "np-session-sess-egress"
    assert policy.spec.egress[1].to[0].ip_block.cidr == "203.0.113.0/24"


@pytest.mark.asyncio
async def test_an_unrestricted_sandbox_gets_no_policy_and_no_label() -> None:
    spec = _spec()
    driver, core_v1, networking = _driver(spec)

    await driver.start_sandbox(spec.session_id)

    networking.create_namespaced_network_policy.assert_not_called()
    pod = core_v1.create_namespaced_pod.call_args.kwargs["body"]
    assert EGRESS_LABEL not in pod.metadata.labels


@pytest.mark.asyncio
async def test_an_existing_policy_is_replaced_not_duplicated() -> None:
    spec = _spec(["203.0.113.0/24"])
    driver, _, networking = _driver(spec)
    networking.create_namespaced_network_policy.side_effect = ApiException(status=409)

    await driver.start_sandbox(spec.session_id)

    networking.replace_namespaced_network_policy.assert_awaited_once()


@pytest.mark.asyncio
async def test_a_policy_failure_stops_the_sandbox_from_starting() -> None:
    spec = _spec(["203.0.113.0/24"])
    driver, core_v1, networking = _driver(spec)
    networking.create_namespaced_network_policy.side_effect = ApiException(status=403)

    with pytest.raises(Exception, match="egress policy"):
        await driver.start_sandbox(spec.session_id)

    core_v1.create_namespaced_pod.assert_not_called()


@pytest.mark.asyncio
async def test_destroying_a_sandbox_removes_its_policy_and_tolerates_absence() -> None:
    spec = _spec(["203.0.113.0/24"])
    driver, _, networking = _driver(spec)

    await driver.destroy_workspace(spec.session_id)
    networking.delete_namespaced_network_policy.assert_awaited_once_with(
        name="np-session-sess-egress", namespace="agent-sandboxes"
    )

    networking.delete_namespaced_network_policy.side_effect = ApiException(status=404)
    await driver.destroy_workspace(spec.session_id)  # no raise


@pytest.mark.skipif(shutil.which("helm") is None, reason="helm not installed")
def test_the_chart_keeps_restricted_sandboxes_out_of_the_internet_policy() -> None:
    rendered = subprocess.run(
        [
            "helm",
            "template",
            "t",
            "deploy/helm/platform",
            "-f",
            "deploy/helm/platform/ci/values.yaml",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    policies = {
        doc["metadata"]["name"]: doc
        for doc in yaml.safe_load_all(rendered)
        if doc and doc.get("kind") == "NetworkPolicy"
    }

    selector = policies["sandbox-default-egress"]["spec"]["podSelector"]["matchExpressions"][0]
    assert (selector["key"], selector["operator"], selector["values"]) == (
        egress.EGRESS_LABEL,
        "NotIn",
        ["restricted"],
    )
    ingress = policies["sandbox-network-policy"]["spec"]
    assert ingress["policyTypes"] == ["Ingress"] and ingress["ingress"] == []
    role = next(
        doc
        for doc in yaml.safe_load_all(rendered)
        if doc and doc.get("kind") == "Role" and "sandbox-controller" in doc["metadata"]["name"]
    )
    assert any("networkpolicies" in rule["resources"] for rule in role["rules"])


@pytest.mark.asyncio
async def test_blocking_internal_ranges_alone_allows_the_internet_but_not_private_networks() -> (
    None
):
    spec = _spec()
    spec.block_internal_subnets = True
    driver, core_v1, networking = _driver(spec)

    await driver.start_sandbox(spec.session_id)

    policy = networking.create_namespaced_network_policy.call_args.kwargs["body"]
    block = policy.spec.egress[1].to[0].ip_block
    assert block.cidr == "0.0.0.0/0"
    assert set(block._except) == {
        "169.254.169.254/32",
        "10.0.0.0/8",
        "172.16.0.0/12",
        "192.168.0.0/16",
    }
    pod = core_v1.create_namespaced_pod.call_args.kwargs["body"]
    assert pod.metadata.labels[EGRESS_LABEL] == "restricted"


@pytest.mark.asyncio
async def test_an_explicit_allowlist_wins_over_the_internal_range_block() -> None:
    spec = _spec(["10.50.0.0/16"])  # the org deliberately allows one internal network
    spec.block_internal_subnets = True
    driver, _, networking = _driver(spec)

    await driver.start_sandbox(spec.session_id)

    policy = networking.create_namespaced_network_policy.call_args.kwargs["body"]
    assert [peer.ip_block.cidr for peer in policy.spec.egress[1].to] == ["10.50.0.0/16"]


def test_a_restricted_sandbox_can_still_reach_the_platforms_git_gateway(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from sandbox_driver.egress import PlatformPeer

    monkeypatch.setenv("ROCKET_PLATFORM_NAMESPACE", "rocket")
    monkeypatch.setenv(
        "ROCKET_PLATFORM_POD_LABELS",
        "app.kubernetes.io/name=rocket-chat-backend,app.kubernetes.io/instance=prod",
    )
    peer = PlatformPeer.from_env()
    assert peer is not None

    policy = build_egress_policy(
        name="n", namespace="ns", session_label="s", cidrs=[], labels={}, platform_peer=peer
    )

    rule = policy.spec.egress[1]
    target = rule.to[0]
    assert target.namespace_selector.match_labels == {"kubernetes.io/metadata.name": "rocket"}
    assert target.pod_selector.match_labels == {
        "app.kubernetes.io/name": "rocket-chat-backend",
        "app.kubernetes.io/instance": "prod",
    }
    assert [(p.protocol, p.port) for p in rule.ports] == [("TCP", 8000)]
    assert target.ip_block is None, "the API pods only, never an address range"


def test_without_platform_settings_there_is_no_peer(monkeypatch: pytest.MonkeyPatch) -> None:
    from sandbox_driver.egress import PlatformPeer

    monkeypatch.delenv("ROCKET_PLATFORM_NAMESPACE", raising=False)
    assert PlatformPeer.from_env() is None

"""Placement profiles: node pools and sizing the operator defines, applied to sandbox pods."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from kubernetes_asyncio.client.exceptions import ApiException
from sandbox_driver.exceptions import SandboxError
from sandbox_driver.k8s_driver import K8sSandboxDriver
from sandbox_driver.profiles import PROFILES_ENV, ProfileError, parse_profiles

from specifications.interfaces.sandbox import ResourceLimits, WorkspaceSpec

GPU = {
    "gpu": {
        "description": "GPU pool",
        "nodeSelector": {"pool": "gpu"},
        "tolerations": [{"key": "nvidia.com/gpu", "operator": "Exists", "effect": "NoSchedule"}],
        "runtimeClassName": "gvisor",
        "priorityClassName": "sandbox-high",
        "labels": {"team": "ml", "rocket-chat.io/session": "forged"},
        "annotations": {"karpenter.sh/do-not-disrupt": "true"},
        "storageClassName": "fast-ssd",
        "resources": {"requests": {"cpu": "2"}, "limits": {"cpu": "8", "memory": "32Gi"}},
    }
}


def _spec(profile: str = "") -> WorkspaceSpec:
    return WorkspaceSpec(
        session_id="sess_1",
        tenant_org_id="org_a",
        tenant_user_id="u",
        container_image="python:3.12-slim",
        resources=ResourceLimits(cpu_cores=2.0, memory_limit="4Gi", disk_size="25Gi"),
        profile=profile,
    )


@pytest.fixture
def driver(monkeypatch: pytest.MonkeyPatch) -> K8sSandboxDriver:
    monkeypatch.setenv(PROFILES_ENV, json.dumps(GPU))
    core_v1 = MagicMock()
    core_v1.read_namespaced_persistent_volume_claim = AsyncMock(side_effect=ApiException(404))
    core_v1.create_namespaced_persistent_volume_claim = AsyncMock()
    return K8sSandboxDriver(
        namespace="agent-sandboxes",
        cloud_provider="aws",
        core_v1_api=core_v1,
        ws_core_v1_api=MagicMock(),
    )


def test_a_profile_places_and_sizes_the_pod(driver: K8sSandboxDriver) -> None:
    pod = driver._build_pod(_spec("gpu"), "pod-sess-1", None)

    assert pod.spec.node_selector == {"pool": "gpu"}
    assert [(t.key, t.operator, t.effect) for t in pod.spec.tolerations] == [
        ("nvidia.com/gpu", "Exists", "NoSchedule")
    ]
    assert (pod.spec.runtime_class_name, pod.spec.priority_class_name) == ("gvisor", "sandbox-high")
    resources = pod.spec.containers[0].resources
    assert resources.limits == {"cpu": "8", "memory": "32Gi"}
    assert resources.requests["cpu"] == "2"
    assert pod.metadata.labels["team"] == "ml"
    assert pod.metadata.labels["tenant.org.id"] == "org_a", "the platform's labels win"
    assert pod.metadata.annotations["karpenter.sh/do-not-disrupt"] == "true"


def test_without_a_profile_the_pod_keeps_the_deployments_placement(
    driver: K8sSandboxDriver,
) -> None:
    pod = driver._build_pod(_spec(), "pod-sess-1", None)

    assert pod.spec.node_selector is None and pod.spec.tolerations is None
    assert pod.spec.containers[0].resources.limits == {"cpu": "2.0", "memory": "4Gi"}


async def test_a_profiles_storage_class_holds_the_workspace(driver: K8sSandboxDriver) -> None:
    await driver.ensure_workspace(_spec("gpu"))

    body = driver._core_v1.create_namespaced_persistent_volume_claim.call_args.kwargs["body"]  # type: ignore[union-attr]
    assert body.spec.storage_class_name == "fast-ssd"


def test_an_unknown_profile_is_refused_with_the_way_out(driver: K8sSandboxDriver) -> None:
    with pytest.raises(SandboxError, match="not defined by this deployment"):
        driver._build_pod(_spec("missing"), "pod-sess-1", None)


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("[1]", "object of name"),
        ('{"x": {"tolerations": [{"key": "a", "sneaky": 1}]}}', "unknown toleration fields"),
        ('{"x": {"resources": {"limits": {"gpu": "1"}}}}', "unknown resources"),
        ("{not json", "not valid JSON"),
    ],
)
def test_invalid_profiles_are_refused(text: str, message: str) -> None:
    with pytest.raises(ProfileError, match=message):
        parse_profiles(text)

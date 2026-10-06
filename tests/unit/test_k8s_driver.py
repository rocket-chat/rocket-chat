"""Unit tests for K8sSandboxDriver verifying lifecycle, PVCs, node-affinity pinning, and exec."""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from kubernetes_asyncio.client.exceptions import ApiException
from kubernetes_asyncio.stream import ws_client
from sandbox_driver.k8s_driver import K8sSandboxDriver

from specifications.interfaces.sandbox import (
    ResourceLimits,
    SandboxStatus,
    WorkspaceSpec,
)


@pytest.fixture
def mock_clients():
    core_v1 = MagicMock()
    core_v1.api_client = MagicMock()
    core_v1.api_client.configuration = MagicMock()

    # Async mock methods
    core_v1.read_namespaced_persistent_volume_claim = AsyncMock()
    core_v1.create_namespaced_persistent_volume_claim = AsyncMock()
    core_v1.delete_namespaced_persistent_volume_claim = AsyncMock()
    core_v1.read_namespaced_pod = AsyncMock()
    core_v1.create_namespaced_pod = AsyncMock()
    core_v1.delete_namespaced_pod = AsyncMock()
    core_v1.patch_namespaced_pod = AsyncMock()

    ws_core_v1 = MagicMock()
    ws_core_v1.connect_get_namespaced_pod_exec = AsyncMock()

    return core_v1, ws_core_v1


@pytest.fixture
def spec():
    return WorkspaceSpec(
        session_id="sess_12345",
        tenant_org_id="org_alpha",
        tenant_user_id="user_beta",
        container_image="python:3.12-slim",
        resources=ResourceLimits(cpu_cores=2.0, memory_limit="4Gi", disk_size="25Gi"),
        env_vars={"ROCKET_ENV": "production"},
    )


@pytest.mark.asyncio
async def test_storage_class_resolution_and_pvc_creation(mock_clients, spec):
    core_v1, ws_core_v1 = mock_clients

    # Test AWS provider auto-mapping to gp3
    driver = K8sSandboxDriver(
        namespace="agent-sandboxes",
        cloud_provider="aws",
        core_v1_api=core_v1,
        ws_core_v1_api=ws_core_v1,
    )
    assert driver.storage_class_name == "gp3"

    # PVC does not exist yet (404)
    core_v1.read_namespaced_persistent_volume_claim.side_effect = ApiException(status=404)

    await driver.ensure_workspace(spec)

    core_v1.create_namespaced_persistent_volume_claim.assert_called_once()
    call_kwargs = core_v1.create_namespaced_persistent_volume_claim.call_args.kwargs
    namespace = call_kwargs["namespace"]
    pvc_body = call_kwargs["body"]

    assert namespace == "agent-sandboxes"
    assert pvc_body.metadata.name == "pvc-session-sess-12345"
    assert pvc_body.spec.storage_class_name == "gp3"
    assert pvc_body.spec.resources.requests["storage"] == "25Gi"
    assert pvc_body.metadata.labels["tenant.org.id"] == "org_alpha"


@pytest.mark.asyncio
async def test_cloud_provider_storage_classes():
    # Azure
    d_azure = K8sSandboxDriver(cloud_provider="azure")
    assert d_azure.storage_class_name == "managed-csi"

    # GCP
    d_gcp = K8sSandboxDriver(cloud_provider="gcp")
    assert d_gcp.storage_class_name == "pd-balanced"

    # Explicit custom storage class override
    d_custom = K8sSandboxDriver(storage_class_name="rook-ceph-block", cloud_provider="aws")
    assert d_custom.storage_class_name == "rook-ceph-block"


@pytest.mark.asyncio
async def test_pod_node_affinity_pinning_lifecycle(mock_clients, spec):
    core_v1, ws_core_v1 = mock_clients
    driver = K8sSandboxDriver(
        namespace="agent-sandboxes",
        cloud_provider="aws",
        core_v1_api=core_v1,
        ws_core_v1_api=ws_core_v1,
    )

    # PVC exists
    core_v1.read_namespaced_persistent_volume_claim.side_effect = None
    core_v1.read_namespaced_persistent_volume_claim.return_value = MagicMock()

    # Step 1: Initial launch (no pod exists yet -> 404, then created and transitions to Running)
    mock_running_pod = MagicMock()
    mock_running_pod.status.phase = "Running"
    mock_running_pod.spec.node_name = "k8s-worker-node-42"
    mock_running_pod.metadata.deletion_timestamp = None

    core_v1.read_namespaced_pod.side_effect = [
        ApiException(status=404),  # check existing
        mock_running_pod,  # wait for running
    ]

    await driver.start_sandbox(spec.session_id)

    # Assert initial pod was created without node pinning
    first_pod_call = core_v1.create_namespaced_pod.call_args[1]["body"]
    assert first_pod_call.spec.affinity is None

    # Verify scheduled node was recorded in cache
    assert driver._node_affinity_cache[spec.session_id] == "k8s-worker-node-42"

    # Step 2: Hibernate pod (scale compute to 0)
    await driver.hibernate_sandbox(spec.session_id)
    core_v1.delete_namespaced_pod.assert_called_once_with(
        name="pod-session-sess-12345",
        namespace="agent-sandboxes",
        grace_period_seconds=5,
    )

    # Status check after hibernation: pod 404, PVC exists -> HIBERNATED
    core_v1.read_namespaced_pod.side_effect = ApiException(status=404)
    status = await driver.get_status(spec.session_id)
    assert status == SandboxStatus.HIBERNATED

    # Step 3: Resume pod from hibernation -> Must have soft node affinity configured!
    core_v1.create_namespaced_pod.reset_mock()
    core_v1.read_namespaced_pod.side_effect = [
        ApiException(status=404),  # check existing
        mock_running_pod,  # wait for running
    ]

    await driver.start_sandbox(spec.session_id)

    second_pod_call = core_v1.create_namespaced_pod.call_args[1]["body"]
    affinity = second_pod_call.spec.affinity
    assert affinity is not None
    terms = affinity.node_affinity.preferred_during_scheduling_ignored_during_execution
    assert len(terms) == 1
    assert terms[0].weight == 100
    expr = terms[0].preference.match_expressions[0]
    assert expr.key == "kubernetes.io/hostname"
    assert expr.values == ["k8s-worker-node-42"]


@pytest.mark.asyncio
async def test_exec_command_streaming_and_exit_code(mock_clients, spec):
    core_v1, ws_core_v1 = mock_clients
    driver = K8sSandboxDriver(
        namespace="agent-sandboxes",
        core_v1_api=core_v1,
        ws_core_v1_api=ws_core_v1,
    )

    # Pod is running
    mock_pod = MagicMock()
    mock_pod.status.phase = "Running"
    core_v1.read_namespaced_pod.return_value = mock_pod

    # Prepare mock WebSocket message stream
    class MockWSMessage:
        def __init__(self, data: bytes):
            self.data = data

    class MockWSConnection:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

        async def __aiter__(self):
            messages = [
                # Channel 1: STDOUT
                MockWSMessage(bytes([ws_client.STDOUT_CHANNEL]) + b"Deploying satellite... "),
                MockWSMessage(bytes([ws_client.STDOUT_CHANNEL]) + b"Success!\n"),
                # Channel 3: ERROR status (success)
                MockWSMessage(
                    bytes([ws_client.ERROR_CHANNEL])
                    + json.dumps({"status": "Success"}).encode("utf-8")
                ),
            ]
            for msg in messages:
                yield msg

    ws_core_v1.connect_get_namespaced_pod_exec.return_value = MockWSConnection()

    received_chunks = []

    def on_chunk(chunk: str):
        received_chunks.append(chunk)

    result = await driver.exec_command(
        session_id=spec.session_id,
        command="echo test",
        on_stdout_chunk=on_chunk,
    )

    assert result.exit_code == 0
    assert result.stdout == "Deploying satellite... Success!\n"
    assert received_chunks == ["Deploying satellite... ", "Success!\n"]
    assert result.duration_ms >= 0


@pytest.mark.asyncio
async def test_destroy_workspace_purges_pod_and_pvc(mock_clients, spec):
    core_v1, ws_core_v1 = mock_clients
    driver = K8sSandboxDriver(
        namespace="agent-sandboxes",
        core_v1_api=core_v1,
        ws_core_v1_api=ws_core_v1,
    )

    await driver.destroy_workspace(spec.session_id)

    core_v1.delete_namespaced_pod.assert_called_once_with(
        name="pod-session-sess-12345",
        namespace="agent-sandboxes",
        grace_period_seconds=0,
    )
    core_v1.delete_namespaced_persistent_volume_claim.assert_called_once_with(
        name="pvc-session-sess-12345",
        namespace="agent-sandboxes",
    )

    status = await driver.get_status(spec.session_id)
    assert status == SandboxStatus.DESTROYED


@pytest.mark.asyncio
async def test_custom_sandbox_image_and_pull_secrets(mock_clients):
    core_v1, ws_core_v1 = mock_clients
    driver = K8sSandboxDriver(
        namespace="agent-sandboxes",
        core_v1_api=core_v1,
        ws_core_v1_api=ws_core_v1,
    )
    driver.image_pull_secrets = ["ghcr-secret", "company-regcred"]

    custom_spec = WorkspaceSpec(
        session_id="custom_img_session",
        tenant_org_id="org_custom",
        tenant_user_id="user_custom",
        container_image="ghcr.io/acme/rust-compiler:1.80-alpine",
        resources=ResourceLimits(cpu_cores=4.0, memory_limit="8Gi"),
    )

    core_v1.read_namespaced_persistent_volume_claim.return_value = MagicMock()
    mock_running_pod = MagicMock()
    mock_running_pod.status.phase = "Running"
    mock_running_pod.spec.node_name = "node-1"

    core_v1.read_namespaced_pod.side_effect = [
        ApiException(status=404),
        mock_running_pod,
    ]

    await driver.ensure_workspace(custom_spec)
    await driver.start_sandbox(custom_spec.session_id)

    create_call = core_v1.create_namespaced_pod.call_args[1]["body"]
    assert create_call.spec.containers[0].image == "ghcr.io/acme/rust-compiler:1.80-alpine"
    pull_secret_names = [s.name for s in create_call.spec.image_pull_secrets]
    assert "ghcr-secret" in pull_secret_names
    assert "company-regcred" in pull_secret_names


@pytest.mark.asyncio
async def test_warmed_pods_pool_reconciliation_and_node_selection(mock_clients):
    core_v1, ws_core_v1 = mock_clients
    driver = K8sSandboxDriver(
        namespace="agent-sandboxes",
        core_v1_api=core_v1,
        ws_core_v1_api=ws_core_v1,
    )
    driver.warmed_pool_size = 2
    driver.warmed_image = "python:3.12-slim"

    # Simulating that no warm pods exist yet (404)
    core_v1.read_namespaced_pod.side_effect = ApiException(status=404)

    active_pods = await driver.reconcile_warm_pool()
    assert len(active_pods) == 2
    assert active_pods == ["pod-warm-0", "pod-warm-1"]
    assert core_v1.create_namespaced_pod.call_count == 2

    first_warm_pod = core_v1.create_namespaced_pod.call_args_list[0][1]["body"]
    assert first_warm_pod.metadata.name == "pod-warm-0"
    assert first_warm_pod.metadata.labels["rocket.sandbox.pool"] == "warm"

    # Cleanup warm pool
    await driver.cleanup_warm_pool()
    assert core_v1.delete_namespaced_pod.call_count >= 2


@pytest.mark.asyncio
async def test_sandbox_pausing_after_inactivity(mock_clients, spec):
    core_v1, ws_core_v1 = mock_clients
    driver = K8sSandboxDriver(
        namespace="agent-sandboxes",
        core_v1_api=core_v1,
        ws_core_v1_api=ws_core_v1,
    )
    driver.inactivity_pause_seconds = 300.0  # 5 minutes

    # Mark active 400 seconds ago
    import time

    driver._last_activity[spec.session_id] = time.time() - 400.0

    # Pod is currently Running
    mock_running_pod = MagicMock()
    mock_running_pod.status.phase = "Running"
    mock_running_pod.metadata.deletion_timestamp = None
    core_v1.read_namespaced_pod.return_value = mock_running_pod

    paused = await driver.pause_inactive_sandboxes()
    assert spec.session_id in paused

    core_v1.delete_namespaced_pod.assert_called_once_with(
        name="pod-session-sess-12345",
        namespace="agent-sandboxes",
        grace_period_seconds=5,
    )


@pytest.mark.asyncio
async def test_sandbox_cleanup_after_extended_inactivity(mock_clients, spec):
    core_v1, ws_core_v1 = mock_clients
    driver = K8sSandboxDriver(
        namespace="agent-sandboxes",
        core_v1_api=core_v1,
        ws_core_v1_api=ws_core_v1,
    )
    driver.inactivity_cleanup_seconds = 3600.0  # 1 hour

    # Mark active 4000 seconds ago
    import time

    driver._last_activity[spec.session_id] = time.time() - 4000.0

    # Pod is deleted (404), PVC still exists -> HIBERNATED state
    core_v1.read_namespaced_pod.side_effect = ApiException(status=404)
    core_v1.read_namespaced_persistent_volume_claim.return_value = MagicMock()

    cleaned = await driver.cleanup_inactive_sandboxes()
    assert spec.session_id in cleaned

    # Storage volume must be purged
    core_v1.delete_namespaced_persistent_volume_claim.assert_called_once_with(
        name="pvc-session-sess-12345",
        namespace="agent-sandboxes",
    )
    assert spec.session_id not in driver._last_activity


@pytest.mark.asyncio
async def test_pod_eviction_annotations_default_safe_to_evict(mock_clients, spec):
    core_v1, ws_core_v1 = mock_clients
    driver = K8sSandboxDriver(
        namespace="agent-sandboxes",
        core_v1_api=core_v1,
        ws_core_v1_api=ws_core_v1,
    )

    core_v1.read_namespaced_persistent_volume_claim.return_value = MagicMock()
    mock_running_pod = MagicMock()
    mock_running_pod.status.phase = "Running"
    mock_running_pod.spec.node_name = "node-1"

    core_v1.read_namespaced_pod.side_effect = [
        ApiException(status=404),
        mock_running_pod,
    ]

    await driver.start_sandbox(spec.session_id)

    create_call = core_v1.create_namespaced_pod.call_args[1]["body"]
    annotations = create_call.metadata.annotations
    assert annotations["cluster-autoscaler.kubernetes.io/safe-to-evict"] == "true"
    assert annotations["karpenter.sh/do-not-disrupt"] == "false"


@pytest.mark.asyncio
async def test_exec_command_toggles_eviction_protection(mock_clients, spec):
    core_v1, ws_core_v1 = mock_clients
    driver = K8sSandboxDriver(
        namespace="agent-sandboxes",
        core_v1_api=core_v1,
        ws_core_v1_api=ws_core_v1,
    )

    mock_pod = MagicMock()
    mock_pod.status.phase = "Running"
    core_v1.read_namespaced_pod.return_value = mock_pod

    class MockWSMessage:
        def __init__(self, data: bytes):
            self.data = data

    class MockWSConnection:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

        async def __aiter__(self):
            yield MockWSMessage(
                bytes([ws_client.ERROR_CHANNEL]) + json.dumps({"status": "Success"}).encode("utf-8")
            )

    ws_core_v1.connect_get_namespaced_pod_exec.return_value = MockWSConnection()

    await driver.exec_command(session_id=spec.session_id, command="cargo test")

    # Verify patch_namespaced_pod was called twice:
    # 1. Before exec: safe-to-evict = "false", do-not-disrupt = "true"
    # 2. After exec: safe-to-evict = "true", do-not-disrupt = "false"
    assert core_v1.patch_namespaced_pod.call_count == 2

    first_patch = core_v1.patch_namespaced_pod.call_args_list[0][1]["body"]["metadata"][
        "annotations"
    ]
    assert first_patch["cluster-autoscaler.kubernetes.io/safe-to-evict"] == "false"
    assert first_patch["karpenter.sh/do-not-disrupt"] == "true"

    second_patch = core_v1.patch_namespaced_pod.call_args_list[1][1]["body"]["metadata"][
        "annotations"
    ]
    assert second_patch["cluster-autoscaler.kubernetes.io/safe-to-evict"] == "true"
    assert second_patch["karpenter.sh/do-not-disrupt"] == "false"


@pytest.mark.asyncio
async def test_evicted_pod_recovery_as_hibernated(mock_clients, spec):
    core_v1, ws_core_v1 = mock_clients
    driver = K8sSandboxDriver(
        namespace="agent-sandboxes",
        core_v1_api=core_v1,
        ws_core_v1_api=ws_core_v1,
    )

    # Simulate pod evicted by cluster autoscaler/node drain
    mock_evicted_pod = MagicMock()
    mock_evicted_pod.status.phase = "Failed"
    mock_evicted_pod.status.reason = "Evicted"
    mock_evicted_pod.metadata.deletion_timestamp = None
    core_v1.read_namespaced_pod.return_value = mock_evicted_pod
    core_v1.read_namespaced_persistent_volume_claim.return_value = MagicMock()

    # Assert get_status treats evicted pod as HIBERNATED so user can resume
    status = await driver.get_status(spec.session_id)
    assert status == SandboxStatus.HIBERNATED

    # When start_sandbox is called, the dead evicted pod must be purged and replaced
    mock_running_pod = MagicMock()
    mock_running_pod.status.phase = "Running"
    mock_running_pod.status.reason = None
    mock_running_pod.metadata.deletion_timestamp = None
    mock_running_pod.spec.node_name = "node-new"

    core_v1.read_namespaced_pod.side_effect = [
        mock_evicted_pod,  # Initial check detects evicted
        mock_running_pod,  # Wait loop detects running
    ]

    await driver.start_sandbox(spec.session_id)

    # Verified dead pod was explicitly purged with delete_namespaced_pod
    core_v1.delete_namespaced_pod.assert_called_with(
        name="pod-session-sess-12345",
        namespace="agent-sandboxes",
        grace_period_seconds=0,
    )
    # Verified new pod was created
    core_v1.create_namespaced_pod.assert_called_once()

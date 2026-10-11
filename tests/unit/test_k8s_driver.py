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


@pytest.mark.asyncio
async def test_pod_security_hardening(mock_clients, spec):
    core_v1, ws_core_v1 = mock_clients
    driver = K8sSandboxDriver(
        namespace="agent-sandboxes",
        core_v1_api=core_v1,
        ws_core_v1_api=ws_core_v1,
    )

    core_v1.read_namespaced_persistent_volume_claim.return_value = MagicMock()
    mock_running_pod = MagicMock()
    mock_running_pod.status.phase = "Running"
    mock_running_pod.spec.node_name = "k8s-node-1"
    core_v1.read_namespaced_pod.side_effect = [
        ApiException(status=404),
        mock_running_pod,
    ]

    await driver.start_sandbox(spec.session_id)

    pod_manifest = core_v1.create_namespaced_pod.call_args[1]["body"]
    assert pod_manifest.spec.automount_service_account_token is False
    # PID 1 of the container is `tail`, which never reaps children; the pause container must.
    assert pod_manifest.spec.share_process_namespace is True

    container = pod_manifest.spec.containers[0]
    assert container.security_context is not None
    assert container.security_context.allow_privilege_escalation is False
    assert "NET_RAW" in container.security_context.capabilities.drop
    assert "SYS_ADMIN" in container.security_context.capabilities.drop
    assert container.security_context.seccomp_profile.type == "RuntimeDefault"


async def _started_pod(core_v1, ws_core_v1, spec, **env):
    driver = K8sSandboxDriver(
        namespace="agent-sandboxes", core_v1_api=core_v1, ws_core_v1_api=ws_core_v1
    )
    core_v1.read_namespaced_persistent_volume_claim.return_value = MagicMock()
    running = MagicMock()
    running.status.phase = "Running"
    running.spec.node_name = "node-1"
    core_v1.read_namespaced_pod.side_effect = [ApiException(status=404), running]
    await driver.ensure_workspace(spec)
    await driver.start_sandbox(spec.session_id)
    return core_v1.create_namespaced_pod.call_args[1]["body"]


@pytest.mark.asyncio
async def test_non_root_sandbox_drops_all_capabilities_and_sets_fs_group(mock_clients, spec):
    spec.require_non_root = True
    pod = await _started_pod(*mock_clients, spec)

    assert pod.spec.security_context.run_as_non_root is True
    assert pod.spec.security_context.run_as_user == 1000
    assert pod.spec.security_context.fs_group == 1000
    assert pod.spec.containers[0].security_context.capabilities.drop == ["ALL"]


@pytest.mark.asyncio
async def test_default_sandbox_does_not_force_a_user_but_keeps_the_volume_writable(
    mock_clients, spec
):
    pod = await _started_pod(*mock_clients, spec)

    # The devbox image runs as uid 1000 by itself; its volume must be group-writable for it.
    assert pod.spec.security_context.fs_group == 1000
    assert pod.spec.security_context.run_as_non_root is None
    assert pod.spec.security_context.run_as_user is None
    assert pod.spec.runtime_class_name is None
    assert "SYS_ADMIN" in pod.spec.containers[0].security_context.capabilities.drop


@pytest.mark.asyncio
async def test_runtime_class_comes_from_the_environment(mock_clients, spec, monkeypatch):
    monkeypatch.setenv("K8S_SANDBOX_RUNTIME_CLASS", "gvisor")
    pod = await _started_pod(*mock_clients, spec)

    assert pod.spec.runtime_class_name == "gvisor"


@pytest.mark.asyncio
async def test_workspace_purge_is_off_by_default(mock_clients, spec):
    import time

    core_v1, ws_core_v1 = mock_clients
    driver = K8sSandboxDriver(
        namespace="agent-sandboxes", core_v1_api=core_v1, ws_core_v1_api=ws_core_v1
    )
    driver._last_activity[spec.session_id] = time.time() - 10 * 86400
    core_v1.read_namespaced_pod.side_effect = ApiException(status=404)

    assert await driver.cleanup_inactive_sandboxes() == []
    core_v1.delete_namespaced_persistent_volume_claim.assert_not_called()


@pytest.mark.asyncio
async def test_purge_never_touches_the_workspace_of_a_live_session(mock_clients, spec):
    import time

    core_v1, ws_core_v1 = mock_clients
    driver = K8sSandboxDriver(
        namespace="agent-sandboxes", core_v1_api=core_v1, ws_core_v1_api=ws_core_v1
    )
    driver._last_activity[spec.session_id] = time.time() - 4000.0
    core_v1.read_namespaced_pod.side_effect = ApiException(status=404)
    core_v1.read_namespaced_persistent_volume_claim.return_value = MagicMock()

    async def live(_session_id: str) -> bool:
        return True

    assert await driver.cleanup_inactive_sandboxes(threshold_seconds=3600, is_live=live) == []
    core_v1.delete_namespaced_persistent_volume_claim.assert_not_called()

    async def gone(_session_id: str) -> bool:
        return False

    assert await driver.cleanup_inactive_sandboxes(threshold_seconds=3600, is_live=gone) == [
        spec.session_id
    ]


@pytest.mark.asyncio
async def test_pods_that_outlive_a_restart_are_adopted_for_idle_tracking(mock_clients):
    core_v1, ws_core_v1 = mock_clients
    driver = K8sSandboxDriver(
        namespace="agent-sandboxes", core_v1_api=core_v1, ws_core_v1_api=ws_core_v1
    )
    pod = MagicMock()
    pod.metadata.labels = {"agent.session.id": "sess-old"}
    core_v1.list_namespaced_pod = AsyncMock(return_value=MagicMock(items=[pod]))
    core_v1.list_namespaced_persistent_volume_claim = AsyncMock(return_value=MagicMock(items=[]))

    await driver.adopt_existing_sandboxes()

    assert "sess-old" in driver._last_activity
    selector = core_v1.list_namespaced_pod.call_args.kwargs["label_selector"]
    assert selector == "app.kubernetes.io/managed-by=rocket-chat"


@pytest.mark.asyncio
async def test_hibernated_workspaces_without_a_pod_are_adopted_for_reaping(mock_clients):
    core_v1, ws_core_v1 = mock_clients
    driver = K8sSandboxDriver(
        namespace="agent-sandboxes", core_v1_api=core_v1, ws_core_v1_api=ws_core_v1
    )
    claim = MagicMock()
    claim.metadata.labels = {"agent.session.id": "sess-hibernated"}
    core_v1.list_namespaced_pod = AsyncMock(return_value=MagicMock(items=[]))
    core_v1.list_namespaced_persistent_volume_claim = AsyncMock(
        return_value=MagicMock(items=[claim])
    )

    await driver.adopt_existing_sandboxes()

    assert "sess-hibernated" in driver._last_activity


def test_exec_output_routes_each_channel_and_reads_the_exit_status() -> None:
    from sandbox_driver.k8s_driver import _ExecOutput

    seen: list[str] = []
    output = _ExecOutput(seen.append)

    output.feed(bytes([ws_client.STDOUT_CHANNEL]) + b"out ")
    output.feed(chr(ws_client.STDOUT_CHANNEL) + "more")
    output.feed(bytes([ws_client.STDERR_CHANNEL]) + b"warn")
    output.feed(None)
    output.feed(b"")
    output.feed(bytes([ws_client.ERROR_CHANNEL]) + b'{"status":"Success"}')

    assert output.stdout.text() == "out more" and seen == ["out ", "more"]
    assert output.stderr.text() == "warn"
    assert output.exit_code == 0


def test_exec_output_reports_a_failing_exit_code_and_survives_garbage() -> None:
    from sandbox_driver.k8s_driver import _ExecOutput

    output = _ExecOutput(None)
    failure = (
        b'{"status":"Failure","reason":"NonZeroExitCode",'
        b'"details":{"causes":[{"reason":"ExitCode","message":"3"}]}}'
    )

    output.feed(bytes([ws_client.ERROR_CHANNEL]) + failure)
    assert output.exit_code == 3

    output.feed(bytes([ws_client.ERROR_CHANNEL]) + b"not json")
    assert output.exit_code == 3


def _pod_waiting(reason: str, message: str = "") -> MagicMock:
    pod = MagicMock()
    pod.status.phase = "Pending"
    pod.status.reason = None
    pod.metadata.deletion_timestamp = None
    container = MagicMock()
    container.image = "ghcr.io/acme/sandbox:latest"
    container.state.waiting.reason = reason
    container.state.waiting.message = message
    pod.status.container_statuses = [container]
    return pod


@pytest.mark.asyncio
async def test_an_image_that_cannot_be_pulled_fails_fast_and_names_the_image(mock_clients, spec):
    from sandbox_driver.exceptions import SandboxStartError

    core_v1, ws_core_v1 = mock_clients
    driver = K8sSandboxDriver(core_v1_api=core_v1, ws_core_v1_api=ws_core_v1)
    core_v1.read_namespaced_persistent_volume_claim.return_value = MagicMock()
    core_v1.read_namespaced_pod.side_effect = [
        ApiException(status=404),
        _pod_waiting("ImagePullBackOff", "failed to authorize: 403 Forbidden"),
    ]

    with pytest.raises(SandboxStartError) as failure:
        await driver.start_sandbox(spec.session_id)

    message = str(failure.value)
    assert "ImagePullBackOff" in message and "ghcr.io/acme/sandbox:latest" in message
    assert "403 Forbidden" in message and "Settings > Organization > Sandboxes" in message


@pytest.mark.asyncio
async def test_a_pod_that_failed_to_start_is_removed_so_a_retry_gets_a_fresh_one(
    mock_clients, spec
):
    from sandbox_driver.exceptions import SandboxStartError

    core_v1, ws_core_v1 = mock_clients
    driver = K8sSandboxDriver(core_v1_api=core_v1, ws_core_v1_api=ws_core_v1)
    core_v1.read_namespaced_persistent_volume_claim.return_value = MagicMock()
    core_v1.read_namespaced_pod.side_effect = [
        ApiException(status=404),
        _pod_waiting("InvalidImageName"),
    ]

    with pytest.raises(SandboxStartError):
        await driver.start_sandbox(spec.session_id)

    core_v1.delete_namespaced_pod.assert_awaited_once()
    assert core_v1.delete_namespaced_pod.call_args.kwargs["name"] == driver._pod_name(
        spec.session_id
    )


@pytest.mark.asyncio
async def test_a_first_pull_error_is_given_time_to_clear(mock_clients, spec):
    core_v1, ws_core_v1 = mock_clients
    driver = K8sSandboxDriver(core_v1_api=core_v1, ws_core_v1_api=ws_core_v1)
    core_v1.read_namespaced_persistent_volume_claim.return_value = MagicMock()
    running = MagicMock()
    running.status.phase = "Running"
    running.status.container_statuses = []
    running.spec.node_name = "node-1"
    running.metadata.deletion_timestamp = None
    core_v1.read_namespaced_pod.side_effect = [
        ApiException(status=404),
        _pod_waiting("ErrImagePull", "temporary registry error"),
        running,
    ]

    await driver.start_sandbox(spec.session_id)

    core_v1.delete_namespaced_pod.assert_not_awaited()

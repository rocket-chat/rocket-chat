"""Integration test for K8sSandboxDriver verifying Pod lifecycle, volume persistence, and node pinning."""

import time
import uuid

import pytest
from sandbox_driver.k8s_driver import K8sSandboxDriver

from specifications.interfaces.sandbox import (
    ResourceLimits,
    SandboxStatus,
    WorkspaceSpec,
)
from tests.harness.k8s_harness import FakeK8sHarness


@pytest.mark.asyncio
async def test_k8s_sandbox_lifecycle_and_volume_persistence() -> None:
    session_id = f"test_k8s_{uuid.uuid4().hex[:8]}"
    harness = FakeK8sHarness()
    core_v1, ws_core_v1 = harness.create_mock_apis()

    driver = K8sSandboxDriver(
        namespace="agent-sandboxes",
        cloud_provider="aws",  # Asserts gp3 storage class
        core_v1_api=core_v1,
        ws_core_v1_api=ws_core_v1,
    )

    spec = WorkspaceSpec(
        session_id=session_id,
        tenant_org_id="org_satellite",
        tenant_user_id="user_mission",
        container_image="python:3.12-slim",
        resources=ResourceLimits(cpu_cores=2.0, memory_limit="4Gi", disk_size="20Gi"),
    )

    try:
        # 1. Ensure workspace creates PersistentVolumeClaim with gp3 storage class
        await driver.ensure_workspace(spec)
        assert driver.storage_class_name == "gp3"

        pvc_name = driver._pvc_name(session_id)
        assert pvc_name in harness.pvcs
        pvc = harness.pvcs[pvc_name]
        assert pvc.status.phase == "Bound"
        assert pvc.spec.storage_class_name == "gp3"

        status = await driver.get_status(session_id)
        assert status == SandboxStatus.HIBERNATED

        # 2. Start sandbox Pod (initial schedule)
        await driver.start_sandbox(session_id)
        status = await driver.get_status(session_id)
        assert status == SandboxStatus.RUNNING

        pod_name = driver._pod_name(session_id)
        assert pod_name in harness.pods
        initial_node = harness.pods[pod_name].spec.node_name
        assert initial_node == harness.default_node

        # Node affinity cache must have recorded the scheduled node
        assert driver._node_affinity_cache[session_id] == initial_node

        # 3. Exec Command Streaming: execute uname -a and capture stdout chunks
        streamed_chunks: list[str] = []

        def on_chunk(chunk: str) -> None:
            streamed_chunks.append(chunk)

        uname_result = await driver.exec_command(
            session_id=session_id,
            command="uname -a",
            on_stdout_chunk=on_chunk,
        )
        assert uname_result.exit_code == 0
        assert len(uname_result.stdout) > 0
        assert len(streamed_chunks) > 0
        assert "".join(streamed_chunks) == uname_result.stdout

        # 4. Write data to volume inside the Pod
        test_payload = "TELEMETRY_SAMPLE_RATE=100Hz\nSTATUS=NOMINAL\n"
        await driver.write_file(session_id, "k8s_data.txt", test_payload)

        read_back = await driver.read_file(session_id, "k8s_data.txt")
        assert read_back == test_payload

        # 5. Pod Scale-to-Zero Hibernation: Delete pod compute while retaining PVC
        await driver.hibernate_sandbox(session_id)
        assert pod_name not in harness.pods  # Ephemeral pod is gone
        assert pvc_name in harness.pvcs  # PVC is preserved
        assert harness.pvcs[pvc_name].status.phase == "Bound"

        status = await driver.get_status(session_id)
        assert status == SandboxStatus.HIBERNATED

        # 6. Resume Pod: Assert Node-Affinity Pinning and sub-second resume latency (< 3s)
        resume_start = time.monotonic()
        await driver.start_sandbox(session_id)
        resume_duration = time.monotonic() - resume_start

        # Resume latency criteria: < 3 seconds
        assert resume_duration < 3.0, f"Resume latency {resume_duration:.2f}s exceeded 3s threshold"

        # Assert newly created pod was scheduled with soft node affinity preferred to initial_node
        resumed_pod = harness.pods[pod_name]
        affinity = resumed_pod.spec.affinity
        assert affinity is not None
        terms = affinity.node_affinity.preferred_during_scheduling_ignored_during_execution
        assert terms[0].preference.match_expressions[0].values == [initial_node]
        assert resumed_pod.spec.node_name == initial_node

        status = await driver.get_status(session_id)
        assert status == SandboxStatus.RUNNING

        # 7. Assert data integrity from persistent volume across pod deletion
        persisted_content = await driver.read_file(session_id, "k8s_data.txt")
        assert persisted_content == test_payload

        # 8. Directory tree listing and patch test
        patch = (
            "--- k8s_data.txt\n"
            "+++ k8s_data.txt\n"
            "@@ -1,2 +1,2 @@\n"
            "-TELEMETRY_SAMPLE_RATE=100Hz\n"
            "+TELEMETRY_SAMPLE_RATE=200Hz\n"
            " STATUS=NOMINAL\n"
        )
        applied = await driver.apply_patch(session_id, "k8s_data.txt", patch)
        assert applied is True

        patched_content = await driver.read_file(session_id, "k8s_data.txt")
        assert "TELEMETRY_SAMPLE_RATE=200Hz\n" in patched_content

        file_list = await driver.list_files(session_id, "/workspace")
        file_paths = [f.path for f in file_list]
        assert any("k8s_data.txt" in p for p in file_paths)

    finally:
        # 9. Clean up workspace: purge Pod and PVC
        await driver.destroy_workspace(session_id)
        assert pod_name not in harness.pods
        assert pvc_name not in harness.pvcs
        status = await driver.get_status(session_id)
        assert status == SandboxStatus.DESTROYED
        harness.cleanup()

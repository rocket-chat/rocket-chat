"""In-memory and simulated filesystem test harness for Kubernetes Sandbox Driver."""

import asyncio
import base64
import json
import os
import re
import shutil
import sys
import tempfile
import uuid
from typing import Any
from unittest.mock import MagicMock

from kubernetes_asyncio.client import (
    V1PersistentVolumeClaim,
    V1PersistentVolumeClaimStatus,
    V1Pod,
    V1PodStatus,
)
from kubernetes_asyncio.client.exceptions import ApiException
from kubernetes_asyncio.stream import ws_client


class FakeWSMessage:
    def __init__(self, data: bytes):
        self.data = data


class FakeWSConnection:
    def __init__(self, messages: list[FakeWSMessage]):
        self.messages = messages

    async def __aenter__(self) -> "FakeWSConnection":
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        pass

    async def __aiter__(self):
        for msg in self.messages:
            yield msg


class FakeK8sHarness:
    """Simulates Kubernetes CoreV1Api, PVC lifecycle, Pod lifecycle, node pinning, and exec."""

    def __init__(self, base_dir: str | None = None) -> None:
        self.storage_root = base_dir or tempfile.mkdtemp(prefix="rocket_k8s_vol_")
        self.pvcs: dict[str, V1PersistentVolumeClaim] = {}
        self.pods: dict[str, V1Pod] = {}
        self.default_node = "k8s-node-worker-01"

    def cleanup(self) -> None:
        if os.path.exists(self.storage_root):
            shutil.rmtree(self.storage_root, ignore_errors=True)

    def _get_volume_dir(self, pvc_name: str) -> str:
        vol_dir = os.path.join(self.storage_root, pvc_name)
        os.makedirs(vol_dir, exist_ok=True)
        return vol_dir

    async def read_namespaced_persistent_volume_claim(
        self,
        name: str,
        namespace: str,
    ) -> V1PersistentVolumeClaim:
        if name not in self.pvcs:
            raise ApiException(status=404, reason=f"PVC {name} not found")
        return self.pvcs[name]

    async def create_namespaced_persistent_volume_claim(
        self,
        namespace: str,
        body: V1PersistentVolumeClaim,
    ) -> V1PersistentVolumeClaim:
        name = body.metadata.name if body.metadata else f"pvc-{uuid.uuid4().hex[:6]}"
        if name in self.pvcs:
            raise ApiException(status=409, reason=f"PVC {name} already exists")

        # Initialize physical volume folder
        self._get_volume_dir(name)

        # Set PVC to Bound status
        pvc = body
        pvc.status = V1PersistentVolumeClaimStatus(phase="Bound")
        self.pvcs[name] = pvc
        return pvc

    async def delete_namespaced_persistent_volume_claim(
        self,
        name: str,
        namespace: str,
    ) -> None:
        if name not in self.pvcs:
            raise ApiException(status=404, reason=f"PVC {name} not found")
        del self.pvcs[name]
        vol_dir = os.path.join(self.storage_root, name)
        await asyncio.to_thread(shutil.rmtree, vol_dir, ignore_errors=True)

    async def read_namespaced_pod(
        self,
        name: str,
        namespace: str,
    ) -> V1Pod:
        if name not in self.pods:
            raise ApiException(status=404, reason=f"Pod {name} not found")
        return self.pods[name]

    async def create_namespaced_pod(
        self,
        namespace: str,
        body: V1Pod,
    ) -> V1Pod:
        name = body.metadata.name if body.metadata else f"pod-{uuid.uuid4().hex[:6]}"
        if name in self.pods:
            raise ApiException(status=409, reason=f"Pod {name} already exists")

        pod = body
        # Apply node selection / preferred affinity or assign default worker node
        assigned_node = self.default_node
        if (
            pod.spec
            and pod.spec.affinity
            and pod.spec.affinity.node_affinity
            and pod.spec.affinity.node_affinity.preferred_during_scheduling_ignored_during_execution
        ):
            terms = (
                pod.spec.affinity.node_affinity.preferred_during_scheduling_ignored_during_execution
            )
            for term in terms:
                for expr in term.preference.match_expressions:
                    if expr.key == "kubernetes.io/hostname" and expr.values:
                        assigned_node = expr.values[0]
                        break
        elif (
            pod.spec
            and pod.spec.node_selector
            and "kubernetes.io/hostname" in pod.spec.node_selector
        ):
            assigned_node = pod.spec.node_selector["kubernetes.io/hostname"]

        if pod.spec:
            pod.spec.node_name = assigned_node
        pod.status = V1PodStatus(phase="Running")
        self.pods[name] = pod
        return pod

    async def delete_namespaced_pod(
        self,
        name: str,
        namespace: str,
        grace_period_seconds: int = 0,
    ) -> None:
        if name not in self.pods:
            raise ApiException(status=404, reason=f"Pod {name} not found")
        del self.pods[name]

    async def patch_namespaced_pod(
        self,
        name: str,
        namespace: str,
        body: dict[str, Any],
    ) -> V1Pod:
        if name not in self.pods:
            raise ApiException(status=404, reason=f"Pod {name} not found")
        pod = self.pods[name]
        if "metadata" in body and "annotations" in body["metadata"]:
            if pod.metadata is None:
                from kubernetes_asyncio.client import V1ObjectMeta

                pod.metadata = V1ObjectMeta()
            if pod.metadata.annotations is None:
                pod.metadata.annotations = {}
            pod.metadata.annotations.update(body["metadata"]["annotations"])
        return pod

    async def connect_get_namespaced_pod_exec(
        self,
        name: str,
        namespace: str,
        command: list[str],
        stderr: bool = True,
        stdin: bool = False,
        stdout: bool = True,
        tty: bool = False,
        _preload_content: bool = False,
    ) -> FakeWSConnection:
        if name not in self.pods:
            raise ApiException(status=404, reason=f"Pod {name} not found")
        pod = self.pods[name]
        if not pod.status or pod.status.phase != "Running":
            raise ApiException(status=400, reason=f"Pod {name} is not in Running phase")

        # Determine PVC volume backing this pod
        pvc_name = ""
        if pod.spec and pod.spec.volumes:
            for vol in pod.spec.volumes:
                if vol.persistent_volume_claim:
                    pvc_name = vol.persistent_volume_claim.claim_name
                    break
        volume_dir = self._get_volume_dir(pvc_name) if pvc_name else self.storage_root

        # Map /workspace references to physical simulated volume directory
        raw_cmd = command[2] if len(command) >= 3 else " ".join(command)

        def _replace_b64(match: re.Match[str]) -> str:
            b64_str = match.group(1)
            try:
                decoded = base64.b64decode(b64_str).decode("utf-8")
                if "/workspace" in decoded:
                    decoded = decoded.replace("/workspace", volume_dir)
                    return f"base64.b64decode('{base64.b64encode(decoded.encode('utf-8')).decode('ascii')}')"
            except Exception:
                pass
            return match.group(0)

        adapted_cmd = re.sub(r"base64\.b64decode\('([A-Za-z0-9+/=]+)'\)", _replace_b64, raw_cmd)
        adapted_cmd = adapted_cmd.replace("/workspace", volume_dir)

        env = dict(os.environ)
        bin_dir = os.path.dirname(sys.executable)
        env["PATH"] = f"{bin_dir}:{env.get('PATH', '')}"

        proc = await asyncio.create_subprocess_shell(
            adapted_cmd,
            cwd=volume_dir,
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        proc_stdout, proc_stderr = await proc.communicate()
        exit_code = proc.returncode or 0

        messages: list[FakeWSMessage] = []
        if proc_stdout:
            # Deliver stdout in chunks to simulate streaming
            chunk_size = 64
            for i in range(0, len(proc_stdout), chunk_size):
                chunk = proc_stdout[i : i + chunk_size]
                messages.append(FakeWSMessage(bytes([ws_client.STDOUT_CHANNEL]) + chunk))

        if proc_stderr:
            messages.append(FakeWSMessage(bytes([ws_client.STDERR_CHANNEL]) + proc_stderr))

        if exit_code == 0:
            status_json = json.dumps({"status": "Success"}).encode("utf-8")
        else:
            status_json = json.dumps(
                {
                    "status": "Failure",
                    "details": {"causes": [{"message": str(exit_code)}]},
                }
            ).encode("utf-8")

        messages.append(FakeWSMessage(bytes([ws_client.ERROR_CHANNEL]) + status_json))
        return FakeWSConnection(messages)

    def create_mock_apis(self) -> tuple[Any, Any]:
        """Wrap harness methods into MagicMock CoreV1Api instances."""
        core_v1 = MagicMock()
        core_v1.api_client = MagicMock()
        core_v1.api_client.configuration = MagicMock()

        core_v1.read_namespaced_persistent_volume_claim = (
            self.read_namespaced_persistent_volume_claim
        )
        core_v1.create_namespaced_persistent_volume_claim = (
            self.create_namespaced_persistent_volume_claim
        )
        core_v1.delete_namespaced_persistent_volume_claim = (
            self.delete_namespaced_persistent_volume_claim
        )
        core_v1.read_namespaced_pod = self.read_namespaced_pod
        core_v1.create_namespaced_pod = self.create_namespaced_pod
        core_v1.delete_namespaced_pod = self.delete_namespaced_pod
        core_v1.patch_namespaced_pod = self.patch_namespaced_pod

        ws_core_v1 = MagicMock()
        ws_core_v1.connect_get_namespaced_pod_exec = self.connect_get_namespaced_pod_exec

        return core_v1, ws_core_v1

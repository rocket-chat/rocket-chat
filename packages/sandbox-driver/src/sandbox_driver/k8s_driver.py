"""Kubernetes Sandbox Driver implementing SandboxDriverProtocol for production clusters."""

import asyncio
import base64
import json
import logging
import os
import re
import shlex
import time
from collections.abc import AsyncIterator, Callable
from typing import Any

from kubernetes_asyncio import client, config
from kubernetes_asyncio.client.exceptions import ApiException
from kubernetes_asyncio.stream import WsApiClient, ws_client

from sandbox_driver.exceptions import (
    SandboxError,
    SandboxExecutionError,
    SandboxFilesystemError,
    SandboxNotFoundError,
    SandboxTimeoutError,
)
from sandbox_driver.patcher import apply_unified_diff
from sandbox_driver.utils import (
    build_list_files_command,
    normalize_workspace_path,
)
from specifications.interfaces.sandbox import (
    ExecResult,
    FileStat,
    SandboxDriverProtocol,
    SandboxStatus,
    WorkspaceSpec,
)

logger = logging.getLogger(__name__)

CLOUD_STORAGE_CLASSES: dict[str, str] = {
    "aws": "gp3",
    "eks": "gp3",
    "azure": "managed-csi",
    "aks": "managed-csi",
    "gcp": "pd-balanced",
    "gke": "pd-balanced",
}


def _sanitize_k8s_name(name: str) -> str:
    """Sanitize name to comply with RFC 1123 DNS subdomain format (lowercase, dash, max 63 chars)."""
    cleaned = re.sub(r"[^a-z0-9-]", "-", name.lower()).strip("-")
    if not cleaned:
        cleaned = "session"
    return cleaned[:50]


def _sanitize_label_value(val: str) -> str:
    """Sanitize label value to adhere to Kubernetes label syntax."""
    cleaned = re.sub(r"[^a-zA-Z0-9_.-]", "-", val).strip("-._")
    if not cleaned:
        cleaned = "default"
    return cleaned[:63]


CLUSTER_AUTOSCALER_SAFE_TO_EVICT = "cluster-autoscaler.kubernetes.io/safe-to-evict"
KARPENTER_DO_NOT_DISRUPT = "karpenter.sh/do-not-disrupt"


class K8sSandboxDriver(SandboxDriverProtocol):
    """Manages ephemeral Kubernetes Pods bound to PersistentVolumeClaims."""

    def __init__(
        self,
        namespace: str | None = None,
        storage_class_name: str | None = None,
        cloud_provider: str | None = None,
        default_image: str = "python:3.12-slim",
        grace_period_seconds: int = 5,
        core_v1_api: client.CoreV1Api | None = None,
        ws_core_v1_api: client.CoreV1Api | None = None,
    ) -> None:
        self.namespace: str = namespace or os.getenv("K8S_SANDBOX_NAMESPACE") or "agent-sandboxes"
        self.default_image: str = os.getenv("K8S_DEFAULT_IMAGE") or default_image
        self.grace_period_seconds: int = grace_period_seconds

        # Determine storage class based on explicit override or cloud provider mapping
        raw_sc = storage_class_name or os.getenv("K8S_STORAGE_CLASS") or ""
        resolved_sc = raw_sc.strip() or None
        if not resolved_sc:
            raw_provider = (
                cloud_provider
                or os.getenv("K8S_CLOUD_PROVIDER")
                or os.getenv("CLOUD_PROVIDER")
                or ""
            )
            provider = raw_provider.lower()
            resolved_sc = CLOUD_STORAGE_CLASSES.get(provider)
        self.storage_class_name: str | None = resolved_sc

        self._core_v1: client.CoreV1Api | None = core_v1_api
        self._ws_core_v1: client.CoreV1Api | None = ws_core_v1_api

        # Inactivity lifecycle settings (configured via env or parameters)
        pause_env = os.getenv("SANDBOX_INACTIVITY_PAUSE_SECONDS")
        self.inactivity_pause_seconds: float = (
            float(pause_env) if pause_env is not None else 600.0  # 10m default
        )
        cleanup_env = os.getenv("SANDBOX_INACTIVITY_CLEANUP_SECONDS")
        self.inactivity_cleanup_seconds: float = (
            float(cleanup_env) if cleanup_env is not None else 86400.0  # 24h default
        )

        # Warmed standby pod pool settings
        warmed_env = os.getenv("K8S_WARMED_POOL_SIZE")
        self.warmed_pool_size: int = (
            int(warmed_env) if warmed_env is not None else 0  # Disabled by default
        )
        self.warmed_image: str = os.getenv("K8S_WARMED_IMAGE") or self.default_image

        # Custom registry image pull secrets
        secrets_env = os.getenv("K8S_IMAGE_PULL_SECRETS")
        self.image_pull_secrets: list[str] = (
            [s.strip() for s in secrets_env.split(",") if s.strip()] if secrets_env else []
        )

        self._specs: dict[str, WorkspaceSpec] = {}
        self._node_affinity_cache: dict[str, str] = {}
        self._destroyed_sessions: set[str] = set()
        self._last_activity: dict[str, float] = {}
        self._init_lock = asyncio.Lock()

    def record_activity(self, session_id: str) -> None:
        """Record interaction timestamp to calculate idle duration for pause and cleanup."""
        self._last_activity[session_id] = time.time()

    def get_last_activity(self, session_id: str) -> float | None:
        """Return the last recorded timestamp for a session."""
        return self._last_activity.get(session_id)

    async def _ensure_clients(self) -> tuple[client.CoreV1Api, client.CoreV1Api]:
        """Lazily initialize Kubernetes API and WebSocket clients within an active event loop."""
        if self._core_v1 is not None and self._ws_core_v1 is not None:
            return self._core_v1, self._ws_core_v1

        async with self._init_lock:
            if self._core_v1 is None:
                try:
                    await config.load_incluster_config()  # type: ignore[no-untyped-call]
                except Exception:
                    try:
                        await config.load_kube_config()
                    except Exception as err:
                        logger.warning("Failed loading Kubernetes config: %s", err)
                self._core_v1 = client.CoreV1Api()

            if self._ws_core_v1 is None:
                api_client = getattr(self._core_v1, "api_client", None)
                conf = api_client.configuration if api_client is not None else None
                ws_api_client = WsApiClient(configuration=conf)
                self._ws_core_v1 = client.CoreV1Api(api_client=ws_api_client)

        return self._core_v1, self._ws_core_v1

    def _pvc_name(self, session_id: str) -> str:
        return f"pvc-session-{_sanitize_k8s_name(session_id)}"

    def _pod_name(self, session_id: str) -> str:
        return f"pod-session-{_sanitize_k8s_name(session_id)}"

    def _build_labels(self, spec: WorkspaceSpec) -> dict[str, str]:
        return {
            "agent.session.id": _sanitize_label_value(spec.session_id),
            "tenant.org.id": _sanitize_label_value(spec.tenant_org_id),
            "tenant.user.id": _sanitize_label_value(spec.tenant_user_id),
            "app.kubernetes.io/managed-by": "rocket-chat",
        }

    async def ensure_workspace(self, spec: WorkspaceSpec) -> None:
        """Provision PersistentVolumeClaim for the session if it does not already exist."""
        self._specs[spec.session_id] = spec
        self._destroyed_sessions.discard(spec.session_id)
        core_v1, _ = await self._ensure_clients()
        pvc_name = self._pvc_name(spec.session_id)

        try:
            await core_v1.read_namespaced_persistent_volume_claim(
                name=pvc_name,
                namespace=self.namespace,
            )
            return
        except ApiException as err:
            if err.status != 404:
                raise SandboxError(f"Error checking PVC {pvc_name}: {err}") from err

        storage_size = spec.resources.disk_size or "20Gi"
        pvc_manifest = client.V1PersistentVolumeClaim(
            metadata=client.V1ObjectMeta(
                name=pvc_name,
                namespace=self.namespace,
                labels=self._build_labels(spec),
            ),
            spec=client.V1PersistentVolumeClaimSpec(
                access_modes=["ReadWriteOnce"],
                resources=client.V1VolumeResourceRequirements(requests={"storage": storage_size}),
                storage_class_name=self.storage_class_name,
            ),
        )

        try:
            await core_v1.create_namespaced_persistent_volume_claim(
                namespace=self.namespace,
                body=pvc_manifest,
            )
        except ApiException as err:
            if err.status != 409:
                raise SandboxError(f"Failed creating PVC {pvc_name}: {err}") from err

    async def _wait_for_pod_running(
        self,
        core_v1: client.CoreV1Api,
        pod_name: str,
        session_id: str,
        timeout_seconds: float = 60.0,
    ) -> client.V1Pod:
        """Poll until pod transitions into Running state, recording scheduled nodeName."""
        start_time = time.monotonic()
        while time.monotonic() - start_time < timeout_seconds:
            try:
                pod = await core_v1.read_namespaced_pod(
                    name=pod_name,
                    namespace=self.namespace,
                )
                phase = pod.status.phase if pod.status else None

                if phase == "Running":
                    if pod.spec and pod.spec.node_name:
                        self._node_affinity_cache[session_id] = pod.spec.node_name
                    return pod

                if phase in ("Failed", "Unknown"):
                    reason = pod.status.reason if pod.status else "Unknown"
                    raise SandboxExecutionError(
                        f"Pod {pod_name} entered failed phase '{phase}' ({reason})"
                    )

            except ApiException as err:
                if err.status != 404:
                    raise SandboxError(f"Failed querying pod {pod_name}: {err}") from err

            await asyncio.sleep(0.1)

        raise SandboxTimeoutError(f"Timed out waiting for Pod {pod_name} to enter Running state")

    async def start_sandbox(self, session_id: str) -> None:
        """Spin up ephemeral pod mounting the session PVC with node-affinity pinning."""
        spec = self._specs.get(session_id)
        if spec is None:
            spec = WorkspaceSpec(
                session_id=session_id,
                tenant_org_id="default",
                tenant_user_id="default",
                container_image=self.default_image,
            )
            self._specs[session_id] = spec

        await self.ensure_workspace(spec)
        core_v1, _ = await self._ensure_clients()

        pod_name = self._pod_name(session_id)
        pvc_name = self._pvc_name(session_id)

        try:
            existing_pod = await core_v1.read_namespaced_pod(
                name=pod_name,
                namespace=self.namespace,
            )
            phase = existing_pod.status.phase if existing_pod.status else None
            reason = existing_pod.status.reason if existing_pod.status else None
            is_evicted = reason == "Evicted" or phase in ("Failed", "Unknown")
            is_terminating = (
                existing_pod.metadata is not None
                and existing_pod.metadata.deletion_timestamp is not None
            )

            if phase == "Running" and not is_evicted and not is_terminating:
                if existing_pod.spec and existing_pod.spec.node_name:
                    self._node_affinity_cache[session_id] = existing_pod.spec.node_name
                return

            # Force purge dead, evicted, or failed pod before recreating
            await core_v1.delete_namespaced_pod(
                name=pod_name,
                namespace=self.namespace,
                grace_period_seconds=0,
            )
            await asyncio.sleep(0.2)
        except ApiException as err:
            if err.status != 404:
                raise SandboxError(f"Error checking existing Pod {pod_name}: {err}") from err

        limits: dict[str, str] = {}
        requests: dict[str, str] = {}
        limits["cpu"] = os.getenv(
            "SANDBOX_CPU_LIMIT",
            str(spec.resources.cpu_cores) if spec.resources.cpu_cores else "500m",
        )
        requests["cpu"] = os.getenv("SANDBOX_CPU_REQUEST", "50m")
        limits["memory"] = os.getenv("SANDBOX_MEMORY_LIMIT", spec.resources.memory_limit or "512Mi")
        requests["memory"] = os.getenv("SANDBOX_MEMORY_REQUEST", "128Mi")

        env_vars = [client.V1EnvVar(name=k, value=str(v)) for k, v in spec.env_vars.items()]

        # Respect custom container image from WorkspaceSpec or fall back to default
        target_image = spec.container_image or self.default_image

        image_pull_refs = [
            client.V1LocalObjectReference(name=s) for s in self.image_pull_secrets
        ] or None

        affinity: client.V1Affinity | None = None
        preferred_node: str | None = None
        if session_id in self._node_affinity_cache:
            preferred_node = self._node_affinity_cache[session_id]
        elif self.warmed_pool_size > 0:
            # When warm pods are enabled, prefer worker nodes that already have warm pods running
            for i in range(self.warmed_pool_size):
                try:
                    wp = await core_v1.read_namespaced_pod(
                        name=self._warm_pod_name(i),
                        namespace=self.namespace,
                    )
                    if wp.status and wp.status.phase == "Running" and wp.spec and wp.spec.node_name:
                        preferred_node = wp.spec.node_name
                        break
                except Exception:
                    pass

        if preferred_node:
            # Soft node affinity (preferredDuringSchedulingIgnoredDuringExecution):
            # Prioritizes the target node (score +100) to bypass cloud EBS detachment latency (~90s).
            # If the node is cordoned, drained, or deleted, kube-scheduler natively and immediately
            # schedules the pod onto another available node without blocking or timing out.
            affinity = client.V1Affinity(
                node_affinity=client.V1NodeAffinity(
                    preferred_during_scheduling_ignored_during_execution=[
                        client.V1PreferredSchedulingTerm(
                            weight=100,
                            preference=client.V1NodeSelectorTerm(
                                match_expressions=[
                                    client.V1NodeSelectorRequirement(
                                        key="kubernetes.io/hostname",
                                        operator="In",
                                        values=[preferred_node],
                                    )
                                ]
                            ),
                        )
                    ]
                )
            )

        # Unmanaged pods are annotated safe-to-evict by default so Cluster Autoscaler
        # and Karpenter can downscale nodes or drain nodes during upgrades when idle.
        annotations = {
            CLUSTER_AUTOSCALER_SAFE_TO_EVICT: "true",
            KARPENTER_DO_NOT_DISRUPT: "false",
        }

        pod_manifest = client.V1Pod(
            metadata=client.V1ObjectMeta(
                name=pod_name,
                namespace=self.namespace,
                labels=self._build_labels(spec),
                annotations=annotations,
            ),
            spec=client.V1PodSpec(
                affinity=affinity,
                restart_policy="Never",
                termination_grace_period_seconds=self.grace_period_seconds,
                image_pull_secrets=image_pull_refs,
                containers=[
                    client.V1Container(
                        name="sandbox",
                        image=target_image,
                        command=["tail", "-f", "/dev/null"],
                        working_dir="/workspace",
                        env=env_vars,
                        resources=client.V1ResourceRequirements(limits=limits, requests=requests),
                        volume_mounts=[
                            client.V1VolumeMount(
                                name="workspace-storage",
                                mount_path="/workspace",
                            )
                        ],
                    )
                ],
                volumes=[
                    client.V1Volume(
                        name="workspace-storage",
                        persistent_volume_claim=client.V1PersistentVolumeClaimVolumeSource(
                            claim_name=pvc_name,
                        ),
                    )
                ],
            ),
        )

        try:
            await core_v1.create_namespaced_pod(
                namespace=self.namespace,
                body=pod_manifest,
            )
        except ApiException as err:
            if err.status != 409:
                raise SandboxError(f"Failed creating Pod {pod_name}: {err}") from err

        await self._wait_for_pod_running(core_v1, pod_name, session_id, timeout_seconds=45.0)

        self.record_activity(session_id)

    async def hibernate_sandbox(self, session_id: str) -> None:
        """Scale compute to zero by deleting the Pod while retaining the PVC volume."""
        core_v1, _ = await self._ensure_clients()
        pod_name = self._pod_name(session_id)

        try:
            await core_v1.delete_namespaced_pod(
                name=pod_name,
                namespace=self.namespace,
                grace_period_seconds=self.grace_period_seconds,
            )
        except ApiException as err:
            if err.status != 404:
                raise SandboxError(f"Failed hibernating Pod {pod_name}: {err}") from err

    async def destroy_workspace(self, session_id: str) -> None:
        """Purge both the ephemeral compute Pod and the persistent storage PVC."""
        core_v1, _ = await self._ensure_clients()
        pod_name = self._pod_name(session_id)
        pvc_name = self._pvc_name(session_id)

        try:
            await core_v1.delete_namespaced_pod(
                name=pod_name,
                namespace=self.namespace,
                grace_period_seconds=0,
            )
        except ApiException as err:
            if err.status != 404:
                logger.debug("Pod deletion error during destroy: %s", err)

        try:
            await core_v1.delete_namespaced_persistent_volume_claim(
                name=pvc_name,
                namespace=self.namespace,
            )
        except ApiException as err:
            if err.status != 404:
                logger.debug("PVC deletion error during destroy: %s", err)

        self._destroyed_sessions.add(session_id)
        self._specs.pop(session_id, None)
        self._node_affinity_cache.pop(session_id, None)

    async def get_status(self, session_id: str) -> SandboxStatus:
        """Resolve current lifecycle status from Kubernetes Pod and PVC states."""
        if session_id in self._destroyed_sessions:
            return SandboxStatus.DESTROYED

        core_v1, _ = await self._ensure_clients()
        pod_name = self._pod_name(session_id)
        pvc_name = self._pvc_name(session_id)

        try:
            pod = await core_v1.read_namespaced_pod(name=pod_name, namespace=self.namespace)
            if pod.metadata and pod.metadata.deletion_timestamp is not None:
                return SandboxStatus.HIBERNATED

            phase = pod.status.phase if pod.status else None
            reason = pod.status.reason if pod.status else None
            if reason == "Evicted" or (phase == "Failed" and reason == "Evicted"):
                # If evicted during node drain/downscale, PVC is intact -> treat as hibernated
                return SandboxStatus.HIBERNATED

            if phase == "Running":
                return SandboxStatus.RUNNING
            if phase == "Pending":
                return SandboxStatus.STARTING
            if phase in ("Failed", "Unknown"):
                return SandboxStatus.ERROR
        except ApiException as err:
            if err.status != 404:
                return SandboxStatus.ERROR

        try:
            await core_v1.read_namespaced_persistent_volume_claim(
                name=pvc_name,
                namespace=self.namespace,
            )
            return SandboxStatus.HIBERNATED
        except ApiException as err:
            if err.status == 404:
                return SandboxStatus.NON_EXISTENT
            return SandboxStatus.ERROR

    async def set_eviction_protection(self, session_id: str, protected: bool) -> None:
        """Dynamically toggle eviction protection for Cluster Autoscaler and Karpenter.

        When protected=True (during active command/tool execution):
          - cluster-autoscaler.kubernetes.io/safe-to-evict: "false"
          - karpenter.sh/do-not-disrupt: "true"
        When protected=False (idle, awaiting user prompt):
          - cluster-autoscaler.kubernetes.io/safe-to-evict: "true"
          - karpenter.sh/do-not-disrupt: "false"

        This ensures unmanaged (naked) sandbox pods do not block nodepool autoscaling,
        drains, or node upgrades when idle, while protecting active computations from disruption.
        """
        core_v1, _ = await self._ensure_clients()
        pod_name = self._pod_name(session_id)
        patch = {
            "metadata": {
                "annotations": {
                    CLUSTER_AUTOSCALER_SAFE_TO_EVICT: "false" if protected else "true",
                    KARPENTER_DO_NOT_DISRUPT: "true" if protected else "false",
                }
            }
        }
        try:
            await core_v1.patch_namespaced_pod(
                name=pod_name,
                namespace=self.namespace,
                body=patch,
            )
        except ApiException as err:
            # 404 is normal if the pod was hibernated, evicted, or deleted
            if err.status != 404:
                logger.warning("Failed updating eviction protection for pod %s: %s", pod_name, err)

    async def exec_command(
        self,
        session_id: str,
        command: str,
        workdir: str = "/workspace",
        timeout_seconds: int = 120,
        env: dict[str, str] | None = None,
        on_stdout_chunk: Callable[[str], None] | None = None,
    ) -> ExecResult:
        """Execute command inside running pod using Kubernetes Exec WebSocket API."""
        core_v1, ws_core_v1 = await self._ensure_clients()
        pod_name = self._pod_name(session_id)

        try:
            pod = await core_v1.read_namespaced_pod(name=pod_name, namespace=self.namespace)
            if not pod.status or pod.status.phase != "Running":
                raise SandboxNotFoundError(f"Active sandbox pod {pod_name} is not running.")
        except ApiException as err:
            if err.status == 404:
                raise SandboxNotFoundError(f"Sandbox pod {pod_name} not found.") from err
            raise SandboxError(f"Failed verifying pod {pod_name}: {err}") from err

        env_prefix = ""
        if env:
            env_prefix = " ".join(f"{k}={shlex.quote(str(v))}" for k, v in env.items()) + " "
        wrapped_command = f"cd {shlex.quote(workdir)} && {env_prefix}{command}"
        exec_cmd = ["/bin/sh", "-c", wrapped_command]

        start_time = time.monotonic()
        stdout_chunks: list[str] = []
        stderr_chunks: list[str] = []
        exit_code = 0

        async def _run_stream() -> int:
            nonlocal exit_code
            ws_conn: Any = await ws_core_v1.connect_get_namespaced_pod_exec(
                name=pod_name,
                namespace=self.namespace,
                command=exec_cmd,  # type: ignore[arg-type]
                stderr=True,
                stdin=False,
                stdout=True,
                tty=False,
                _preload_content=False,
            )

            async with ws_conn as ws:
                async for wsmsg in ws:
                    raw = wsmsg.data
                    if not raw:
                        continue
                    if isinstance(raw, bytes):
                        channel = raw[0]
                        payload = raw[1:].decode("utf-8", errors="replace")
                    elif isinstance(raw, str):
                        channel = ord(raw[0])
                        payload = raw[1:]
                    else:
                        continue

                    if channel == ws_client.STDOUT_CHANNEL:
                        stdout_chunks.append(payload)
                        if on_stdout_chunk:
                            on_stdout_chunk(payload)
                    elif channel == ws_client.STDERR_CHANNEL:
                        stderr_chunks.append(payload)
                    elif channel == ws_client.ERROR_CHANNEL:
                        try:
                            exit_code = WsApiClient.parse_error_data(payload)
                        except Exception:
                            pass
            return exit_code

        await self.set_eviction_protection(session_id, protected=True)
        try:
            exit_code = await asyncio.wait_for(_run_stream(), timeout=timeout_seconds)
        except TimeoutError as err:
            raise SandboxTimeoutError(
                f"Command timed out after {timeout_seconds} seconds: {command}"
            ) from err
        except SandboxError:
            raise
        except Exception as err:
            raise SandboxExecutionError(f"Exec stream failure in pod {pod_name}: {err}") from err
        finally:
            await self.set_eviction_protection(session_id, protected=False)

        self.record_activity(session_id)
        duration_ms = int((time.monotonic() - start_time) * 1000)
        return ExecResult(
            exit_code=exit_code,
            stdout="".join(stdout_chunks),
            stderr="".join(stderr_chunks),
            duration_ms=duration_ms,
        )

    async def read_file(
        self,
        session_id: str,
        path: str,
        start_line: int | None = None,
        end_line: int | None = None,
    ) -> str:
        """Read text file contents from the pod filesystem."""
        target_path = normalize_workspace_path(path)
        read_cmd = (
            f"python3 -c 'import sys, os, base64; "
            f'p = "{target_path}"; '
            f"sys.exit(2) if not os.path.exists(p) else "
            f'sys.stdout.write(base64.b64encode(open(p, "rb").read()).decode("ascii"))\''
        )
        res = await self.exec_command(session_id, read_cmd)
        if res.exit_code == 2:
            raise FileNotFoundError(f"File not found in sandbox: {path}")
        if res.exit_code != 0:
            raise SandboxFilesystemError(f"Failed reading {path}: {res.stderr}")

        content = base64.b64decode(res.stdout).decode("utf-8", errors="replace")
        if start_line is not None or end_line is not None:
            lines = content.splitlines(keepends=True)
            start_idx = max(0, start_line - 1) if (start_line is not None and start_line > 0) else 0
            end_idx = end_line if end_line is not None else len(lines)
            return "".join(lines[start_idx:end_idx])
        return content

    async def write_file(
        self,
        session_id: str,
        path: str,
        content: str,
        overwrite: bool = True,
    ) -> None:
        """Write content to file within the pod workspace."""
        target_path = normalize_workspace_path(path)
        if not overwrite:
            check_res = await self.exec_command(session_id, f"test -e {shlex.quote(target_path)}")
            if check_res.exit_code == 0:
                raise FileExistsError(f"Path already exists and overwrite=False: {path}")

        parent_dir = os.path.dirname(target_path)
        b64_content = base64.b64encode(content.encode("utf-8")).decode("ascii")
        write_cmd = (
            f"mkdir -p {shlex.quote(parent_dir)} && "
            f'python3 -c \'import base64; open("{target_path}", "wb").write(base64.b64decode("{b64_content}"))\''
        )
        res = await self.exec_command(session_id, write_cmd)
        if res.exit_code != 0:
            raise SandboxFilesystemError(f"Failed writing {path}: {res.stderr}")

    async def list_files(
        self,
        session_id: str,
        directory: str = "/workspace",
        max_depth: int = 3,
    ) -> list[FileStat]:
        """List files and directories recursively up to max_depth."""
        target_dir = normalize_workspace_path(directory)
        cmd = build_list_files_command(target_dir, max_depth)
        res = await self.exec_command(session_id, cmd)
        if res.exit_code != 0:
            raise SandboxExecutionError(f"Failed to list directory {directory}: {res.stderr}")

        try:
            raw_entries = json.loads(res.stdout)
            return [
                FileStat(
                    path=item["path"],
                    is_dir=bool(item["is_dir"]),
                    size_bytes=int(item["size_bytes"]),
                    modified_at=int(item["modified_at"]),
                )
                for item in raw_entries
            ]
        except Exception as err:
            raise SandboxFilesystemError(f"Failed parsing file listing: {err}") from err

    async def apply_patch(
        self,
        session_id: str,
        path: str,
        patch_content: str,
    ) -> bool:
        """Apply unified diff patch directly to a file in the workspace."""
        target_path = normalize_workspace_path(path)
        try:
            current_content = await self.read_file(session_id, target_path)
        except (FileNotFoundError, SandboxError):
            return False

        clean, patched_content = apply_unified_diff(current_content, patch_content)
        if not clean:
            return False

        await self.write_file(session_id, target_path, patched_content, overwrite=True)
        return True

    async def attach_pty(
        self,
        session_id: str,
        cols: int = 80,
        rows: int = 24,
    ) -> AsyncIterator[bytes]:
        """Attach a bidirectional pseudo-terminal stream to the running pod."""
        pod_name = self._pod_name(session_id)
        status = await self.get_status(session_id)
        if status != SandboxStatus.RUNNING:
            raise SandboxNotFoundError(
                f"Pod {pod_name} is in status {status}, not {SandboxStatus.RUNNING}."
            )

        # Build interactive exec command stream
        try:
            from kubernetes.stream import stream as k8s_stream  # type: ignore[import-not-found]

            if not self._core_v1:
                raise SandboxNotFoundError("Kubernetes CoreV1 client is uninitialized.")

            resp = k8s_stream(
                self._core_v1.connect_get_namespaced_pod_exec,
                pod_name,
                self.namespace,
                command=["/bin/sh"],
                stderr=True,
                stdin=True,
                stdout=True,
                tty=True,
                _preload_content=False,
            )

            while resp.is_open():
                chunk = await asyncio.to_thread(resp.read_stdout, timeout=1)
                if chunk:
                    yield chunk.encode("utf-8") if isinstance(chunk, str) else chunk
                await asyncio.sleep(0.01)
        except Exception as pty_err:
            logger.debug("K8s PTY stream terminated: %s", pty_err)

    def _warm_pod_name(self, index: int) -> str:
        return f"pod-warm-{index}"

    async def reconcile_warm_pool(self) -> list[str]:
        """
        Maintain pool of pre-warmed running standby pods with target container image.

        Pre-warming pods on worker nodes eliminates container image pull delays
        (30-90s) on initial session launch.
        """
        if self.warmed_pool_size <= 0:
            return []

        core_v1, _ = await self._ensure_clients()
        active_warm_pods: list[str] = []

        for i in range(self.warmed_pool_size):
            pod_name = self._warm_pod_name(i)
            try:
                pod = await core_v1.read_namespaced_pod(
                    name=pod_name,
                    namespace=self.namespace,
                )
                phase = pod.status.phase if pod.status else None
                if phase == "Running":
                    active_warm_pods.append(pod_name)
                    continue

                # Delete dead/failed warm pod to replace it
                await core_v1.delete_namespaced_pod(
                    name=pod_name,
                    namespace=self.namespace,
                    grace_period_seconds=0,
                )
                await asyncio.sleep(0.1)
            except ApiException as err:
                if err.status != 404:
                    logger.warning("Error checking warm pod %s: %s", pod_name, err)
                    continue

            image_pull_refs = [
                client.V1LocalObjectReference(name=s) for s in self.image_pull_secrets
            ] or None

            warm_manifest = client.V1Pod(
                metadata=client.V1ObjectMeta(
                    name=pod_name,
                    namespace=self.namespace,
                    labels={
                        "rocket.sandbox.pool": "warm",
                        "app.kubernetes.io/managed-by": "rocket-chat",
                    },
                    annotations={
                        CLUSTER_AUTOSCALER_SAFE_TO_EVICT: "true",
                        KARPENTER_DO_NOT_DISRUPT: "false",
                    },
                ),
                spec=client.V1PodSpec(
                    restart_policy="Always",
                    termination_grace_period_seconds=10,
                    image_pull_secrets=image_pull_refs,
                    containers=[
                        client.V1Container(
                            name="warm-sandbox",
                            image=self.warmed_image,
                            command=["tail", "-f", "/dev/null"],
                            resources=client.V1ResourceRequirements(
                                requests={"cpu": "100m", "memory": "128Mi"},
                                limits={"cpu": "500m", "memory": "512Mi"},
                            ),
                        )
                    ],
                ),
            )

            try:
                await core_v1.create_namespaced_pod(
                    namespace=self.namespace,
                    body=warm_manifest,
                )
                active_warm_pods.append(pod_name)
            except ApiException as err:
                if err.status == 409:
                    active_warm_pods.append(pod_name)
                else:
                    logger.warning("Failed creating warm pod %s: %s", pod_name, err)

        return active_warm_pods

    async def cleanup_warm_pool(self) -> None:
        """Purge all standby pre-warmed pods."""
        core_v1, _ = await self._ensure_clients()
        for i in range(max(10, self.warmed_pool_size)):
            pod_name = self._warm_pod_name(i)
            try:
                await core_v1.delete_namespaced_pod(
                    name=pod_name,
                    namespace=self.namespace,
                    grace_period_seconds=0,
                )
            except ApiException as err:
                if err.status != 404:
                    logger.debug("Failed deleting warm pod %s: %s", pod_name, err)

    async def pause_inactive_sandboxes(self, threshold_seconds: float | None = None) -> list[str]:
        """Hibernate running sandboxes that exceeded configured idle duration."""
        threshold = (
            threshold_seconds if threshold_seconds is not None else self.inactivity_pause_seconds
        )
        now = time.time()
        paused: list[str] = []

        for session_id, last_time in list(self._last_activity.items()):
            if now - last_time >= threshold:
                status = await self.get_status(session_id)
                if status == SandboxStatus.RUNNING:
                    logger.info(
                        "Pausing inactive sandbox %s (idle for %.1fs, threshold %.1fs)",
                        session_id,
                        now - last_time,
                        threshold,
                    )
                    await self.hibernate_sandbox(session_id)
                    paused.append(session_id)
        return paused

    async def cleanup_inactive_sandboxes(self, threshold_seconds: float | None = None) -> list[str]:
        """Purge and destroy hibernated sandboxes that exceeded maximum retention inactivity."""
        threshold = (
            threshold_seconds if threshold_seconds is not None else self.inactivity_cleanup_seconds
        )
        now = time.time()
        cleaned: list[str] = []

        for session_id, last_time in list(self._last_activity.items()):
            if now - last_time >= threshold:
                status = await self.get_status(session_id)
                if status in (SandboxStatus.HIBERNATED, SandboxStatus.ERROR):
                    logger.info(
                        "Purging inactive sandbox workspace %s (idle for %.1fs, threshold %.1fs)",
                        session_id,
                        now - last_time,
                        threshold,
                    )
                    await self.destroy_workspace(session_id)
                    cleaned.append(session_id)
                    self._last_activity.pop(session_id, None)
        return cleaned

    async def reconcile_inactivity(self) -> tuple[list[str], list[str]]:
        """Run single sweep of inactivity checks: pause idle compute, then purge stale storage."""
        paused = await self.pause_inactive_sandboxes()
        cleaned = await self.cleanup_inactive_sandboxes()
        return paused, cleaned

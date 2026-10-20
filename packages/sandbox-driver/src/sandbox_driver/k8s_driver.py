"""Kubernetes Sandbox Driver implementing SandboxDriverProtocol for production clusters."""

import asyncio
import base64
import contextlib
import inspect
import logging
import os
import re
import shlex
import time
import uuid
from collections.abc import Callable, Mapping
from typing import Any

from kubernetes_asyncio import client, config
from kubernetes_asyncio.client.exceptions import ApiException
from kubernetes_asyncio.stream import WsApiClient, ws_client

from sandbox_driver.egress import (
    EGRESS_LABEL,
    EGRESS_RESTRICTED,
    PlatformPeer,
    build_egress_policy,
    is_restricted,
    resolve_destinations,
)
from sandbox_driver.exceptions import (
    SandboxError,
    SandboxExecutionError,
    SandboxFilesystemError,
    SandboxNotFoundError,
    SandboxStartError,
    SandboxTimeoutError,
)
from sandbox_driver.patcher import apply_unified_diff
from sandbox_driver.profiles import PROFILES_ENV, PlacementProfile, configured_profiles
from sandbox_driver.pty import SHELL_LAUNCHER, K8sPty
from sandbox_driver.session_env import DEFAULT_SANDBOX_CACHE_ENV, SessionEnvMixin
from sandbox_driver.tracing import traced_exec
from sandbox_driver.utils import (
    CLIENT_DEADLINE_MARGIN_SECONDS,
    SANDBOX_UID,
    BoundedOutput,
    build_list_files_command,
    build_read_file_command,
    build_write_file_commands,
    hit_deadline,
    normalize_workspace_path,
    parse_list_files_output,
    sandbox_dropped_capabilities,
    wrap_with_deadline,
)
from specifications.interfaces.sandbox import (
    ExecResult,
    FileStat,
    PtyChannel,
    SandboxDriverProtocol,
    SandboxStatus,
    SessionLivenessCheck,
    WorkspaceSpec,
)

logger = logging.getLogger(__name__)

# The exec stdin channel (first byte of a frame), and the prelude that turns the base64
# ``KEY=VALUE`` lines sent on it into the command's environment (see ``_shell_command``).
_STDIN_FRAME = b"\x00"
_ENV_END = "__ROCKET_ENV_END__"
_ENV_PRELUDE = (
    'while IFS= read -r __rocket_kv && [ "$__rocket_kv" != "' + _ENV_END + '" ]; do '
    'export "$(printf %s "$__rocket_kv" | base64 -d)"; done;'
)
_ENV_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

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


# Waiting reasons that do not clear by waiting longer: the image or the container config is
# wrong. ``ErrImagePull`` is deliberately absent: it is the first, possibly transient, failure and
# turns into ``ImagePullBackOff`` within seconds if it persists.
_UNRECOVERABLE_WAITING_REASONS = frozenset(
    {"ImagePullBackOff", "InvalidImageName", "ErrImageNeverPull", "CreateContainerConfigError"}
)
_MAX_KUBELET_MESSAGE_CHARS = 300
# How long a sandbox start waits for its pod to run. Generous on purpose: when the pool has no
# room the cluster adds a node, which takes one to two minutes, and the first tool call of a
# session (often a clone) waits on it.
POD_START_TIMEOUT_SECONDS = 120.0


def _raise_if_container_cannot_start(pod: client.V1Pod, pod_name: str) -> None:
    """Fail at once, naming the cause, when a container is stuck for a reason waiting cannot fix."""
    for status in (pod.status.container_statuses if pod.status else None) or []:
        waiting = status.state.waiting if status.state else None
        if waiting is None or waiting.reason not in _UNRECOVERABLE_WAITING_REASONS:
            continue
        detail = (waiting.message or "").strip()[:_MAX_KUBELET_MESSAGE_CHARS]
        raise SandboxStartError(
            f"The sandbox could not start: {waiting.reason} for image '{status.image}' "
            f"(pod {pod_name}). {detail} "
            "Check that the image exists and the cluster can pull it, or choose another "
            "image in Settings > Organization > Sandboxes."
        )


def _soft_node_affinity(preferred_node: str | None) -> client.V1Affinity | None:
    """Prefer ``preferred_node`` without requiring it.

    Soft affinity (preferredDuringSchedulingIgnoredDuringExecution) scores the node +100 to
    bypass cloud block-storage re-attach latency (~90s). If the node is cordoned, drained or
    deleted, kube-scheduler places the pod elsewhere instead of leaving it Pending.
    """
    if not preferred_node:
        return None
    return client.V1Affinity(
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


class _ExecOutput:
    """Collects one exec call's stdout, stderr and exit status from the multiplexed stream."""

    def __init__(self, on_stdout_chunk: Callable[[str], None] | None) -> None:
        self.stdout = BoundedOutput()
        self.stderr = BoundedOutput()
        self.exit_code = 0
        self._on_stdout_chunk = on_stdout_chunk

    def feed(self, raw: bytes | str | None) -> None:
        """Route one WebSocket frame (first byte is the channel) to the right buffer."""
        if not raw:
            return
        if isinstance(raw, bytes):
            channel, payload = raw[0], raw[1:].decode("utf-8", errors="replace")
        elif isinstance(raw, str):
            channel, payload = ord(raw[0]), raw[1:]
        else:
            return

        if channel == ws_client.STDOUT_CHANNEL:
            self.stdout.append(payload)
            if self._on_stdout_chunk:
                self._on_stdout_chunk(payload)
        elif channel == ws_client.STDERR_CHANNEL:
            self.stderr.append(payload)
        elif channel == ws_client.ERROR_CHANNEL:
            with contextlib.suppress(Exception):
                self.exit_code = WsApiClient.parse_error_data(payload)


class K8sSandboxDriver(SessionEnvMixin, SandboxDriverProtocol):
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
        networking_v1_api: client.NetworkingV1Api | None = None,
    ) -> None:
        self.namespace: str = namespace or os.getenv("K8S_SANDBOX_NAMESPACE") or "agent-sandboxes"
        # The API pods a restricted sandbox must still reach (the git gateway); set by the chart.
        self._platform_peer = PlatformPeer.from_env()
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
        self._networking_v1: client.NetworkingV1Api | None = networking_v1_api

        # Inactivity lifecycle settings (configured via env or parameters)
        pause_env = os.getenv("SANDBOX_INACTIVITY_PAUSE_SECONDS")
        self.inactivity_pause_seconds: float = (
            float(pause_env) if pause_env is not None else 600.0  # 10m default
        )
        cleanup_env = os.getenv("SANDBOX_INACTIVITY_CLEANUP_SECONDS")
        self.inactivity_cleanup_seconds: float = (
            float(cleanup_env) if cleanup_env is not None else 0.0  # disabled by default
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

        # e.g. gvisor / kata-qemu for kernel-level isolation of untrusted agent code.
        self.runtime_class_name: str | None = os.getenv("K8S_SANDBOX_RUNTIME_CLASS") or None
        self.profiles: dict[str, PlacementProfile] = configured_profiles()

        self._specs: dict[str, WorkspaceSpec] = {}
        self._node_affinity_cache: dict[str, str] = {}
        self._destroyed_sessions: set[str] = set()
        self._last_activity: dict[str, float] = {}
        self._held_until: dict[str, float] = {}
        self._holds: dict[str, dict[str, float]] = {}
        self._init_lock = asyncio.Lock()
        # Eviction protection: how many commands are running per session, the state last
        # written to the pod, and a lock so two writes for one pod never cross.
        self._running_commands: dict[str, int] = {}
        self._protection_applied: dict[str, bool] = {}
        self._protection_locks: dict[str, asyncio.Lock] = {}
        self._protection_tasks: set[asyncio.Task[None]] = set()

    def hold_awake(self, session_id: str, seconds: float, reason: str = "process") -> None:
        if seconds <= 0:
            if session_id in self._holds:
                self._holds[session_id].pop(reason, None)
                if not self._holds[session_id]:
                    self._holds.pop(session_id, None)
                    self._held_until.pop(session_id, None)
                else:
                    self._held_until[session_id] = max(self._holds[session_id].values())
            else:
                self._held_until.pop(session_id, None)
        else:
            now = time.time()
            self._holds.setdefault(session_id, {})[reason] = now + seconds
            self._held_until[session_id] = max(self._holds[session_id].values())
        # Background processes and turn executions need the pod kept from eviction now.
        with contextlib.suppress(RuntimeError):  # no running loop: the next command syncs it
            task = asyncio.get_running_loop().create_task(self._sync_protection(session_id))
            self._protection_tasks.add(task)
            task.add_done_callback(self._protection_tasks.discard)

    def is_held(self, session_id: str) -> bool:
        """Return True if the sandbox is held awake by an active lease."""
        return self._held_until.get(session_id, 0) > time.time()

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
                    # Synchronous in kubernetes_asyncio: awaiting its ``None`` raised and sent
                    # every in-cluster pod down the kubeconfig fallback with a spurious warning.
                    config.load_incluster_config()  # type: ignore[no-untyped-call]  # untyped upstream
                except config.ConfigException:
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

    async def _networking(self) -> client.NetworkingV1Api:
        if self._networking_v1 is None:
            core_v1, _ = await self._ensure_clients()
            self._networking_v1 = client.NetworkingV1Api(
                api_client=getattr(core_v1, "api_client", None)
            )
        return self._networking_v1

    def _egress_policy_name(self, session_id: str) -> str:
        return f"np-session-{_sanitize_k8s_name(session_id)}"

    async def _apply_egress_policy(self, spec: WorkspaceSpec) -> None:
        """Give a restricted sandbox its own policy (create, or replace on conflict)."""
        networking = await self._networking()
        policy = build_egress_policy(
            name=self._egress_policy_name(spec.session_id),
            namespace=self.namespace,
            session_label=_sanitize_label_value(spec.session_id),
            cidrs=await resolve_destinations(spec.egress_allowlist),
            labels=self._build_labels(spec),
            internet_except_internal=not spec.egress_allowlist,
            platform_peer=self._platform_peer,
        )
        try:
            await networking.create_namespaced_network_policy(namespace=self.namespace, body=policy)
        except ApiException as err:
            if err.status != 409:
                raise SandboxExecutionError(
                    f"Could not apply the egress policy for {spec.session_id}: {err}"
                ) from err
            await networking.replace_namespaced_network_policy(
                name=policy.metadata.name, namespace=self.namespace, body=policy
            )

    async def _delete_egress_policy(self, session_id: str) -> None:
        """Remove the sandbox's policy, if it can have one. Best effort: a missing policy (or an
        older chart whose role cannot touch policies) must never block destroying the workspace."""
        known = self._specs.get(session_id)
        if known is not None and not is_restricted(known):
            return  # a sandbox the driver knows is unrestricted never had a policy
        try:
            networking = await self._networking()
            await networking.delete_namespaced_network_policy(
                name=self._egress_policy_name(session_id), namespace=self.namespace
            )
        except ApiException as err:
            if err.status not in (403, 404):
                logger.warning("Egress policy deletion error during destroy: %s", err)
        except Exception as err:
            logger.debug("Egress policy deletion skipped: %s", err)

    def _pvc_name(self, session_id: str) -> str:
        return f"pvc-session-{_sanitize_k8s_name(session_id)}"

    def _pod_name(self, session_id: str) -> str:
        return f"pod-session-{_sanitize_k8s_name(session_id)}"

    def _pod_security_context(self, spec: WorkspaceSpec) -> client.V1PodSecurityContext | None:
        # fs_group makes the PVC writable by the unprivileged user. It is set even when the
        # organization does not *require* non-root: the default devbox image runs as uid 1000
        # on its own, and a root-owned volume would be read-only to it.
        if not spec.require_non_root:
            return client.V1PodSecurityContext(fs_group=SANDBOX_UID)
        return client.V1PodSecurityContext(
            run_as_non_root=True, run_as_user=SANDBOX_UID, fs_group=SANDBOX_UID
        )

    def _build_labels(self, spec: WorkspaceSpec) -> dict[str, str]:
        labels = {
            "agent.session.id": _sanitize_label_value(spec.session_id),
            "tenant.org.id": _sanitize_label_value(spec.tenant_org_id),
            "tenant.user.id": _sanitize_label_value(spec.tenant_user_id),
            "app.kubernetes.io/managed-by": "rocket-chat",
        }
        if is_restricted(spec):
            # Excluded from the namespace-wide internet policy; the per-sandbox one governs it.
            labels[EGRESS_LABEL] = EGRESS_RESTRICTED
        return labels

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
        profile = self._profile_of(spec)
        pvc_manifest = client.V1PersistentVolumeClaim(
            metadata=client.V1ObjectMeta(
                name=pvc_name,
                namespace=self.namespace,
                labels=self._build_labels(spec),
            ),
            spec=client.V1PersistentVolumeClaimSpec(
                access_modes=["ReadWriteOnce"],
                resources=client.V1VolumeResourceRequirements(requests={"storage": storage_size}),
                storage_class_name=(profile.storage_class_name if profile else None)
                or self.storage_class_name,
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
                _raise_if_container_cannot_start(pod, pod_name)

            except ApiException as err:
                if err.status != 404:
                    raise SandboxError(f"Failed querying pod {pod_name}: {err}") from err

            await asyncio.sleep(0.1)

        raise SandboxTimeoutError(f"Timed out waiting for Pod {pod_name} to enter Running state")

    async def _await_start(self, core_v1: client.CoreV1Api, pod_name: str, session_id: str) -> None:
        """Wait for the pod to run; a pod that cannot start is removed, a slow one is kept."""
        try:
            await self._wait_for_pod_running(
                core_v1, pod_name, session_id, timeout_seconds=POD_START_TIMEOUT_SECONDS
            )
        except SandboxTimeoutError as err:
            # Still Pending: usually a node being added or a large image being pulled. The pod
            # is kept so the next start resumes the wait instead of queueing a new pod.
            raise SandboxTimeoutError(
                f"The sandbox is still starting after {POD_START_TIMEOUT_SECONDS:.0f}s (pod "
                f"{pod_name} is waiting for a node or its image); it keeps starting, so retry "
                "the same command in a minute."
            ) from err
        except SandboxError:
            # A pod that never started keeps its image: remove it so the next attempt (after
            # the image or catalogue is fixed) creates a fresh one instead of reusing it.
            await self._delete_pod_quietly(core_v1, pod_name)
            raise
        self.record_activity(session_id)

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
            if phase == "Pending" and not is_terminating:
                # An earlier start ran out of time while the pod waited for a node or its image:
                # keep waiting on it, since recreating it would start scheduling over.
                await self._await_start(core_v1, pod_name, session_id)
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

        preferred_node = await self._preferred_node(core_v1, session_id)
        if is_restricted(spec):
            # The policy must exist before the pod, or it would briefly run unrestricted.
            await self._apply_egress_policy(spec)
        pod_manifest = self._build_pod(spec, pod_name, _soft_node_affinity(preferred_node))

        try:
            await core_v1.create_namespaced_pod(
                namespace=self.namespace,
                body=pod_manifest,
            )
        except ApiException as err:
            if err.status != 409:
                raise SandboxError(f"Failed creating Pod {pod_name}: {err}") from err

        await self._await_start(core_v1, pod_name, session_id)

    async def _preferred_node(self, core_v1: client.CoreV1Api, session_id: str) -> str | None:
        """The node whose volume attachment or warm pods make it the cheapest place to resume."""
        if session_id in self._node_affinity_cache:
            return self._node_affinity_cache[session_id]
        if self.warmed_pool_size <= 0:
            return None
        # When warm pods are enabled, prefer worker nodes that already have warm pods running
        for i in range(self.warmed_pool_size):
            try:
                warm_pod = await core_v1.read_namespaced_pod(
                    name=self._warm_pod_name(i),
                    namespace=self.namespace,
                )
            except Exception:
                continue
            if (
                warm_pod.status
                and warm_pod.status.phase == "Running"
                and warm_pod.spec
                and warm_pod.spec.node_name
            ):
                return warm_pod.spec.node_name
        return None

    def _build_pod(
        self, spec: WorkspaceSpec, pod_name: str, affinity: client.V1Affinity | None
    ) -> client.V1Pod:
        """The pod manifest for a session's sandbox."""
        pvc_name = self._pvc_name(spec.session_id)
        profile = self._profile_of(spec)
        limits, requests = _sizing(spec, profile)

        combined_env = {**DEFAULT_SANDBOX_CACHE_ENV, **spec.env_vars}
        env_vars = [client.V1EnvVar(name=k, value=str(v)) for k, v in combined_env.items()]

        # Respect custom container image from WorkspaceSpec or fall back to default
        target_image = spec.container_image or self.default_image

        image_pull_refs = [
            client.V1LocalObjectReference(name=s) for s in self.image_pull_secrets
        ] or None

        # Unmanaged pods are annotated safe-to-evict by default so Cluster Autoscaler
        # and Karpenter can downscale nodes or drain nodes during upgrades when idle.
        annotations = {
            CLUSTER_AUTOSCALER_SAFE_TO_EVICT: "true",
            KARPENTER_DO_NOT_DISRUPT: "false",
            **(profile.annotations if profile else {}),
        }
        # The platform's own labels win: they are how it finds and isolates its pods.
        labels = {**(profile.labels if profile else {}), **self._build_labels(spec)}

        return client.V1Pod(
            metadata=client.V1ObjectMeta(
                name=pod_name,
                namespace=self.namespace,
                labels=labels,
                annotations=annotations,
            ),
            spec=client.V1PodSpec(
                affinity=affinity,
                node_selector=dict(profile.node_selector)
                if profile and profile.node_selector
                else None,
                tolerations=_tolerations(profile),
                priority_class_name=profile.priority_class_name if profile else None,
                restart_policy="Never",
                termination_grace_period_seconds=self.grace_period_seconds,
                automount_service_account_token=False,
                # The container's own PID 1 is `tail`, which never reaps children, so every killed or
                # finished process would linger as a zombie. With a shared process namespace the
                # pause container is PID 1 and reaps them (Docker sandboxes use `init` for this).
                share_process_namespace=True,
                image_pull_secrets=image_pull_refs,
                runtime_class_name=(profile.runtime_class_name if profile else None)
                or self.runtime_class_name,
                security_context=self._pod_security_context(spec),
                containers=[
                    client.V1Container(
                        name="sandbox",
                        image=target_image,
                        command=["tail", "-f", "/dev/null"],
                        working_dir="/workspace",
                        env=env_vars,
                        resources=client.V1ResourceRequirements(limits=limits, requests=requests),
                        security_context=client.V1SecurityContext(
                            allow_privilege_escalation=False,
                            capabilities=client.V1Capabilities(
                                drop=sandbox_dropped_capabilities(spec.require_non_root)
                            ),
                            seccomp_profile=client.V1SeccompProfile(type="RuntimeDefault"),
                        ),
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

    def _profile_of(self, spec: WorkspaceSpec) -> PlacementProfile | None:
        if not spec.profile:
            return None
        profile = self.profiles.get(spec.profile)
        if profile is None:
            raise SandboxError(
                f"Sandbox profile '{spec.profile}' is not defined by this deployment "
                f"({PROFILES_ENV}); an administrator must choose another one for this sandbox."
            )
        return profile

    async def _delete_pod_quietly(self, core_v1: client.CoreV1Api, pod_name: str) -> None:
        try:
            await core_v1.delete_namespaced_pod(
                name=pod_name, namespace=self.namespace, grace_period_seconds=0
            )
        except ApiException as err:
            if err.status != 404:
                logger.warning(
                    "Could not remove the pod %s that failed to start: %s", pod_name, err
                )

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

        await self._delete_egress_policy(session_id)

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

    def _wants_protection(self, session_id: str) -> bool:
        """A command is running, or background processes hold the sandbox awake."""
        held = self._held_until.get(session_id, 0) > time.time()
        return self._running_commands.get(session_id, 0) > 0 or held

    async def _sync_protection(self, session_id: str) -> None:
        """Bring the pod's eviction annotations in line with what the session needs now.

        Commands overlap (parallel tool calls, a background process with a shell): toggling per
        command let the first to finish mark the pod evictable while others still ran. The pod
        is written only when the needed state differs from the last one written.
        """
        lock = self._protection_locks.setdefault(session_id, asyncio.Lock())
        async with lock:
            wanted = self._wants_protection(session_id)
            if self._protection_applied.get(session_id) == wanted:
                return
            await self.set_eviction_protection(session_id, protected=wanted)
            self._protection_applied[session_id] = wanted

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
        held_until = self._held_until.get(session_id, 0)
        active_execs = self._running_commands.get(session_id, 0)
        patch = {
            "metadata": {
                "annotations": {
                    CLUSTER_AUTOSCALER_SAFE_TO_EVICT: "false" if protected else "true",
                    KARPENTER_DO_NOT_DISRUPT: "true" if protected else "false",
                    "rocket.chat/hold-until": str(int(held_until)),
                    "rocket.io/protected-until": str(int(held_until)),
                    "rocket.chat/active-execs": str(active_execs),
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

    async def _assert_pod_running(self, core_v1: Any, pod_name: str) -> None:
        try:
            res = core_v1.read_namespaced_pod(name=pod_name, namespace=self.namespace)
            pod = await res if inspect.isawaitable(res) else res
            if not pod.status or pod.status.phase != "Running":
                raise SandboxNotFoundError(f"Active sandbox pod {pod_name} is not running.")
        except ApiException as err:
            if err.status == 404:
                raise SandboxNotFoundError(f"Sandbox pod {pod_name} not found.") from err
            raise SandboxError(f"Failed verifying pod {pod_name}: {err}") from err

    def _shell_command(
        self,
        session_id: str,
        command: str,
        workdir: str,
        timeout_seconds: int,
        env: dict[str, str] | None,
    ) -> tuple[list[str], bytes | None]:
        """The ``/bin/sh -c`` argv that runs ``command`` in ``workdir``, and its stdin.

        The environment (the git gateway capability among it) never goes on the command line,
        which anything in the pod can read in ``/proc/*/cmdline`` and the API server records in
        its audit log of exec requests. It is sent on stdin as base64 ``KEY=VALUE`` lines read by
        a short prelude; the command itself then reads ``/dev/null``, as without stdin.
        """
        deadline_command = wrap_with_deadline(command, timeout_seconds)
        merged = self.merged_env(session_id, env)
        # The cache defaults are in the pod spec already; anything else, or a changed value,
        # travels with the command.
        dynamic_env = {k: v for k, v in merged.items() if DEFAULT_SANDBOX_CACHE_ENV.get(k) != v}
        if not dynamic_env:
            return ["/bin/sh", "-c", f"cd {shlex.quote(workdir)} && {deadline_command}"], None
        payload = "".join(
            base64.b64encode(f"{key}={value}".encode()).decode() + "\n"
            for key, value in dynamic_env.items()
            if _ENV_KEY.match(key)
        )
        script = f"{_ENV_PRELUDE} cd {shlex.quote(workdir)} && {{ {deadline_command}; }} </dev/null"
        return ["/bin/sh", "-c", script], f"{payload}{_ENV_END}\n".encode()

    @traced_exec
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
        await self._assert_pod_running(core_v1, pod_name)
        exec_cmd, stdin_payload = self._shell_command(
            session_id, command, workdir, timeout_seconds, env
        )

        start_time = time.monotonic()
        output = _ExecOutput(on_stdout_chunk)
        self._running_commands[session_id] = self._running_commands.get(session_id, 0) + 1
        await self._sync_protection(session_id)
        try:
            await asyncio.wait_for(
                self._stream_exec(ws_core_v1, pod_name, exec_cmd, output, stdin_payload),
                timeout=timeout_seconds + CLIENT_DEADLINE_MARGIN_SECONDS,
            )
        except TimeoutError as err:
            raise SandboxTimeoutError(
                f"Command timed out after {timeout_seconds} seconds: {command}",
                output.stdout.text(),
                output.stderr.text(),
            ) from err
        except SandboxError:
            raise
        except Exception as err:
            raise SandboxExecutionError(f"Exec stream failure in pod {pod_name}: {err}") from err
        finally:
            self._running_commands[session_id] -= 1
            if not self._running_commands[session_id]:
                self._running_commands.pop(session_id)
            await self._sync_protection(session_id)

        self.record_activity(session_id)
        elapsed = time.monotonic() - start_time
        if hit_deadline(output.exit_code, elapsed, timeout_seconds):
            raise SandboxTimeoutError(
                f"Command timed out after {timeout_seconds} seconds: {command}",
                output.stdout.text(),
                output.stderr.text(),
            )
        return ExecResult(
            exit_code=output.exit_code,
            stdout=output.stdout.text(),
            stderr=output.stderr.text(),
            duration_ms=int(elapsed * 1000),
        )

    async def _stream_exec(
        self,
        ws_core_v1: Any,
        pod_name: str,
        exec_cmd: list[str],
        output: _ExecOutput,
        stdin_payload: bytes | None = None,
    ) -> None:
        ws_conn: Any = await ws_core_v1.connect_get_namespaced_pod_exec(
            name=pod_name,
            namespace=self.namespace,
            command=exec_cmd,
            stderr=True,
            stdin=stdin_payload is not None,
            stdout=True,
            tty=False,
            _preload_content=False,
        )
        async with ws_conn as ws:
            if stdin_payload is not None:
                await ws.send_bytes(_STDIN_FRAME + stdin_payload)
            async for message in ws:
                output.feed(message.data)

    async def read_file(
        self,
        session_id: str,
        path: str,
        start_line: int | None = None,
        end_line: int | None = None,
    ) -> str:
        """Read text file contents from the pod filesystem."""
        target_path = normalize_workspace_path(path)
        read_cmd = build_read_file_command(target_path)
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

        for command in build_write_file_commands(target_path, content.encode("utf-8")):
            res = await self.exec_command(session_id, command)
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
            return parse_list_files_output(res.stdout)
        except ValueError as err:
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

    async def _terminal_environment(self, session_id: str) -> str:
        """Shell text that loads the session environment into a terminal, or ``""``.

        A terminal's stdin is a TTY that echoes what it receives, so the environment cannot be
        sent there as for a command. A plain exec (which gets it on stdin) writes it to a
        private file instead; the terminal's shell reads and deletes it before it starts.
        """
        if not self._env_store().get(session_id):
            return ""
        path = f"/tmp/.rocket-env-{uuid.uuid4().hex}"
        written = await self.exec_command(
            session_id, f"umask 077 && export -p > {path}", timeout_seconds=30
        )
        if written.exit_code != 0:
            logger.warning("Could not prepare the terminal environment: %s", written.stderr)
            return ""
        return f". {path} 2>/dev/null; rm -f {path}; "

    async def open_pty(self, session_id: str, cols: int = 80, rows: int = 24) -> PtyChannel:
        """Start a login shell in the pod with a real TTY over the exec WebSocket."""
        pod_name = self._pod_name(session_id)
        status = await self.get_status(session_id)
        if status != SandboxStatus.RUNNING:
            raise SandboxNotFoundError(
                f"Pod {pod_name} is in status {status}, not {SandboxStatus.RUNNING}."
            )
        _, ws_core_v1 = await self._ensure_clients()
        env_prefix = await self._terminal_environment(session_id)
        ws_conn: Any = await ws_core_v1.connect_get_namespaced_pod_exec(
            name=pod_name,
            namespace=self.namespace,
            command=["/bin/sh", "-c", f"cd /workspace && {env_prefix}{SHELL_LAUNCHER}"],  # type: ignore[arg-type]
            stderr=True,
            stdin=True,
            stdout=True,
            tty=True,
            _preload_content=False,
        )
        # The generated client returns aiohttp's un-awaited ``ws_connect`` context manager when the
        # response is not preloaded; a real cluster needs the second await to get the socket.
        if not hasattr(ws_conn, "receive"):
            ws_conn = await ws_conn
        pty = K8sPty(ws_conn)
        await pty.resize(cols, rows)
        return pty

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
                    automount_service_account_token=False,
                    share_process_namespace=True,
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
                            security_context=client.V1SecurityContext(
                                allow_privilege_escalation=False,
                                capabilities=client.V1Capabilities(
                                    drop=["NET_RAW", "SYS_ADMIN", "SYS_PTRACE", "DAC_READ_SEARCH"]
                                ),
                                seccomp_profile=client.V1SeccompProfile(type="RuntimeDefault"),
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

    async def adopt_existing_sandboxes(self) -> None:
        """Start idle tracking for managed pods and workspaces created before this process.

        Activity is held in memory, so after an API restart a running pod would otherwise
        never be paused and a hibernated workspace (a PVC with no pod) of a session deleted in
        the meantime would never be reaped. Adopted sandboxes count as active *now*: nothing
        is paused or purged before one full threshold has passed.
        """
        core_v1, _ = await self._ensure_clients()
        selector = "app.kubernetes.io/managed-by=rocket-chat"
        pods = await core_v1.list_namespaced_pod(namespace=self.namespace, label_selector=selector)
        claims = await core_v1.list_namespaced_persistent_volume_claim(
            namespace=self.namespace, label_selector=selector
        )
        resources: list[Any] = [*pods.items, *claims.items]
        for resource in resources:
            session_id = (resource.metadata.labels or {}).get("agent.session.id", "")
            if session_id and session_id not in self._last_activity:
                self.record_activity(session_id)

    async def _pod_is_cluster_protected(self, session_id: str, now: float) -> bool:
        """Check if annotations on Kubernetes indicate another replica is actively using the pod."""
        try:
            core_v1, _ = await self._ensure_clients()
            res = core_v1.read_namespaced_pod(
                name=self._pod_name(session_id), namespace=self.namespace
            )
            pod = await res if inspect.isawaitable(res) else res
            raw_ann = getattr(getattr(pod, "metadata", None), "annotations", None)
            ann = raw_ann if isinstance(raw_ann, dict) else {}
            if ann.get(CLUSTER_AUTOSCALER_SAFE_TO_EVICT) == "false":
                return True
            if ann.get(KARPENTER_DO_NOT_DISRUPT) == "true":
                return True
            if float(ann.get("rocket.chat/hold-until", 0)) > now:
                return True
            if int(ann.get("rocket.chat/active-execs", 0)) > 0:
                return True
        except Exception:
            pass
        return False

    async def pause_inactive_sandboxes(
        self,
        threshold_seconds: float | None = None,
        active_sessions: set[str] | None = None,
        session_idleness: Mapping[str, float] | None = None,
    ) -> list[str]:
        """Hibernate running sandboxes that exceeded configured idle duration."""
        threshold = (
            threshold_seconds if threshold_seconds is not None else self.inactivity_pause_seconds
        )
        now = time.time()
        paused: list[str] = []

        activity = dict(self._last_activity)
        if session_idleness:
            for s_id, t_stamp in session_idleness.items():
                if s_id not in activity or t_stamp > activity[s_id]:
                    activity[s_id] = t_stamp

        for session_id, last_time in list(activity.items()):
            if active_sessions and session_id in active_sessions:
                continue
            # A hold that ran out (its background processes ended) releases the pod.
            if self._protection_applied.get(session_id):
                await self._sync_protection(session_id)
            if self._held_until.get(session_id, 0) > now:
                continue
            if now - last_time >= threshold:
                status = await self.get_status(session_id)
                if status == SandboxStatus.RUNNING:
                    if await self._pod_is_cluster_protected(session_id, now):
                        continue
                    logger.info(
                        "Pausing inactive sandbox %s (idle for %.1fs, threshold %.1fs)",
                        session_id,
                        now - last_time,
                        threshold,
                    )
                    await self.hibernate_sandbox(session_id)
                    paused.append(session_id)
        return paused

    async def cleanup_inactive_sandboxes(
        self,
        threshold_seconds: float | None = None,
        is_live: SessionLivenessCheck | None = None,
    ) -> list[str]:
        """Purge hibernated workspaces of sessions that no longer exist.

        Disabled unless a positive threshold is configured: the workspace is the session's
        working copy, so it is removed with the session, not on a timer. ``is_live`` guards the
        timer path against destroying work a user can still resume.
        """
        threshold = (
            threshold_seconds if threshold_seconds is not None else self.inactivity_cleanup_seconds
        )
        if threshold <= 0:
            return []
        now = time.time()
        cleaned: list[str] = []

        for session_id, last_time in list(self._last_activity.items()):
            if now - last_time < threshold:
                continue
            if is_live is not None and await is_live(session_id):
                continue
            status = await self.get_status(session_id)
            if status in (SandboxStatus.HIBERNATED, SandboxStatus.ERROR):
                logger.info(
                    "Purging orphaned sandbox workspace %s (idle %.1fs, threshold %.1fs)",
                    session_id,
                    now - last_time,
                    threshold,
                )
                await self.destroy_workspace(session_id)
                cleaned.append(session_id)
                self._last_activity.pop(session_id, None)
        return cleaned

    async def reconcile_inactivity(
        self,
        is_live: SessionLivenessCheck | None = None,
        active_sessions: set[str] | None = None,
        session_idleness: Mapping[str, float] | None = None,
    ) -> tuple[list[str], list[str]]:
        """One sweep: adopt sandboxes that outlived a restart, pause idle compute, purge orphans."""
        await self.adopt_existing_sandboxes()
        paused = await self.pause_inactive_sandboxes(
            active_sessions=active_sessions, session_idleness=session_idleness
        )
        cleaned = await self.cleanup_inactive_sandboxes(is_live=is_live)
        return paused, cleaned


def _sizing(
    spec: WorkspaceSpec, profile: PlacementProfile | None
) -> tuple[dict[str, str], dict[str, str]]:
    """(limits, requests) of the sandbox container: the profile's sizing, else the session's
    resources and the deployment's defaults."""
    limits = {
        "cpu": os.getenv(
            "SANDBOX_CPU_LIMIT",
            str(spec.resources.cpu_cores) if spec.resources.cpu_cores else "500m",
        ),
        "memory": os.getenv("SANDBOX_MEMORY_LIMIT", spec.resources.memory_limit or "512Mi"),
    }
    requests = {
        "cpu": os.getenv("SANDBOX_CPU_REQUEST", "50m"),
        "memory": os.getenv("SANDBOX_MEMORY_REQUEST", "128Mi"),
    }
    if profile is not None:
        limits.update(profile.limits)
        requests.update(profile.requests)
    return limits, requests


def _tolerations(profile: PlacementProfile | None) -> list[client.V1Toleration] | None:
    if profile is None or not profile.tolerations:
        return None
    return [
        client.V1Toleration(
            key=t.get("key"),
            operator=t.get("operator"),
            value=t.get("value"),
            effect=t.get("effect"),
            toleration_seconds=t.get("tolerationSeconds"),
        )
        for t in profile.tolerations
    ]

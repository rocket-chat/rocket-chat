# `sandbox-driver`

Pluggable execution driver package providing isolated filesystem and command execution environments for autonomous coding agents.

## Overview

This package implements the `SandboxDriverProtocol` defined in `specifications/interfaces/sandbox.py`. It provides two pluggable drivers:
- `DockerSandboxDriver`: for local single-host development and lightweight Docker environments.
- `K8sSandboxDriver`: for production Kubernetes clusters with PersistentVolumeClaim management, cloud CSI support, and node-affinity pinning.

### Key Capabilities
- **Persistent Storage Lifecycle:** Manages Docker Named Volumes (`sandbox_vol_<session_id>`) or Kubernetes PersistentVolumeClaims (`pvc-session-<session-id>`) independently of container/pod lifecycles.
- **Scale-to-Zero Hibernation:** Spawns ephemeral containers or pods attached to persistent volumes; supports scale-to-zero via `hibernate_sandbox` while retaining volume integrity.
- **Sub-Second Cloud Resume via Soft Node-Affinity Pinning:** In `K8sSandboxDriver`, records the scheduled node and injects native soft node affinity (`preferredDuringSchedulingIgnoredDuringExecution` with weight 100 on `kubernetes.io/hostname`) on pod restart, bypassing cloud EBS/Disk detachment latency (~90s) and resuming in `< 1s`. If the node is cordoned or deleted, `kube-scheduler` immediately schedules the pod on another node with zero delay.
- **Node Autoscaling & Eviction Lifecycle:** Solves unmanaged naked pod lifecycle constraints. Injects `cluster-autoscaler.kubernetes.io/safe-to-evict: "true"` and `karpenter.sh/do-not-disrupt: "false"` when idle so nodepool downscaling and upgrades are not blocked. Dynamically toggles eviction protection during active command executions, and recovers evicted pods automatically via PVC-backed hibernation.
- **Command Execution & Streaming:** Runs commands inside containers or pods with stdout/stderr chunk streaming, timeout enforcement, and exit code capture via Docker Engine or Kubernetes Exec WebSocket API.
- **Atomic Filesystem Operations:** Direct read, write, directory listing, and diff patching inside workspace directories.

## Installation

Dependencies are managed using `uv` at the monorepo root:

```bash
uv sync
```

## Running Tests

Run the unit test suite:
```bash
uv run pytest tests/unit/test_docker_driver.py tests/unit/test_k8s_driver.py -v
```

Run the integration test suite:
```bash
# Docker Driver
uv run pytest tests/integration/test_docker_sandbox_lifecycle.py -v

# Kubernetes Driver
uv run pytest tests/integration/test_k8s_sandbox_lifecycle.py -v
```

## Environment Variables

| Variable | Description | Default |
| :--- | :--- | :--- |
| `DEFAULT_SANDBOX_DRIVER` | Driver to activate in FastAPI control plane (`docker` or `k8s`) | `docker` |
| `DOCKER_HOST` | Custom Docker daemon socket path or TCP endpoint | System default (`/var/run/docker.sock`) |
| `K8S_SANDBOX_NAMESPACE` | Kubernetes namespace for pods and PVCs | `agent-sandboxes` |
| `K8S_CLOUD_PROVIDER` | Cloud provider for auto storage class selection (`aws`, `azure`, `gcp`) | None |
| `K8S_STORAGE_CLASS` | Explicit Kubernetes StorageClass name override (e.g. `gp3`, `managed-csi`) | None (cluster default) |
| `K8S_DEFAULT_IMAGE` | Ephemeral container image for agent sandboxes | `python:3.12-slim` |

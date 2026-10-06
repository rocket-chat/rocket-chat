# Kubernetes Quickstart

Rocket Chat features native, first-class support for Kubernetes. When running in Kubernetes mode (`DEFAULT_SANDBOX_DRIVER="k8s"`), agent execution happens inside ephemeral Pods with persistent volume storage provisioned on demand in a dedicated `agent-sandboxes` namespace.

---

## 1. Setting Up a Local Cluster (Kind or Minikube)

If testing locally without a remote cloud cluster, you can use **Kind** (Kubernetes in Docker):

```bash
# Create a Kind cluster
kind create cluster --name rocket-local

# Verify cluster connectivity
kubectl cluster-info --context kind-rocket-local
```

---

## 2. Deploying Rocket Chat via Helm

Rocket Chat is distributed as an official OCI Helm chart via GitHub Container Registry (`ghcr.io/rocket-chat/charts/rocket-chat`).

### Option A: Install via Official OCI Chart (Recommended)
Install directly from the public registry without needing to clone the repository:

```bash
helm install rocket-chat oci://ghcr.io/rocket-chat/charts/rocket-chat \
  --namespace rocket-chat \
  --create-namespace \
  --set sandbox.storageClass="standard" \
  --set global.domain="rocket.local"
```

### Option B: Install from Local Clone
If you have cloned the repository (see [Setup from Source](/getting-started/from-source)), install using the local chart path:

```bash
helm install rocket-chat deploy/helm/platform \
  --namespace rocket-chat \
  --create-namespace \
  --set sandbox.storageClass="standard" \
  --set global.domain="rocket.local"
```

The Helm chart automatically provisions:
1. **Control Plane Deployments & Services:** `rocket-chat-backend` and `rocket-chat-frontend`.
2. **Dedicated Sandbox Namespace:** `agent-sandboxes`.
3. **Sandbox RBAC:** `ServiceAccount`, `Role`, and `RoleBinding` granting the backend permissions to manage `pods`, `persistentvolumeclaims`, and `pods/exec` strictly within `agent-sandboxes`.
4. **Security Controls:** `ResourceQuota` preventing runaway container proliferation and `NetworkPolicy` blocking cloud instance metadata requests (`169.254.169.254/32`).

---

## 3. Cloud Storage Class Selection

When deploying on managed cloud Kubernetes services, specify your cloud provider's high-performance block storage CSI:

=== "AWS EKS (Amazon Web Services)"
    ```bash
    helm upgrade --install rocket-chat deploy/helm/platform \
      --namespace rocket-chat \
      --set sandbox.storageClass="gp3"
    ```

=== "Azure AKS (Microsoft Azure)"
    ```bash
    helm upgrade --install rocket-chat deploy/helm/platform \
      --namespace rocket-chat \
      --set sandbox.storageClass="managed-csi"
    ```

=== "GCP GKE (Google Cloud Platform)"
    ```bash
    helm upgrade --install rocket-chat deploy/helm/platform \
      --namespace rocket-chat \
      --set sandbox.storageClass="pd-balanced"
    ```

---

## 4. Verifying Sandbox Execution

Once installed, check that the backend is running and the sandbox namespace is active:

```bash
# Check control plane pods
kubectl get pods -n rocket-chat

# Verify the dedicated sandboxes namespace
kubectl get namespaces | grep agent-sandboxes

# Inspect role bindings in the sandbox namespace
kubectl get rolebinding -n agent-sandboxes
```

When an agent session begins, the backend dynamically spawns:
- `pvc-session-<id>`: Retains the workspace repository and git history.
- `pod-session-<id>`: Executes tool commands (file viewing, test suites, compiler invocations).

When idle, the pod automatically hibernates (scale-to-zero), releasing CPU/memory while preserving the bound PVC.

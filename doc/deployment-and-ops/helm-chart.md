# Production Kubernetes Helm Chart

Rocket Chat provides an official production Helm chart in `deploy/helm/platform/` for cloud enterprise Kubernetes deployments.

---

## 1. Chart Structure

```text
deploy/helm/platform/
├── Chart.yaml                  # Chart metadata and semantic versioning
├── values.yaml                 # Configurable deployment values
├── templates/
│   ├── _helpers.tpl            # Template name and label helpers
│   ├── backend/
│   │   ├── deployment.yaml     # Backend FastAPI control plane (2+ replicas)
│   │   ├── service.yaml        # ClusterIP service on port 8000
│   │   ├── serviceaccount.yaml # ServiceAccount for sandbox management
│   │   └── rbac.yaml           # Role & RoleBinding in agent-sandboxes
│   ├── frontend/
│   │   ├── deployment.yaml     # Next.js 15 standalone server (2+ replicas)
│   │   └── service.yaml        # ClusterIP service on port 3000
│   ├── ingress.yaml            # Ingress rules for Web UI and REST/WebSocket API
│   ├── configmap.yaml          # Environment settings
│   ├── secrets.yaml            # Database connection, encryption key, Slack tokens
│   └── sandboxes/
│       ├── namespace.yaml      # Dedicated agent-sandboxes namespace
│       ├── networkpolicy.yaml  # SSRF metadata blocking (169.254.169.254)
│       └── resourcequota.yaml  # Caps max pods and storage in sandboxes namespace
```

---

## 2. Installation Guide

### Step 1: Add Custom Values
Create a production values file (e.g. `prod-values.yaml`):

```yaml
global:
  domain: "rocket.internal.company.com"

backend:
  database:
    url: "postgresql+asyncpg://rocket_app:StrongPass@pg-cluster.internal:5432/rocket_chat"
  encryption:
    masterKey: "0123456789abcdef0123456789abcdef"
  slack:
    botToken: "xoxb-..."
    appToken: "xapp-..."

sandbox:
  storageClass: "gp3" # Use gp3 for AWS EKS, managed-csi for Azure AKS, pd-balanced for GCP GKE
```

### Step 2: Install via Helm (Local Chart or OCI Registry)
From local repository checkout:
```bash
helm install rocket-chat deploy/helm/platform \
  --namespace rocket-chat \
  --create-namespace \
  -f prod-values.yaml
```

Or install directly from the public GitHub OCI Container Registry:
```bash
helm install rocket-chat oci://ghcr.io/rocket-chat/charts/rocket-chat \
  --version 0.1.2 \
  --namespace rocket-chat \
  --create-namespace \
  -f prod-values.yaml
```

---

## 3. Important Operational Notes

1. **Slack Socket Mode:** The Helm chart creates zero Ingress rules or public ports for Slack. Slack Bolt connects outbound via WebSockets.
2. **Dedicated Sandbox Namespace:** Sandboxes live in `agent-sandboxes`, completely isolated from the control plane namespace (`rocket-chat`).
3. **Fine-Grained RBAC:** The backend `ServiceAccount` possesses RBAC permissions to create, delete, and exec pods strictly within `agent-sandboxes`. It has zero permissions in any other namespace.

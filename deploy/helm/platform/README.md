# Rocket Chat Platform Helm Chart

[![Artifact Hub](https://img.shields.io/endpoint?url=https%3A%2F%2Fartifacthub.io%2Fbadge%2Frepository%2Frocket-chat&style=flat-square)](https://artifacthub.io/packages/helm/rocket-chat/rocket-chat)
[![License: GPL-3.0](https://img.shields.io/badge/License-GPL--3.0-green?style=flat-square)](https://github.com/rocket-chat/rocket-chat/blob/main/LICENSE)

Production-grade Helm chart for deploying the **Rocket Chat** autonomous AI pair-programming platform on Kubernetes.

Rocket Chat enables engineering organizations to safely execute autonomous AI coding agents inside isolated multi-tenant Kubernetes Pods, complete with real-time WebSocket mission telemetry, full-duplex streaming terminals, and Bring-Your-Own-Key (BYOK) LLM gateway routing.

## Prerequisites

- Kubernetes 1.28+
- Helm 3.12+
- Dynamic Persistent Volume Provisioner supporting one of:
  - AWS EKS: `gp3`
  - Azure AKS: `managed-csi`
  - GCP GKE: `pd-balanced`
  - Local / Kind: `standard` or `local-path`

## Quick Start

### 1. Local Development (Kind / Minikube)

```bash
# Deploy using the local values profile (NodePort, local-path storage, in-cluster PostgreSQL)
helm upgrade --install rocket-chat deploy/helm/platform \
  --namespace rocket-chat \
  --create-namespace \
  -f deploy/helm/platform/values.local.yaml \
  --set backend.openrouterApiKey="$OPENROUTER_API_KEY"
```

### 2. Production Cloud Deployment (EKS, AKS, GKE)

```bash
# Verify chart syntax
helm lint deploy/helm/platform

# Deploy into dedicated namespace
helm upgrade --install rocket-chat deploy/helm/platform \
  --namespace rocket-chat \
  --create-namespace \
  --set sandbox.storageClass="gp3" \
  --set global.domain="rocket.internal.company.com"
```

## Key Architectural Notes

1. **Slack Socket Mode:** No public ingress or webhook URL is required for Slack. The backend connects outbound to Slack using WebSocket Socket Mode.
2. **Dedicated Sandbox Namespace:** Agent execution sandboxes are completely isolated in the `agent-sandboxes` namespace with ResourceQuotas and NetworkPolicies (preventing cloud metadata access).
3. **Storage Configuration:** Sandboxes use persistent volume claims backed by `sandbox.storageClass`.

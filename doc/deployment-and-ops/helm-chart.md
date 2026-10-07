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
  --version 0.1.5 \
  --namespace rocket-chat \
  --create-namespace \
  -f prod-values.yaml
```

---

## 3. External Kubernetes Secrets & Production Hardening

In production environments, credentials should not be stored in plaintext inside `values.yaml`. Rocket Chat natively supports referencing existing Kubernetes Secrets (e.g. provisioned via **External Secrets Operator**, **Sealed Secrets**, **Vault CSI Provider**, or **AWS/GCP Secrets Manager**).

When an `existingSecret` is configured, Rocket Chat bypasses internal Secret creation and mounts credentials directly into the backend Pod using Kubernetes `secretKeyRef`.

### Database Credentials
```yaml
backend:
  database:
    existingSecret: "my-db-credentials"
    existingSecretKey: "DATABASE_URL" # Defaults to DATABASE_URL if omitted
    # Optional dedicated migrations user:
    # adminExistingSecret: "my-db-credentials"
    # adminExistingSecretKey: "ADMIN_DATABASE_URL"
```

If using the bundled PostgreSQL subchart for staging/testing with an external secret:
```yaml
postgresql:
  existingSecret: "my-pg-secret"
  existingSecretKey: "postgres-password"
```

### Encryption Master Key
```yaml
backend:
  encryption:
    existingSecret: "my-vault-secret"
    existingSecretKey: "ENCRYPTION_MASTER_KEY" # Defaults to ENCRYPTION_MASTER_KEY
```

### AI Model Provider API Keys
Configure provider keys using unified `existingSecret` / `existingSecretKey` parameters:
```yaml
backend:
  models:
    openrouter:
      existingSecret: "external-ai-keys"
      existingSecretKey: "OPENROUTER_API_KEY"
    anthropic:
      existingSecret: "external-ai-keys"
      existingSecretKey: "ANTHROPIC_API_KEY"
    openai:
      existingSecret: "external-ai-keys"
      existingSecretKey: "OPENAI_API_KEY"
    deepseek:
      existingSecret: "external-ai-keys"
      existingSecretKey: "DEEPSEEK_API_KEY"
    gemini:
      existingSecret: "external-ai-keys"
      existingSecretKey: "GEMINI_API_KEY"
```

### GitHub App & Slack Integrations
Depending on your setup, you can configure GitHub App automation, a personal GitHub Token (PAT), and/or Slack Socket Mode. All keys are optional:

```yaml
# If using GitHub App automation or live PR creation:
github:
  existingSecret: "github-credentials"
  # Set only the keys you use:
  appIdKey: "GITHUB_APP_ID"                     # Optional: GitHub App ID
  privateKeyKey: "GITHUB_APP_PRIVATE_KEY"       # Optional: RSA private key PEM
  webhookSecretKey: "GITHUB_WEBHOOK_SECRET"     # Optional: Webhook HMAC secret
  tokenKey: "GITHUB_TOKEN"                     # Optional: Personal Access Token (PAT)

backend:
  slack:
    existingSecret: "slack-credentials"
    botTokenKey: "SLACK_BOT_TOKEN"
    appTokenKey: "SLACK_APP_TOKEN"
```

### Authentication & Single Sign-On (NextAuth, Google & OIDC)
Rocket Chat supports NextAuth, Google Workspace, GitHub OAuth, and OIDC JWT validation out of the box. You can configure identity providers via Helm values:
```yaml
auth:
  enabled: true
  nextauthUrl: "https://rocket.company.com" # Optional: defaults to https://{{ .Values.global.domain }} if TLS enabled, else http://
  # Either provide a plaintext secret or load it from an external Kubernetes Secret (Vault, External Secrets Operator, etc.):
  existingSecret: "corporate-auth-secrets" # Kubernetes Secret name
  existingSecretNextAuthKey: "NEXTAUTH_SECRET" # Secret key (defaults to NEXTAUTH_SECRET)
  # nextauthSecret: "strong-random-32-chars" # Alternative: plaintext secret if not using existingSecret
  # Optional: Corporate OIDC (Keycloak, Okta, Azure AD)
  oidc:
    issuer: "https://auth.company.com"
    jwksUrl: "https://auth.company.com/.well-known/jwks.json"
    audience: "rocket-chat"
    clientId: "my-oidc-client-id"
    existingSecret: "corporate-auth-secrets"
    existingSecretClientSecretKey: "OIDC_CLIENT_SECRET"
  # Optional: Google Workspace SSO with Domain Restriction
  google:
    clientId: "12345-xxx.apps.googleusercontent.com"
    allowedDomain: "company.com" # Pre-filters account chooser to your workspace domain
    existingSecret: "corporate-auth-secrets"
    existingSecretClientSecretKey: "GOOGLE_CLIENT_SECRET"
  # Optional: GitHub OAuth
  github:
    clientId: "gh-client-id"
    existingSecret: "corporate-auth-secrets"
    existingSecretClientSecretKey: "GITHUB_CLIENT_SECRET"
```

### PostgreSQL Persistence
When using the built-in PostgreSQL component (`postgresql.enabled: true`), it is deployed as a Kubernetes `StatefulSet` backed by a persistent volume (`volumeClaimTemplate`) rather than ephemeral storage:
```yaml
postgresql:
  enabled: true
  persistence:
    enabled: true
    storageClass: "gp3" # or your cluster default
    size: "10Gi"
```

### Injecting Custom Extra Secrets or Environment Variables
```yaml
backend:
  extraSecretRefs:
    - name: "custom-company-secrets"
  extraEnv:
    - name: "CUSTOM_LOG_LEVEL"
      value: "DEBUG"
```

---

## 4. Operational & Security Architecture

1. **Direct Ingress Routing:** Ingress routes `/api` and `/v1` directly to backend service port 8000, and WebSocket `/ws` with upgraded connection headers, while all frontend UI routes target Next.js.
2. **Slack Socket Mode:** The Helm chart creates zero Ingress rules or public ports for Slack. Slack Bolt connects outbound via WebSockets.
3. **Dedicated Sandbox Namespace:** Sandboxes live in `agent-sandboxes`, completely isolated from the control plane namespace (`rocket-chat`).
4. **Fine-Grained RBAC:** The backend `ServiceAccount` possesses RBAC permissions to create, delete, and exec pods strictly within `agent-sandboxes`. It has zero permissions in any other namespace.

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
│   ├── mcp-runner/             # Dedicated pod for command MCP servers (optional, on by default)
│   │   ├── deployment.yaml     # Non-root, read-only root filesystem, caps dropped
│   │   ├── service.yaml        # ClusterIP on port 8100
│   │   ├── pvc.yaml            # Package-cache volume
│   │   ├── secret.yaml         # Shared token (generated once, kept across upgrades)
│   │   └── networkpolicy.yaml  # Ingress from the backend only; internet egress, no private networks
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
    threadContextLimit: 20      # Recent thread messages auto-injected into the agent prompt
    threadContextMaxChars: 6000 # Character budget for the injected transcript
    channelHistoryLimit: 50     # Max messages for the slack_read_channel_history tool

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
  --version 0.2.2 \
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

### Scaling out

The backend scales horizontally. With `backend.eventBus: auto` (the default) the chart turns on the
PostgreSQL-backed event bus whenever autoscaling is enabled or `backend.replicas > 1`: all replicas
number a session's events from one shared counter, deliver them to each other's WebSocket clients
and forward approvals, answers and cancels to the replica running the turn (a decision nobody
holds is still reported as "not found"). Delivery is at-most-once, like the single-process bus;
a client that misses events resumes by sequence number or refetches the session snapshot.
`ClientIP` session affinity stays on as a safety net for the few caches that are still per pod.

### Database Migrations

Alembic is the only thing that changes the schema. Each backend pod runs an `migrate` init container
(`/app/deploy/docker/migrate.sh`) as the schema owner (`ADMIN_DATABASE_URL`) before the API
container starts; concurrent replicas queue on a PostgreSQL advisory lock, so a three-replica
rollout applies each revision exactly once. The API itself only *verifies* the database is at the
Alembic head it was built for and **refuses to start** (`ENVIRONMENT=production`) otherwise - it
never creates tables or RLS policies at runtime, and it never silently falls back to in-memory
storage in production.

```yaml
backend:
  migrations:
    enabled: true   # set to false only if your pipeline runs `alembic upgrade head` itself
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
Depending on your setup, you can configure GitHub App automation and/or Slack Socket Mode. All keys are optional. There is no deployment-wide GitHub token: organizations use their mapped App installation, members their own linked account.

```yaml
# If using GitHub App automation or live PR creation:
github:
  existingSecret: "github-credentials"
  # Set only the keys you use:
  appIdKey: "GITHUB_APP_ID"                     # Optional: GitHub App ID
  privateKeyKey: "GITHUB_APP_PRIVATE_KEY"       # Optional: RSA private key PEM
  webhookSecretKey: "GITHUB_WEBHOOK_SECRET"     # Optional: Webhook HMAC secret

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
  # Initial administrator bootstrapping:
  initialAdminEmail: "admin@company.com" # First-time setup administrator email
  adminEmails: "lead@company.com, ops@company.com" # Comma-separated list of additional administrators
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

### Sandbox Inactivity Reconciliation & Storage
```yaml
sandbox:
  namespace: "agent-sandboxes"
  storageClass: "gp3" # Cloud CSI storage class: gp3 (AWS), managed-csi (Azure), pd-balanced (GCP)
  inactivity:
    pauseSeconds: 1800 # 30 minutes idle -> hibernate sandbox pod
    cleanupSeconds: 0 # 0 = never purge on a timer; >0 purges workspaces of deleted sessions only
  runtimeClassName: "" # e.g. gvisor / kata-qemu
  quota:
    enabled: true
    pods: "50"
    requestsStorage: "500Gi"
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

### Probes, Draining, Autoscaling & Session Affinity
Probes for both tiers are configurable through `backend.probes.*` / `frontend.probes.*`. The backend readiness probe targets `/v1/ready` (PostgreSQL ping + code-index stats); liveness stays on the static `/health`:

```yaml
backend:
  # Spec R1: in-process session state. Ship one replica; ClientIP affinity pins each
  # client's WebSocket + REST traffic to that pod. Enable scale-out only with the
  # shared event bus post-ship item, or accept single-client coherence via affinity.
  replicas: 1
  service:
    sessionAffinity: ClientIP
    sessionAffinityTimeoutSeconds: 3600
  probes:
    readiness:
      path: /v1/ready
  terminationGracePeriodSeconds: 60        # drain grace period
  autoscaling:
    enabled: true
    minReplicas: 1
    maxReplicas: 4
    targetCPUUtilizationPercentage: 70
  podDisruptionBudget:
    enabled: true
    minAvailable: 1
  # Sized against the M2 code-index math: ~9 MB/session, 192 MiB process ceiling.
  resources:
    limits:
      memory: 2Gi
    requests:
      memory: 1Gi
```

Rollouts use `maxUnavailable: 0` / `maxSurge: 1`, and the `preStop` hook calls `POST /v1/drain` so `/v1/ready` fails before SIGTERM and the pod leaves Service endpoints while in-flight turns complete.

Operational environment variables (rendered into the ConfigMap and consumed by the control plane):

| Variable | Default | Purpose |
| :--- | :--- | :--- |
| `ROCKET_GLOBAL_CONCURRENCY_LIMIT` | `16` | Process-wide concurrent-turn ceiling |
| `ROCKET_SESSION_LOCK_CAPACITY` | `256` | Bound on the bounded per-session single-writer lock registry |
| `ROCKET_DB_PING_TIMEOUT_SECONDS` | `2` | Deadline for the `/v1/ready` PostgreSQL ping |
| `ROCKET_TOOL_MAX_CONCURRENCY` | `8` | Concurrent tool calls per session |
| `ROCKET_TOOL_PROCESS_CONCURRENCY` | `64` | Concurrent tool calls per API process (all sessions) |
| `ROCKET_TOOL_TIMEOUT_SECONDS` | `600` | Per-tool working-time budget (waits for a person excluded) |

---

## 4. Operational & Security Architecture

1. **Direct Ingress Routing:** Ingress routes `/api` and `/v1` directly to backend service port 8000, and WebSocket `/ws` with upgraded connection headers, while all frontend UI routes target Next.js.
2. **Slack Socket Mode:** The Helm chart creates zero Ingress rules or public ports for Slack. Slack Bolt connects outbound via WebSockets.
3. **Dedicated Sandbox Namespace:** Sandboxes live in `agent-sandboxes`, completely isolated from the control plane namespace (`rocket-chat`).
4. **Fine-Grained RBAC:** The backend `ServiceAccount` possesses RBAC permissions to create, delete, and exec pods strictly within `agent-sandboxes`. It has zero permissions in any other namespace. The role was audited against the verbs the driver actually calls: pods (`get`, `list`, `create`, `delete`, `patch`), persistent volume claims (`get`, `list`, `create`, `delete`), per-sandbox NetworkPolicies (`get`, `create`, `update`, `delete`) and `pods/exec`; there is no `watch` and no access to secrets or other kinds. The chart was installed on a throwaway k3d cluster to check the contract: migrations reach head, the backend becomes Ready with the database connected, unauthenticated calls return 401, and a sandbox lifecycle (create, exec, file I/O, PTY, restricted egress, destroy) runs under exactly this role. Sandbox isolation relies on non-root, dropped capabilities, seccomp and the NetworkPolicy; a read-only root filesystem is not applied because agents install toolchains, and the per-container process limit is the kubelet's `podPidsLimit`, which your cluster should set (Docker sandboxes set it per container). Allowlisted host names are resolved to addresses when the sandbox starts. Beyond the namespace-wide `ResourceQuota`, `sandboxes.max_running_sandboxes` caps one organization so it cannot use up the shared capacity.
5. **No ServiceAccount Mounts in Sandboxes:** All sandbox Pods run with `automountServiceAccountToken: false` to guarantee untrusted code executed in `/workspace` cannot authenticate to Kubernetes APIs.
6. **Container Privilege Hardening & Capability Dropping:** Sandbox containers enforce `allowPrivilegeEscalation: false`, drop dangerous capabilities (`NET_RAW`, `SYS_ADMIN`, `SYS_PTRACE`, `DAC_READ_SEARCH`), and enforce Seccomp profile `RuntimeDefault`.
7. **SSRF Mitigation & Internal Cluster Whitelisting (`NetworkPolicy`):**
   - Restricts egress from `agent-sandboxes`, blocking the cloud instance metadata endpoint (`169.254.169.254/32`).
   - Supports strict isolation of private networks (`blockInternalSubnets: true`) to prevent sandbox lateral movement into cluster workloads.
   - Allows explicit egress whitelisting (`allowedEgressRules`) for internal package registries (e.g., in-cluster Artifactory, internal PyPI/npm mirrors, or CoreDNS).

## MCP runner values

Command (stdio) MCP servers run in a dedicated pod, started per turn and stopped with the turn. Set `mcpRunner.enabled=false` to omit it entirely (nothing of it is rendered; remote MCP servers are unaffected and command servers show as unavailable in the app).

| Value | Default | Meaning |
| :--- | :--- | :--- |
| `mcpRunner.enabled` | `true` | Deploy the runner and point the backend at it (`MCP_RUNNER_URL`, `MCP_RUNNER_TOKEN`) |
| `mcpRunner.image.repository` / `.tag` | `ghcr.io/rocket-chat/mcp-runner` / chart version | Runner image; mirror it for private or air-gapped clusters |
| `mcpRunner.token` | `""` | Shared secret; empty generates one and keeps it across upgrades (needs permission to read Secrets at render time, as Helm normally has) |
| `mcpRunner.existingSecret` / `.existingSecretKey` | `""` / `MCP_RUNNER_TOKEN` | Use a Secret you manage instead |
| `mcpRunner.maxProcesses` / `.maxProcessesPerOrg` | `32` / `8` | Servers running at once, in total and for one organization |
| `mcpRunner.maxLifetimeSeconds` | `1800` | A server is stopped after this long |
| `mcpRunner.persistence.enabled` / `.size` / `.storageClass` / `.existingClaim` | `true` / `5Gi` / cluster default / `""` | Volume for the package caches and installed server programs; without it they live on an emptyDir and are fetched again after a restart |
| `mcpRunner.resources` | 100m / 256Mi, limit 2Gi memory | Sizing for the runner pod (each running server uses memory from it) |
| `mcpRunner.networkPolicy.enabled` | `true` | Ingress only from the backend; egress to DNS and the internet |
| `mcpRunner.networkPolicy.blockInternalSubnets` | `true` | Also forbid RFC 1918 ranges and the cloud metadata address |
| `mcpRunner.networkPolicy.allowedEgressRules` | `[]` | Extra egress rules, e.g. an in-cluster service a server must reach |

The runner holds only the secret it shares with the backend: no database credentials, no master key and no Kubernetes access (`automountServiceAccountToken: false`).

## Observability values

| Value | Default | Meaning |
| :--- | :--- | :--- |
| `backend.logFormat` | `console` | `json` emits one correlated JSON object per line (`request_id`, `org_id`, `user_id`, `session_id`, `trace_id`) |
| `observability.otlpEndpoint` | `""` | OTLP/HTTP collector URL; empty disables tracing |
| `observability.serviceName` | `rocket-chat-api` | `service.name` resource attribute |

## Agent web access values

| Value | Default | Meaning |
| :--- | :--- | :--- |
| `search.provider` | `duckduckgo` | `web_search` backend: `duckduckgo`, `searxng` or `brave` |
| `search.searxngUrl` | `""` | Your SearXNG base URL (enable its `json` format) |
| `search.braveApiKey` | `""` | Brave Search API key (stored in the chart secret) |
| `search.fetchProxy` | `""` | Egress proxy for `web_fetch` (hops are still vetted) |
| `backend.env.ROCKET_KB_MAX_DOCUMENT_CHARS` | `2000000` | Largest knowledge-base document |
| `backend.env.ROCKET_KB_MAX_CHUNKS_PER_DOCUMENT` | `2000` | Most chunks one document may produce |
| `global.airGapped` | `false` | Sets `LITELLM_LOCAL_MODEL_COST_MAP` so nothing is fetched from the internet at startup (see [air-gapped installs](/install/air-gapped)) |
| `sandbox.defaultImage` | `""` | Image new sandboxes start from (sets `ROCKET_DEFAULT_SANDBOX_IMAGE`); empty uses the published devbox `ghcr.io/rocket-chat/sandbox:latest` with git, Python, Node, uv, gh and ripgrep. Point it at a mirror for private or air-gapped clusters |
| `sandbox.imagePullSecrets` | `[]` | Image-pull Secrets (in the sandbox namespace) for a private registry |


## Public URL

The backend receives `ROCKET_PUBLIC_URL`, derived the same way as the web app's `NEXTAUTH_URL` (`auth.nextauthUrl`, else `https://<global.domain>` when ingress TLS is on, else `http://<global.domain>`). Slack uses it for the "Open in Rocket Chat" link on every thread; an organization can override it with the `slack.web_base_url` setting.

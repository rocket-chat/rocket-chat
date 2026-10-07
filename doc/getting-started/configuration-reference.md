# Master Configuration Reference

This document provides a comprehensive catalog of all environment variables, policy settings, and runtime flags available in Rocket Chat across the Control Plane, Sandbox Drivers, Security, and Integrations.

---

## 1. Environment Variables Catalog

All environment variables can be provided via shell environment, `.env` file, Docker Compose `environment` blocks, or Kubernetes `ConfigMap` / `Secret` manifests.

### Core Control Plane & LLM Routing

| Environment Variable | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `DEFAULT_SANDBOX_DRIVER` | string | `docker` | Active sandbox driver: `docker` for local container runtimes, `k8s` for Kubernetes clusters. |
| `ROCKET_DEFAULT_MODEL` | string | `openrouter/deepseek/deepseek-v4.1-flash` | Default model identifier passed to LiteLLM for sessions that do not specify a model. |
| `ENCRYPTION_MASTER_KEY` | string | *(Required)* | 32-character hex key (256-bit) used by `CredentialCipher` to encrypt BYOK credentials at rest via AES-256-GCM. |
| `DATABASE_URL` | string | *(Required)* | Async PostgreSQL connection string for application runtime (e.g. `postgresql+asyncpg://rocket_app:pass@host:5432/rocket_chat`). Enforces Row-Level Security. |
| `ADMIN_DATABASE_URL` | string | `None` | Superuser PostgreSQL connection string used exclusively by Alembic migrations to manage DDL and RLS policies. |

### Authentication & Single Sign-On (OIDC)

| Environment Variable | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `OIDC_JWKS_URL` | string | `None` | URL to the identity provider's JSON Web Key Set (JWKS) endpoint (e.g. `https://auth.company.com/.well-known/jwks.json`). |
| `OIDC_PUBLIC_KEY` | string | `None` | Static PEM-formatted RSA public key for offline RS256 JWT signature verification. |
| `OIDC_ISSUER` | string | `None` | Expected JWT issuer (`iss` claim) to validate during token decoding. |
| `OIDC_AUDIENCE` | string | `None` | Expected JWT audience (`aud` claim). |
| `DEV_AUTH_BYPASS` | bool | `false` | When set to `true`, disables OIDC JWT validation for local offline development. Injects mock tenant claims. |
| `DEV_USER_ID` | string | `dev-user-001` | Mock user ID injected when `DEV_AUTH_BYPASS=true`. |
| `DEV_ORG_ID` | string | `dev-org-alpha` | Mock tenant organization ID injected when `DEV_AUTH_BYPASS=true`. |

### Kubernetes Sandbox Driver (`K8sSandboxDriver`)

| Environment Variable | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `K8S_SANDBOX_NAMESPACE` | string | `agent-sandboxes` | Dedicated Kubernetes namespace where ephemeral sandbox Pods and PVCs are provisioned. |
| `K8S_DEFAULT_IMAGE` | string | `python:3.12-slim` | Default container image used for agent sandbox pods. |
| `K8S_STORAGE_CLASS` | string | Auto-detected | Explicit StorageClass name for PVC dynamic provisioning (e.g. `gp3`, `managed-csi`, `pd-balanced`, `local-path`). |
| `K8S_CLOUD_PROVIDER` | string | `None` | Cloud provider hint (`aws`, `azure`, `gcp`). Automatically configures default cloud storage class if `K8S_STORAGE_CLASS` is omitted. |
| `SANDBOX_INACTIVITY_PAUSE_SECONDS` | int | `300` (5 min) | Time of inactivity before a sandbox pod is hibernated (scale-to-zero) to free CPU and RAM while preserving the bound PVC. |
| `SANDBOX_INACTIVITY_CLEANUP_SECONDS` | int | `3600` (1 hr) | Time of inactivity before an entire sandbox (Pod and PVC) is permanently torn down. |
| `K8S_WARMED_POOL_SIZE` | int | `0` | Number of pre-warmed idle Pods to maintain in the sandbox namespace for instant turn startup. |
| `K8S_WARMED_IMAGE` | string | Same as default | Container image used for pre-warmed pool pods. |
| `K8S_IMAGE_PULL_SECRETS` | string | `None` | Comma-delimited list of Kubernetes Secret names for pulling sandbox images from private container registries. |

### Docker Sandbox Driver (`DockerSandboxDriver`)

| Environment Variable | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `SANDBOX_INACTIVITY_PAUSE_SECONDS` | int | `300` | Inactivity duration before stopping the Docker container while preserving its named volume. |
| `SANDBOX_INACTIVITY_CLEANUP_SECONDS` | int | `3600` | Inactivity duration before pruning the container and deleting the session Docker volume. |

### Slack Assistant (Socket Mode)

| Environment Variable | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `SLACK_BOT_TOKEN` | string | `""` | Bot user OAuth token (`xoxb-...`) with scopes to reply in threads and send messages. |
| `SLACK_APP_TOKEN` | string | `""` | App-level token (`xapp-...`) with `connections:write` scope for WebSocket Socket Mode. |

### Git Engine & GitHub App

| Environment Variable | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `GITHUB_APP_ID` | string | `None` | GitHub App ID used for authenticating API calls and generating installation tokens. |
| `GITHUB_APP_PRIVATE_KEY` | string | `None` | RSA private key PEM for the GitHub App. |
| `GITHUB_WEBHOOK_SECRET` | string | `None` | HMAC SHA-256 secret configured in GitHub App settings to verify webhook payloads. |
| `GITHUB_TRIGGER_LABEL` | string | `ai-fix` | Issue label that automatically triggers the autonomous bug-fixing agent workflow. |
| `GIT_BOT_NAME` | string | `RocketChat Bot` | Author name injected into Git commits. |
| `GIT_BOT_EMAIL` | string | `bot@rocketchat.internal` | Author email injected into Git commits. |
| `GIT_DEFAULT_BRANCH` | string | `main` | Default target branch for repository checkouts. |

---

## 2. Policy Schema Reference (Hierarchical Config)

Rocket Chat provides four policy tiers: **System Defaults**, **OrgPolicy**, **TeamPolicy**, and **UserOverride**.

### Organization Policy (`OrgPolicy`)

Governs enterprise compliance and hard constraints:

```python
@dataclass
class OrgPolicy:
    org_id: str
    allowed_models: list[str] = field(default_factory=list)      # Whitelisted models (e.g. ["anthropic/*", "openrouter/*"])
    forbidden_models: list[str] = field(default_factory=list)    # Blacklisted models (e.g. ["openai/*"])
    default_model: str | None = None                             # Default model enforced for organization users
    allowed_images: list[str] = field(default_factory=list)      # Whitelisted sandbox container images
    default_image: str | None = None                             # Default sandbox image
    max_memory: str | None = None                                # Maximum RAM limit per sandbox (e.g. "4Gi")
    max_cpus: float | None = None                                # Maximum CPU cores limit per sandbox (e.g. 2.0)
    idle_timeout_minutes: int | None = None                      # Organization-wide maximum idle timeout
    require_signed_commits: bool = False                         # Enforces GPG or GitHub App signed commits
    byok_provider: str | None = None                             # Organization BYOK provider identifier
    byok_api_key_encrypted: str | None = None                   # AES-256-GCM encrypted API key
    custom_env_vars: dict[str, str] = field(default_factory=dict)# Environment variables injected into all org sandboxes
```

### Team Policy (`TeamPolicy`)

Departmental conventions within organization bounds:

```python
@dataclass
class TeamPolicy:
    team_id: str
    org_id: str
    default_model: str | None = None                             # Team preferred model (must satisfy OrgPolicy)
    default_image: str | None = None                             # Team sandbox image
    default_repo: str | None = None                              # Default repository URL
    slack_channel: str | None = None                             # Default Slack notification channel
    byok_provider: str | None = None                             # Team BYOK provider
    byok_api_key_encrypted: str | None = None                   # Team encrypted API key
    custom_env_vars: dict[str, str] = field(default_factory=dict)# Environment variables injected into team sandboxes
```

### User Override (`UserOverride`)

Developer preferences:

```python
@dataclass
class UserOverride:
    user_id: str
    org_id: str
    team_id: str | None = None
    preferred_model: str | None = None                           # Individual model preference
    preferred_image: str | None = None                           # Individual sandbox image preference
    byok_provider: str | None = None                             # Individual personal BYOK key
    byok_api_key_encrypted: str | None = None                   # Individual encrypted key
    git_author_name: str | None = None                           # Human developer name for Co-authored-by
    git_author_email: str | None = None                          # Human developer email for Co-authored-by
    custom_env_vars: dict[str, str] = field(default_factory=dict)# Personal environment variables
```

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
| `LOG_LEVEL` | string | `INFO` | Configurable backend log verbosity: `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`. |
| `LOG_FORMAT` | string | `console` | Logging output structure: `console` (colored human-readable) or `json` (structured machine-readable for Datadog, Loki, CloudWatch). |
| `AGENT_MAX_TURNS` | int | `60` | Default autonomous ReAct turn limit per user prompt before triggering post-failure synthesis. |
| `ROCKET_FALLBACK_MODELS` | string | `""` | Comma-separated ordered fallback models used on transient provider failures. Handled natively by LiteLLM (`fallbacks`). E.g. `openrouter/openai/gpt-4o,openrouter/google/gemini-2.5-pro`. |
| `ROCKET_MODEL_MAX_RETRIES` | int | `2` | Retry attempts for transient provider errors (rate limit, timeout, 5xx), delegated to LiteLLM (`num_retries`). |
| `ROCKET_CODE_INDEX_MAX_SESSIONS` | int | `16` | Maximum number of per-session semantic code indexes kept in process memory (LRU). The index is derived data, so eviction is always safe. |
| `ROCKET_CODE_INDEX_MAX_BYTES` | int | `201326592` (192 MiB) | Total byte ceiling for all cached code indexes. A full session is ~9 MB (1500 chunks × 1536 float32 vectors); keep this well below `backend.resources.limits.memory`. |
| `ROCKET_CODE_INDEX_TTL_SECONDS` | int | `900` | Age after which a cached code index is rebuilt on next use. |
| `ROCKET_CODE_INDEX_MAX_FILES` | int | `300` | Maximum workspace files scanned when building a code index. |
| `ROCKET_CODE_INDEX_MAX_CHUNKS` | int | `1500` | Maximum chunks stored per code index build. |
| `ROCKET_PROMPT_CACHE` | bool | `true` | Emit provider prompt-cache breakpoints on the stable system-prompt prefix and tool schemas (Anthropic-compatible models). |
| `ROCKET_ATTACHMENTS_DIR` | path | `/tmp/rocket_chat_attachments` | Host directory for content-addressed attachment blobs. Mount a persistent volume here in production so uploads survive a pod restart. |
| `ROCKET_ATTACHMENT_MAX_BYTES` | int | `26214400` (25 MiB) | Per-file upload cap enforced server-side for web and Slack attachments. |
| `ROCKET_ATTACHMENT_INLINE_BYTES` | int | `65536` (64 KiB) | Byte budget for inlining a text attachment's content into the model prompt; longer files are truncated with a marker. |
| `ROCKET_ATTACHMENT_SIGNING_KEY` | secret | *(ephemeral)* | HMAC key for signed attachment retrieval. When unset, an ephemeral per-process key is generated (tokens do not survive a restart or span replicas). |
| `ROCKET_MEMORY_SUMMARY_MODEL` | string | `""` | Optional pinned model for the per-session memory summary written on turn completion; defaults to the turn's effective model. |
| `ROCKET_TOOL_MAX_CONCURRENCY` | int | `8` | How many tool calls of **one session** run at once; extra calls of that session queue. |
| `ROCKET_TOOL_PROCESS_CONCURRENCY` | int | `64` | How many tool calls run at once in one API process, all sessions together. A call waiting for a person (a question or an approval) holds neither limit. |
| `ROCKET_WEB_FETCH_ALLOW_PRIVATE` | bool | `false` | Lets the agent's `web_fetch` reach private, loopback and link-local addresses. Off by default so a prompt-injected agent cannot read cloud metadata or internal services; enable only for trusted single-tenant installs that need internal docs. |
| `ROCKET_TOOL_TIMEOUT_SECONDS` | int | `600` | Per-tool working-time budget; an over-budget tool is recorded with `status="timeout"` rather than failing the whole batch. Time spent waiting for a person (a question or an approval) does not count: those wait as long as it takes. |
| `ROCKET_GLOBAL_CONCURRENCY_LIMIT` | int | `16` | Process-wide maximum number of concurrent turns. |
| `ROCKET_SESSION_LOCK_CAPACITY` | int | `256` | Maximum number of per-session single-writer locks retained (bounded registry). |
| `ROCKET_DB_PING_TIMEOUT_SECONDS` | int | `2` | Deadline for the `/v1/ready` PostgreSQL ping. |
| `ROCKET_DOCKER_THREADS` | int | `64` | Size of the Docker driver's private thread pool for the blocking Docker SDK (a long command holds a thread for its whole run). When full, further Docker calls queue; the rest of the API is unaffected. Watch `rocket_docker_blocking_calls`. |
| `ROCKET_TURN_AUTO_RESUME_ATTEMPTS` | int | `1` | How many times a task is resumed automatically after a restart interrupted it (`0` disables). Organizations can also opt out with `governance.auto_resume_turns`. |
| `ROCKET_SEARCH_PROVIDER` | enum | `duckduckgo` | `web_search` backend: `duckduckgo` (zero-config HTML fallback, fragile), `searxng` (your own instance; set `ROCKET_SEARXNG_URL`, enable the `json` format) or `brave` (set `ROCKET_BRAVE_API_KEY`). A misconfigured provider is reported to the model, never silently replaced. |
| `ROCKET_SEARXNG_URL` | url | – | Base URL of the SearXNG instance (operator-configured, so a private address is fine). |
| `ROCKET_BRAVE_API_KEY` | secret | – | Brave Search API key, sent as a header only. |
| `ROCKET_WEB_FETCH_PROXY` | url | – | Egress proxy for `web_fetch`. Every hop is still vetted for private/link-local targets, but the address cannot be pinned, so the proxy should enforce its own allowlist. |
| `ROCKET_KB_MAX_DOCUMENT_CHARS` | int | `2000000` | Largest knowledge-base document accepted (`413` above it). |
| `ROCKET_KB_MAX_CHUNKS_PER_DOCUMENT` | int | `2000` | Most chunks one document may be split into and embedded (`413` above it). |
| `ROCKET_API_RATE_LIMIT_PER_USER` | int | `60` | Per-user API turn rate limit (per minute) on the authenticated path. Counted **per replica**: with N replicas the effective ceiling is up to N times this value. |
| `ROCKET_API_RATE_LIMIT_PER_ORG` | int | `300` | Per-organisation API turn rate limit (per minute) on the authenticated path. Counted per replica, like the per-user limit. |
| `ENCRYPTION_MASTER_KEY` | string | *(Required)* | 32-character hex key (256-bit) used by `CredentialCipher` to encrypt BYOK credentials at rest via AES-256-GCM. |
| `CREDENTIALS_ENCRYPTION_KEY` | secret | *(dev fallback)* | Fernet key protecting user OAuth/PAT tokens at rest (`services/credentials.py`): a base64 32-byte key, or any string (SHA-256-derived). **Required in production**; when unset a deterministic development key is used. Rotating it requires re-encrypting stored tokens. |
| `DATABASE_URL` | string | *(Required)* | Async PostgreSQL connection string for application runtime (e.g. `postgresql+asyncpg://rocket_app:pass@host:5432/rocket_chat`). Enforces Row-Level Security. |
| `ADMIN_DATABASE_URL` | string | `None` | Schema-owner PostgreSQL connection string used by Alembic migrations (DDL, RLS policies) and by the cross-tenant maintenance loops (interrupted-turn recovery, retention, MCP refresh) that row-level security would otherwise hide. |
| `ROCKET_EVENT_BUS` | enum | `memory` | `memory` keeps events in-process (one replica). `postgres` shares events, per-session sequence numbers and approval/answer/cancel decisions between replicas over PostgreSQL `LISTEN`/`NOTIFY`, so a WebSocket on one replica follows a turn running on another. The Helm chart selects it automatically when more than one replica can run. |
| `ROCKET_RUN_MIGRATIONS` | bool | `true` | The container entrypoint applies Alembic migrations before starting. The Helm chart sets `false` and migrates in an init container instead. |
| `ROCKET_AUTO_CREATE_SCHEMA` | bool | `false` | Development/test only: create tables with `create_all` at startup instead of requiring Alembic. Ignored when `ENVIRONMENT=production`. |

### Authentication & Single Sign-On (OIDC / OAuth)

| Environment Variable | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `NEXTAUTH_URL` | string | `http://localhost:3000` | Canonical public URL of the Rocket Chat web application. |
| `NEXTAUTH_SECRET` | string | *(Required in prod)* | Cryptographic session signing key for NextAuth.js JWT session cookies. |
| `LOCAL_AUTH_ENABLED` | bool | `false` | Enables email + password sign-in with built-in accounts (argon2id, lock-out after 5 failures for 15 minutes). Needs PostgreSQL. See [SSO & sign-in setup](/administer/sso). |
| `INITIAL_ADMIN_EMAIL` / `INITIAL_ADMIN_PASSWORD` | string | `None` | With local accounts, the first administrator is created from these on start (never modifies an existing account). `INITIAL_ADMIN_EMAIL` alone also promotes an SSO login with that address to administrator. |
| `OIDC_ISSUER` | string | `None` | The provider's issuer URL. Enterprise login and bearer-token validation find every endpoint and the signing keys through `<issuer>/.well-known/openid-configuration` (Okta, Azure AD / Entra, Google, Keycloak, Authentik, ...), and tokens must carry this `iss`. |
| `OIDC_SCOPES` | string | `openid email profile` | Scopes requested at login. |
| `OIDC_DISCOVERY_URL` | string | `<issuer>/.well-known/openid-configuration` | Override when the provider publishes its discovery document elsewhere. The document must still name `OIDC_ISSUER` as its issuer. |
| `OIDC_JWKS_URL` | string | discovered | JWKS endpoint for bearer-token validation. Only needed for providers without OIDC discovery; otherwise the `jwks_uri` from discovery is used. |
| `OIDC_CLIENT_ID` | string | `None` | OAuth client ID for enterprise OpenID Connect. |
| `OIDC_CLIENT_SECRET` | string | `None` | OAuth client secret for enterprise OpenID Connect. |
| `GITHUB_CLIENT_ID` | string | `None` | The GitHub App's **Client ID**, for linking members' GitHub accounts (with `GITHUB_CLIENT_SECRET`, a client secret generated on the App's page). The App's Callback URL is `https://<your Rocket Chat>/api/auth/github/callback`. |
| `GITHUB_CLIENT_SECRET` | string | `None` | GitHub OAuth client secret for authentication. |
| `GOOGLE_CLIENT_ID` | string | `None` | Google OAuth client ID for corporate Google Workspace login. |
| `GOOGLE_CLIENT_SECRET` | string | `None` | Google OAuth client secret. |
| `GOOGLE_ALLOWED_DOMAIN` | string | `None` | Optional domain restriction for Google logins (e.g. `company.com`). |
| `OIDC_PUBLIC_KEY` | string | `None` | Static PEM-formatted RSA public key for offline RS256 JWT signature verification. |
| `OIDC_AUDIENCE` | string | `None` | Expected JWT audience (`aud` claim). |
| `ENVIRONMENT` | string | `development` | Set to `production` to fail closed: the API refuses to boot with a default/missing `SESSION_SECRET`/`ENCRYPTION_MASTER_KEY` or with `DEV_AUTH_BYPASS`, `ROCKET_DEV_LOGIN` or `ALLOW_DEV_LOGIN` enabled, and ignores the development identity. The Helm chart sets it to `production`. |
| `SESSION_SECRET` | string | *(Required in prod)* | Signs the `rocket_session` cookie (>= 32 chars). Falls back to `NEXTAUTH_SECRET`. |
| `ENCRYPTION_MASTER_KEY` | string | *(Required in prod)* | Encrypts credentials at rest. After a rotation, list the retired key(s) in `ENCRYPTION_PREVIOUS_KEYS` (comma separated) so old values stay readable. |
| `DEV_AUTH_BYPASS` | bool | `false` | **Local development only.** Gives anonymous requests an admin `dev_user` identity and honours `X-Tenant-*` / `X-User-Role` headers. Ignored (and refused at startup) in production. |
| `ROCKET_DEV_LOGIN` | bool | `false` | Enables the login-page developer form without the anonymous bypass. Roles are server-assigned and the org is fixed unless `DEV_AUTH_BYPASS` is also on. |
| `ROCKET_TRUST_PROXY_HEADERS` | bool | `false` | Trust `X-Tenant-User-Id`, `X-Tenant-Org-Id` and `X-User-Role` as identity. Only enable behind a proxy that strips and re-sets them. |
| `ROCKET_SSO_ORG_MODE` | `single` \| `domain` | `single` | `single` puts every SSO user in `ROCKET_DEFAULT_ORG_ID`; `domain` derives the org from a corporate e-mail domain (public mail domains still map to the default org). |
| `ROCKET_DEFAULT_ORG_ID` | string | `default_org` | Organization for SSO users in `single` mode. |
| `SSO_ALLOWED_DOMAINS` | string | `None` | Comma-separated e-mail domains allowed to sign in with any provider. The org setting `general.sso_allowed_domains` applies in addition when `sso_enforce_domain_match` is on. |
| `GITHUB_WEBHOOK_SECRET` | string | *(Required)* | HMAC secret for `POST /v1/webhooks/github`. Without it every webhook is rejected. |
| `GITHUB_ALLOWED_REPOS` | string | `None` | Comma-separated fnmatch patterns (`acme/*`) of repositories whose events may start sessions. |
| `ROCKET_WEBHOOK_ORG_ID` | string | `default_org` | Organization that owns sessions started by webhooks. |
| `ROCKET_ALLOW_UNSIGNED_WEBHOOKS` | bool | `false` | Development only: accept webhooks when no secret is configured. Ignored in production. |

### Kubernetes Sandbox Driver (`K8sSandboxDriver`)

| Environment Variable | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `K8S_SANDBOX_NAMESPACE` | string | `agent-sandboxes` | Dedicated Kubernetes namespace where ephemeral sandbox Pods and PVCs are provisioned. |
| `K8S_DEFAULT_IMAGE` | string | `python:3.12-slim` | Default container image used for agent sandbox pods. |
| `K8S_STORAGE_CLASS` | string | Auto-detected | Explicit StorageClass name for PVC dynamic provisioning (e.g. `gp3`, `managed-csi`, `pd-balanced`, `local-path`). |
| `K8S_CLOUD_PROVIDER` | string | `None` | Cloud provider hint (`aws`, `azure`, `gcp`). Automatically configures default cloud storage class if `K8S_STORAGE_CLASS` is omitted. |
| `SANDBOX_INACTIVITY_PAUSE_SECONDS` | int | `300` (5 min) | Time of inactivity before a sandbox pod is hibernated (scale-to-zero) to free CPU and RAM while preserving the bound PVC. |
| `SANDBOX_INACTIVITY_CLEANUP_SECONDS` | int | `0` (off) | Idle time after which the Pod and PVC of a hibernated sandbox whose session no longer exists are removed. Workspaces of live sessions are never purged. |
| `K8S_SANDBOX_RUNTIME_CLASS` | string | `None` | RuntimeClass for sandbox pods (e.g. `gvisor`, `kata-qemu`). |
| `K8S_WARMED_POOL_SIZE` | int | `0` | Number of pre-warmed idle Pods to maintain in the sandbox namespace for instant turn startup. |
| `K8S_WARMED_IMAGE` | string | Same as default | Container image used for pre-warmed pool pods. |
| `K8S_IMAGE_PULL_SECRETS` | string | `None` | Comma-delimited list of Kubernetes Secret names for pulling sandbox images from private container registries. |
| `ROCKET_PLATFORM_NAMESPACE` | string | `None` | Namespace of the API pods. With the two below, every restricted sandbox's NetworkPolicy allows the API's git gateway (and nothing else of the platform). Set by the Helm chart. |
| `ROCKET_PLATFORM_POD_LABELS` | string | `None` | `key=value,key=value` labels selecting the API pods. |
| `ROCKET_PLATFORM_POD_PORT` | int | `8000` | The API container's port. |

### Docker Sandbox Driver (`DockerSandboxDriver`)

| Environment Variable | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `SANDBOX_INACTIVITY_PAUSE_SECONDS` | int | `300` | Inactivity duration before stopping the Docker container while preserving its named volume. |
| `SANDBOX_INACTIVITY_CLEANUP_SECONDS` | int | `0` (off) | Idle time after which the container and volume of a session that no longer exists are pruned. |

### MCP runner (command MCP servers)

Command (stdio) MCP servers run in their own pod or container, the `mcp-runner` image. The API reaches it with the first two variables; the runner reads the rest. Without `MCP_RUNNER_URL` command servers are unavailable and remote (HTTP/SSE) servers are unaffected. The Helm chart (`mcpRunner.enabled`, on by default) and both Compose files set all of this for you.

| Environment Variable | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `MCP_RUNNER_URL` | url | *(empty)* | Where the API reaches the runner, for example `http://mcp-runner:8100`. |
| `MCP_RUNNER_TOKEN` | secret | *(none)* | Shared secret (16+ characters) the API presents to the runner; the runner refuses to start without it. Set it on both sides. |
| `MCP_RUNNER_CACHE_DIR` | path | `/cache` | Runner: package caches (npm, uv) and installed server programs. Mount a volume here. |
| `MCP_RUNNER_WORK_DIR` | path | `/tmp/mcp-work` | Runner: per-organization scratch directories. |
| `MCP_RUNNER_MAX_PROCESSES` | int | `32` | Runner: servers running at once. |
| `MCP_RUNNER_MAX_PROCESSES_PER_ORG` | int | `8` | Runner: servers one organization may run at once. |
| `MCP_RUNNER_MAX_LIFETIME_SECONDS` | int | `1800` | Runner: a server is stopped after this long. |

### Slack Assistant (Socket Mode)

> [!NOTE]
> **Slack bots are configured in the cockpit.** Under **Settings → Slack**, administrators add one or more bots, each a Slack app with its own tokens (stored encrypted), agent or router, and response mode. `SLACK_BOT_TOKEN` and `SLACK_APP_TOKEN` are **ignored** since 0.3.1 (start-up logs a warning when they are set); `SLACK_TEAM_ID`, `SLACK_DEFAULT_ORG_ID`, `SLACK_DM_POLICY`, `SLACK_OUTBOUND_CHANNELS`, `SLACK_ALLOWED_TOOLS` and `SLACK_WEB_BASE_URL` are deprecated in favour of the workspace binding and the organization's `slack` settings. The other variables below remain the operator's limits for every bot.

| Environment Variable | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `SLACK_BOT_TOKEN` | string | `""` | *(Ignored)* Each bot's token is entered in **Settings → Slack**. |
| `SLACK_APP_TOKEN` | string | `""` | *(Ignored)* Each bot's app-level token is entered in **Settings → Slack**. |
| `SLACK_THREAD_CONTEXT_LIMIT` | int | `20` | Number of recent messages from the current Slack thread auto-injected into the agent system prompt. |
| `SLACK_THREAD_CONTEXT_MAX_CHARS` | int | `6000` | Character budget for the injected Slack thread transcript (freshest messages kept). |
| `SLACK_CHANNEL_HISTORY_LIMIT` | int | `50` | Maximum messages returned by the on-demand `slack_read_channel_history` tool. |
| `SLACK_TEAM_ID` | string | `""` | *(Deprecated)* Workspace id. Discovered automatically via `auth.test` on bot connection. |
| `SLACK_ALLOWED_TOOLS` | string | `*` | *(Deprecated)* Tool allowlist; use the organization's `slack.allowed_tools` setting or a bot's **Policy**. |
| `SLACK_BLOCKED_TOOLS` | string | `git_revert,git_open_pr,git_push` | Platform-wide deny list: destructive tools always denied across all Slack bots. |
| `SLACK_MAX_ATTACHMENTS_PER_TURN` | int | `5` | Slack files ingested per turn; the rest are skipped and reported back to the caller. |
| `SLACK_MAX_CONSECUTIVE_FAILURES` | int | `3` | Consecutive failed turns before the abuse breaker pauses the workspace (`0` disables). |
| `SLACK_FAILURE_COOLDOWN_SECONDS` | int | `300` | Cooldown after the failure breaker trips. |
| `SLACK_USER_TURNS_PER_MINUTE` | int | `12` | Token-bucket capacity per `(team, user)`. Platform-level ceiling. |
| `SLACK_CHANNEL_TURNS_PER_MINUTE` | int | `30` | Token-bucket capacity per `(team, channel)`. Platform-level ceiling. |
| `SLACK_PROACTIVE_NOTIFY_AFTER_SECONDS` | int | `120` | Post a completion notice into the originating thread after a turn runs this long. |
| `SLACK_FILE_DOWNLOAD_TIMEOUT_SECONDS` | float | `30` | Download timeout for Slack-hosted files. |
| `SLACK_DM_POLICY` | enum | `paired` | *(Deprecated)* Who the agent may DM. Configured per bot under **Policy**; inherits organization policy by default. |
| `SLACK_HEALTH_CHECK_SECONDS` | float | `30` | How often the Socket Mode watchdog checks the connection; two failed checks in a row reconnect it (`0` disables it). |
| `ROCKET_PUBLIC_URL` | url | *(empty; the Helm chart sets it)* | Public address of the web app. Every Slack thread links to its session there (an organization can override it with `slack.web_base_url`). |
| `SLACK_OUTBOUND_CHANNELS` | csv | *(empty)* | *(Deprecated)* Allowlist of channel ids. Configured per bot under **Policy**. |

### Git Engine & GitHub App

| Environment Variable | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `GITHUB_APP_ID` | string | `None` | GitHub App ID used for authenticating API calls and generating installation tokens. |
| `GITHUB_APP_SLUG` | string | `None` | The App's URL slug; enables the "Install on a GitHub account" link in Settings > GitHub. |
| `ROCKET_DEFAULT_SANDBOX_IMAGE` | string | `ghcr.io/rocket-chat/sandbox:latest` | Default image for new sandboxes (`sandboxes.default_image`): the devbox with git, Python, Node, uv and ripgrep (its `gh` only points the agent to the `github_*` tools, since the sandbox holds no GitHub credential). Set it to a mirror in your own registry for private or air-gapped installs. |
| `GITHUB_APP_ALLOW_UNMAPPED_INSTALLATIONS` | bool | `false` | Legacy single-organization mode: mint App tokens for an installation no organization has mapped. Leave off when organizations share the platform. |
| `GITHUB_APP_PRIVATE_KEY` | string | `None` | RSA private key PEM for the GitHub App. |
| `GITHUB_WEBHOOK_SECRET` | string | `None` | HMAC SHA-256 secret configured in GitHub App settings to verify webhook payloads. |
| `GITHUB_TRIGGER_LABEL` | string | `ai-fix` | Issue label that automatically triggers the autonomous bug-fixing agent workflow. |
| `GIT_BOT_NAME` | string | `RocketChat Bot` | Author name injected into Git commits. |
| `GIT_BOT_EMAIL` | string | `bot@rocketchat.internal` | Author email injected into Git commits. |
| `GIT_DEFAULT_BRANCH` | string | `main` | Default target branch for repository checkouts. |
| `ROCKET_GIT_GATEWAY_URL` | string | `http://host.docker.internal:8000` | The API's address **as a sandbox reaches it**, for the git gateway: sandboxes hold no GitHub credential and clone and fetch through `<url>/v1/git/…`. The Docker driver maps `host.docker.internal` to the host on every engine, so the default works when the API publishes port 8000 on the host; the Helm chart sets the backend Service's in-cluster address. |

---

## 2. Settings you change in the app

Environment variables configure the **deployment**. Everything about how the agent behaves for your organization (models, budgets, approval policy, sandboxes, Slack, GitHub rules, member defaults) is a **setting** you change in the app under *Settings*, layered as platform default, organization, then member, and lockable by an administrator. They are all listed, with defaults and descriptions, in the [settings reference](/administer/settings-reference).

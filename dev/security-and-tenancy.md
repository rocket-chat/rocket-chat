# Security and tenancy

The guarantees the platform makes and the mechanism behind each, so a change can be checked against them. System structure is in [`architecture.md`](architecture.md); operator-facing hardening steps are in the `doc/` site (`doc/administer`, `doc/operate`).

## 1. Identity

Every HTTP route, the auth middleware and the WebSocket endpoints resolve the caller through one function, `resolve_identity` (`api/auth/identity.py`). Nothing else reads tenant headers or invents a default identity. Precedence:

1. Identity the middleware already attached to the request.
2. Tenant headers (`X-Tenant-Org-Id`, `X-Tenant-User-Id`, `X-User-Role`) — **only** when `ROCKET_TRUST_PROXY_HEADERS` is set (a proxy strips and re-sets them) or the development bypass is on.
3. Credentials: the signed session cookie/token (`api/auth/session_manager.py`), an OIDC bearer JWT (RS256 against the provider's JWKS, `api/auth/jwt_validator.py`), or the legacy NextAuth cookie. WebSocket clients that cannot set headers pass `?token=`.
4. The development identity — only with `DEV_AUTH_BYPASS` and never when `ENVIRONMENT=production`.
5. Otherwise the caller is anonymous and every protected route answers 401.

Sign-in methods all end in the same signed session: local accounts (`auth/local.py`, throttled per address), OIDC (authorization code + PKCE with signed state and nonce, `auth/oidc.py`), and Google/GitHub OAuth. The OAuth `state` is a signed token that must come back in the same browser that started the flow (a cookie carries it), so a forged or replayed callback is refused. SSO assigns an organization by explicit configuration (`ROCKET_SSO_ORG_MODE=single|domain`), never by guessing from a public mail domain, and an unverified e-mail address is refused. Organizations can restrict sign-in domains (`general` settings).

**Fail-closed start-up.** With `ENVIRONMENT=production`, `enforce_production_config` refuses to start if the session secret is missing, default or shorter than 32 characters, if no `ENCRYPTION_MASTER_KEY` is set, or if any development bypass flag is on. Persistence has no silent fallback in production: an unreachable database stops the process instead of serving from memory.

## 2. Authorization

* **Roles:** `admin`/`owner` (organization administrators), `developer`, `readonly`. Session-cookie identities have their roles re-read from the database (15-second cache), so demoting a member takes effect without waiting for the cookie to expire.
* **Session visibility:** a session is visible to its owner, to the organization when `is_shared`, and to listed collaborators; administrators see the whole organization. Only the owner or an administrator shares or deletes. `readonly` members can observe but not write: REST writes answer 403 and the WebSocket refuses every mutating frame. `_check_session_access` (`api/auth/access.py`) is the single check.
* **Scope of resources:** MCP servers, skills, knowledge bases and agent personas are either organization-scoped (usable by everyone, managed by administrators) or member-scoped (private to the member). Registering organization-wide or command (stdio) MCP servers is administrator-only, and command servers run only in the MCP runner pod (no database credentials, master key or Kubernetes token; ingress from the backend only, no private-network egress), each process with a clean environment holding only its configured variables.
* **Sandbox-adjacent features** (terminal, preview links, processes, workspace browsing) are gated like a write to the session, and the terminal can be disabled per organization (`sandboxes.allow_terminal`).
* Public routes are only `/health`, `/v1/health`, `/metrics`, `/v1/ready`, `/v1/drain`, the OpenAPI pages, preview links (capability token) and webhooks (signature checked). Everything else requires an identity.

## 3. Tenant isolation (PostgreSQL row-level security)

Application-level `WHERE org_id = …` is not trusted. Isolation is enforced by the database:

* Every tenant table has `ENABLE` **and** `FORCE ROW LEVEL SECURITY` and a policy on `org_id = current_setting('app.current_org_id', true)`; member-scoped rows add `user_id = current_setting('app.current_user_id', true)`. `FORCE` matters: it applies the policy to the table owner too.
* The application connects as a non-superuser role (`rocket_app`, `DATABASE_URL`); superusers bypass RLS. Migrations connect as the schema owner (`ADMIN_DATABASE_URL`).
* `DatabaseSessionManager.session(tenant_org_id=…)` sets the context with `set_config(…, true)` — transaction-local, so it is cleared on commit/rollback and can never leak through the connection pool. The HTTP middleware binds the caller's organization into the `current_tenant_org_id` context variable for every request; code that runs outside a request (WebSocket handlers, Slack, background tasks, resumed turns) must bind it explicitly.
* Cross-tenant jobs — the retention sweeper, turn-lease recovery, the mission claimer and the remote-MCP refresh — use the schema-owner connection on purpose and are the only code that does.
* Tests that need RLS run against a real PostgreSQL (`tests/security`, `tests/integration`; `test_schema_ownership.py` checks that tables are owned and forced correctly), so a missing policy is a test failure, not a review comment.

## 4. Secrets

* **At rest:** `CredentialCipher` (`config-engine/crypto.py`) seals secrets with AES-256-GCM. The key is `SHA-256(ENCRYPTION_MASTER_KEY)`, each value gets a fresh random 96-bit nonce, and the stored form is base64(`nonce ‖ ciphertext+tag`); tampering raises `CredentialDecryptionError`. `ENCRYPTION_PREVIOUS_KEYS` lets old values be read during a rotation; new values always use the primary key (`scripts/rotate_master_key.py` re-seals the database). The development key is refused in production.
* **What is encrypted:** model-provider keys (BYOK), MCP server credentials and per-member OAuth tokens, linked GitHub credentials, members' GPG signing keys (`git_signing`, with a passphrase), and stored webhook/integration secrets.
* **Write-only:** the API and logs show a fingerprint, never a secret. The model never sees a token: GitHub tools use credentials resolved server-side, and `clone_repository` injects them into git inside the sandbox.
* **Redaction:** tool output is scrubbed by the organization's redaction policy before it is stored in a trace or history, and outbound Slack messages pass the same pipeline.

## 5. The sandbox boundary

The model's code is untrusted. The mechanisms, from the inside out:

* Sandboxes run as an unprivileged user with CPU, memory and time limits from the organization's `sandboxes` settings; Kubernetes pods also set `fsGroup`, drop privileges and live in their own namespace. The Docker driver needs the host's Docker socket, so it is for local and single-node use; multi-tenant hosting uses the Kubernetes driver.
* **Egress:** an organization can allow-list destinations (CIDRs, IPs, host names, `*.suffix`) and block internal ranges. On Kubernetes it is a per-pod NetworkPolicy; on Docker the sandbox sits on an internal network and reaches the outside only through an allow-listing proxy that checks the *resolved* address and connects to the address it vetted (defeating DNS rebinding and metadata-endpoint access).
* **Server-side fetches** made on the agent's behalf (`web_fetch`, previews, MCP discovery) go through SSRF-safe HTTP (`tools/safe_http.py`, `sandbox_driver/loopback_http.py`): private and link-local ranges and redirects to them are refused; loopback requests into a sandbox are limited to a port range.
* **Preview** output is untrusted: it is served with a `sandbox` CSP (opaque origin, no cookies or storage) and embedded in a sandboxed iframe without `allow-same-origin`; the link is a capability token scoped to one session and port.
* **Write credentials stay out of the sandbox.** Commits leave it only through `git_push` / `git_open_pr` (`GitEngine.publish_branch`, `BranchPublisher`): the write token is a per-command environment variable for the one push, or is used by the platform against GitHub's API (GitHub-signed commits through `createCommitOnBranch`); the session environment carries a read-only token whenever the GitHub App can read the repository (`resolve_session_git_credentials`), and a `pre-push` hook explains the refusal. Where no App covers a repository the member's own token has to be in the sandbox (it cannot be narrowed), so only the hook and the prompt stand in the way: say so, do not claim a hard block.
* **Signing keys are the one long-lived secret placed in a sandbox**, because GnuPG must sign `git commit` there: only for a turn whose member opted in (`commit_as_me`, `gpg_key` mode), in a memory-backed keyring (`/dev/shm`), removed when the turn ends, never on the workspace volume; docs tell members to use a key made only for signing commits. Signing outside the sandbox would need the platform to re-sign commits before any push.
* **Plan and permission modes** are enforced in the tool registry, not in the prompt (see architecture §3). A hard `git_revert` is classified critical and always raises an approval in modes that gate publishing.

## 6. Inbound integrations

* **GitHub webhooks:** HMAC-SHA-256 over the raw body with a constant-time compare; with no secret configured every webhook is rejected unless unsigned delivery is explicitly allowed. Comments and pull requests only trigger the agent when the author is a repository insider (`OWNER`, `MEMBER`, `COLLABORATOR`), and an organization can restrict the repositories.
* **Slack:** the workspace maps to one organization; unlinked Slack users are rate-limited and bounded by a workspace spend quota before any turn starts, may only use what the organization's Slack policy allows, and must pair with a Rocket member for member-scoped features. Approval and Stop buttons are bound to the requester.
* **MCP:** command (stdio) servers execute in the MCP runner pod and are administrator-only (registration, changes and members' own values are audited); a member's own value for an overridable variable is encrypted, never returned, and applied only to that member's turns; remote servers use encrypted headers or OAuth (authorization-server discovery and dynamic client registration through the MCP SDK; tokens per member when the connector asks for it).

## 7. Accountability and limits

* **Audit:** `audit_events` records tool calls, approvals, model calls, credential and settings changes, terminal sessions and sign-in changes, tenant-isolated like everything else. `GET /v1/admin/audit` is administrator-only.
* **Retention:** per-organization windows for sessions, traces, memories, attachments and audit rows live in the `governance` settings; unset means keep, never an implicit purge.
* **Spend and rate:** budgets (organization, member, session) are checked before every provider call from the persisted ledger; a reached cap buys zero tokens. Per-organization caps bound running sandboxes, background processes and concurrent turns; the global gate bounds the process.
* **Releases:** CI runs the full test, lint, type, Helm and documentation gates; images and the chart are built from version tags by the release workflow.

## 8. Invariants to keep when changing code

1. A new table has a migration, `FORCE` RLS and an RLS test.
2. A new route authorises through the shared helpers and states its rule in a test; it never trusts a client-supplied organization.
3. Code outside a request binds the tenant context before touching the database.
4. Secrets enter the system encrypted, leave it as fingerprints, and never reach the model or the logs.
5. Anything the agent can influence that makes a server-side request goes through the SSRF-safe client.
6. Enforcement lives in the registry, the database or the driver — never only in a prompt.

# Security & Compliance

Rocket Chat is built with defense-in-depth principles for enterprise compliance and secure autonomous code execution.

---

## 1. Security Architecture Summary

```
+-----------------------------------------------------------------------------+
| LAYER 1: NETWORK & INGRESS SECURITY                                         |
| - Slack: 100% outbound Socket Mode (No inbound ports or public IP needed)    |
| - GitHub Webhooks: HMAC SHA-256 signature verification                      |
| - OIDC SSO: RS256 JWT signature verification via corporate JWKS             |
+-----------------------------------------------------------------------------+
                                      |
+-----------------------------------------------------------------------------+
| LAYER 2: TENANT ISOLATION (DATABASE)                                        |
| - PostgreSQL 16 Row-Level Security (RLS) enforced at the database engine    |
| - Non-superuser application connections (rocket_app) cannot bypass RLS      |
| - Transaction-local tenant context injection (SET LOCAL app.current_org_id) |
+-----------------------------------------------------------------------------+
                                      |
+-----------------------------------------------------------------------------+
| LAYER 3: CREDENTIAL SECURITY (AT REST)                                      |
| - BYOK API keys encrypted with hardware-grade AES-256-GCM                   |
| - PBKDF2 HMAC-SHA256 (100,000 iterations) with random 16-byte salt per key  |
| - 96-bit unique IVs and 128-bit authentication tags prevent tampering        |
+-----------------------------------------------------------------------------+
                                      |
+-----------------------------------------------------------------------------+
| LAYER 4: RUNTIME SANDBOX ISOLATION                                          |
| - Sandboxes isolated in dedicated agent-sandboxes namespace                 |
| - Kubernetes NetworkPolicy blocks cloud metadata (169.254.169.254/32 SSRF)  |
| - ResourceQuotas prevent runaway CPU/memory or container sprawl             |
+-----------------------------------------------------------------------------+
                                      |
+-----------------------------------------------------------------------------+
| LAYER 5: SUPPLY CHAIN & COMMIT ATTESTATION                                  |
| - Mandatory Co-authored-by git trailers attribute human and AI contributors |
| - Cryptographic signing via GitHub App Installation Tokens or GPG           |
+-----------------------------------------------------------------------------+
```

---

## 2. Cloud Instance Metadata Mitigation (SSRF)

When an autonomous agent runs arbitrary code (such as a developer test suite), malicious dependencies could attempt to exfiltrate IAM role credentials by querying cloud instance metadata endpoints:
- AWS IMDSv1/v2: `http://169.254.169.254/latest/meta-data/`
- GCP Metadata Server: `http://169.254.169.254/computeMetadata/v1/`
- Azure Instance Metadata: `http://169.254.169.254/metadata/instance`

Rocket Chat mitigates this attack vector at the Kubernetes networking layer via an automated `NetworkPolicy`:

```yaml
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: sandbox-network-policy
  namespace: agent-sandboxes
spec:
  podSelector: {}
  policyTypes:
    - Egress
  egress:
    - to:
        - ipBlock:
            cidr: 0.0.0.0/0
            except:
              - 169.254.169.254/32
```
All agent sandboxes can access the public internet (to clone repositories, install packages, and query APIs), but cannot communicate with the cloud instance metadata IP.

---

## 3. Container & Pod Security Hardening

To ensure defense-in-depth against malicious workloads or container escapes executed during autonomous sessions:

### A. Kubernetes Driver Hardening (`K8sSandboxDriver`)
1. **Service Account Token Isolation (`automountServiceAccountToken: false`):**
   Sandbox pods do not mount Kubernetes ServiceAccount tokens under `/var/run/secrets/kubernetes.io/serviceaccount`. Untrusted code running in the sandbox cannot query or compromise cluster APIs.
2. **Privilege Escalation Block (`allowPrivilegeEscalation: false`):**
   Prevents sandbox processes from gaining more privileges than their parent process (disables `setuid` binaries).
3. **Seccomp Profile (`RuntimeDefault`):**
   Enforces the kernel's default seccomp profile, filtering dangerous or deprecated system calls.
4. **Linux Capability Drops:**
   Explicitly drops dangerous Linux capabilities including `NET_RAW` (packet sniffing / spoofing), `SYS_ADMIN` (namespace/mount manipulation), `SYS_PTRACE` (cross-process memory inspection), and `DAC_READ_SEARCH`.

### B. Docker Driver Hardening (`DockerSandboxDriver`)
1. **No New Privileges (`security_opt=["no-new-privileges:true"]`):**
   Guarantees that child processes cannot acquire additional privileges via setuid executables.
2. **Capability Drops (`cap_drop`):**
   Drops `NET_RAW`, `SYS_ADMIN`, `SYS_PTRACE`, and `DAC_READ_SEARCH`.
3. **Fork Bomb Protection (`pids_limit=256`):**
   Restricts the total process count per sandbox container to 256, protecting host daemon availability.
4. **Volume Tenant Isolation:**
   Each session mounts an isolated Docker named volume (`sandbox_vol_<sessionId>`) labelled with the owning organization and user ID.

---

## 4. Unauthenticated Slack Hardening

The Slack bot is intentionally reachable by **anyone in the workspace**, with no Rocket Chat account required. That reach is bounded and observable rather than trusted:

* **Per-workspace quotas** (`slack_workspace_quotas`, migration `007`, RLS-scoped by `org_id`) cap monthly model spend, turns per rolling hour, and concurrent sessions. The check runs **before** any orchestrator call, so an over-limit request costs zero model tokens and never creates a session.
* **Token-bucket rate limits** per `(team, user)` and `(team, channel)` bound bursts; the buckets live in a bounded in-process LRU so they cannot grow without bound. Rejection counters are exposed on `GET /v1/slack/status`.
* **Server-side tool enforcement:** the Slack tool allowlist and destructive-tool blocklist (`git_revert`, `git_open_pr` by default) are enforced in `ToolRegistry.call_tool`, the same gate that enforces plan mode. The model is never asked to police itself.
* **Abuse guardrails:** a per-turn attachment cap and a consecutive-failure circuit breaker stop a failing or abusive workspace from burning budget on retries.
* **Observable denials:** every rejection produces an ephemeral reply to the caller (ephemeral messages are used where Slack supports them) and a status-endpoint counter, so a limit is never an invisible empty response.

Verification: unit tests cover quota/rate-limit allow/deny/expire on an injected fake clock and guardrail decisions; an integration test proves an over-limit turn is rejected with an ephemeral reply and **no** orchestrator call.

---

## 4b. Public session links

A session owner can create a read-only public link (`/shared/<token>`). It is a bearer capability: anyone holding it can read the conversation's questions and answers (never tool calls, files, the terminal or reasoning) until it expires (1-30 days) or is revoked. The feature is **off by default**; enable it with `general.public_session_links`. Turning it off immediately disables every link already issued, and creating or revoking a link is recorded in the audit log. Responses carry `Cache-Control: no-store`, `X-Robots-Tag: noindex` and `Referrer-Policy: no-referrer`.

## 5. Audit, Redaction & Data Governance

"Who ran what, when, with what result" is answerable from a durable, tenant-scoped audit
trail, and sensitive values are kept out of prompts, logs and outbound messages.

### A. Audit trail (`audit_events`, migration `008`, RLS-scoped by `org_id`)
One row is written for every sensitive action, with the actor and (for approvals) the
approver attribution:

| Event | Recorded for |
| :--- | :--- |
| `tool_call` | every tool execution, with status, duration and the redaction count |
| `approval_request` / `approval_decision` | each human or policy gate, including who approved/denied |
| `model_call` | each provider call, with effective model and token totals |
| `credential_change` | linking/unlinking a GitHub credential (never the secret) |
| `settings_change` | org/user settings mutations (keys only, never values) |

Administrators read the trail with `GET /v1/admin/audit?cursor=`, keyset-paginated newest
first; the response also carries the retention windows in force.

### B. Per-org approval policy
An organisation may declare, in its `governance` settings, which risk levels are
`auto_approve`d or `deny`ed: `{"approval_policy": {"auto_approve": ["low"], "deny": ["critical"]}}`.
Anything not named **keeps the human gate** — the default never silently approves a risky
action. The decision is enforced server-side on the `approval_required` event; the model
is never asked to police itself.

### C. Redaction pipeline
Applied to tool output **before** it enters the trace, the persisted history and the
terminal log, and to any value written through the memory service:

* built-in secret patterns (API keys, JWTs, private keys, `password:`/`token:` assignments)
  are active whenever redaction is enabled;
* PII patterns and org-supplied regexes (`redaction_patterns`) are **off by default** and
  org-tunable, because a false positive silently destroys legitimate content;
* the replacement marker and a count are always recorded (`redacted`/`redaction_count` on
  the audit row, `redacted` on the stored tool call), and `CredentialCipher.fingerprint`
  provides a correlation token that never reveals the removed value.

### D. Retention windows (gap M8.3)
Per-org windows for sessions, traces, memories, attachments and audit rows are configured
in the `governance` settings domain (`session_retention_days`, `trace_retention_days`,
`memory_retention_days`, `attachment_retention_days`, `audit_retention_days`). An unset
window means "keep indefinitely". The reconciliation loop invokes the retention sweep, which
runs on the schema-owner connection (RLS is forced) and purges only rows older than the
configured cutoff.

### E. Model data governance
The org's `governance` settings may carry `provider_allowlist`, `zero_retention` and
`region`. The allowlist is enforced server-side against the effective model before the turn
dispatches; the zero-retention flag and region are surfaced in settings and passed toward
the gateway.

### F. Rate limits beyond Slack
The authenticated turn path is bounded per user and per org with token buckets
(`ROCKET_API_RATE_LIMIT_PER_USER`, `ROCKET_API_RATE_LIMIT_PER_ORG`), matching the Slack
path's per-`(team,user)`/`(team,channel)` limits. A rejection returns `429` with a
`Retry-After` header and increments `rocket_quota_rejections_total{scope="api_user"|"api_org"}`.

### G. Workspace data-egress allowlist
The platform-wide sandbox egress default is `sandbox.networkPolicy.allowedEgressRules` in
the Helm chart. An organisation can additionally declare `egress_allowlist` and
`block_internal_subnets` in its `governance` settings. On Kubernetes the sandbox driver enforces that per
sandbox: the pod is labelled `rocket.chat/egress=restricted`, excluded from the chart's namespace-wide
egress policy, and given its own NetworkPolicy (created before the pod starts) allowing DNS plus the
allowlisted CIDRs/IPs/hosts - or the internet minus private ranges when only `block_internal_subnets` is set.
Host names are resolved once at sandbox start; NetworkPolicy enforcement needs a CNI that implements it.
The Docker driver has no per-container firewall and does **not** enforce these rules.

Verification: unit tests cover redaction positive/negative cases, retention-cutoff
selection, the approval policy, egress translation and the rate limiter; an integration
test proves a tool call produces an actor-attributed audit row and that a secret in tool
output is redacted in the **stored** trace.

---

## 6. Secret Lifecycle & Rotation

Every secret is stored **write-only** and never echoed. Rotation is a recurring operational
duty; the table below states the blast radius so an operator knows whether a change is a
config edit or a data migration.

| Secret | Protects | Rotation effect |
| :--- | :--- | :--- |
| `ENCRYPTION_MASTER_KEY` | Tenant BYOK provider keys, AES-256-GCM (`CredentialCipher`) | **Re-encryption migration required.** Changing it without re-encrypting makes every stored BYOK credential undecryptable. |
| `CREDENTIALS_ENCRYPTION_KEY` | User OAuth / PAT tokens, Fernet (`services/credentials.py`) | **Re-encryption migration required.** All stored user tokens become unreadable if rotated without re-encrypting. |
| `SLACK_BOT_TOKEN`, `SLACK_APP_TOKEN` | Socket Mode connectivity | Safe to rotate: regenerate in the Slack app, update the secret, rolling-restart the backend; Socket Mode reconnects automatically. |
| Provider keys (`OPENROUTER_API_KEY`, `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `DEEPSEEK_API_KEY`, `GEMINI_API_KEY`) | Platform-provided model access | Safe to rotate: issue a new key at the provider, update `values.yaml` (or the `existingSecret`), rolling-restart. Tenant BYOK keys are separate rows in §A below. |
| `ROCKET_ATTACHMENT_SIGNING_KEY` | Signed attachment retrieval URLs | Safe to rotate: it only invalidates outstanding preview tokens. When unset, an ephemeral per-process key is used and tokens never leave the pod. |
| Auth secrets (`NEXTAUTH_SECRET`, OIDC/Google/GitHub client secrets, `GITHUB_APP_PRIVATE_KEY`, `GITHUB_WEBHOOK_SECRET`) | SSO sessions, webhook signature verification, Git automation | Rotate at the identity provider / GitHub App, then update the secret and rolling-restart. Session cookies signed with the old `NEXTAUTH_SECRET` are invalidated. |

### A. BYOK master-key rotation (the one that needs a migration)

Because `CredentialCipher` derives a single AES-256-GCM key and has no key versioning, rotation
is performed by decrypting with the old key and re-encrypting with the new key. Use
`scripts/rotate_master_key.py`:

```bash
# 1. Back up the database first (see the Disaster Recovery runbook).
# 2. Dry run: count what would change, write nothing.
uv run python scripts/rotate_master_key.py \
  --database-url "$ADMIN_DATABASE_URL" \
  --old-key "$OLD_ENCRYPTION_MASTER_KEY" --new-key "$NEW_ENCRYPTION_MASTER_KEY"

# 3. Apply, then update the ENCRYPTION_MASTER_KEY secret and rolling-restart the backend.
uv run python scripts/rotate_master_key.py \
  --database-url "$ADMIN_DATABASE_URL" \
  --old-key "$OLD_ENCRYPTION_MASTER_KEY" --new-key "$NEW_ENCRYPTION_MASTER_KEY" --apply
```

The helper runs on the schema-owner connection (RLS is bypassed), never logs a secret, and
leaves any row that fails to decrypt with the old key untouched rather than corrupting it. The
same approach applies to `CREDENTIALS_ENCRYPTION_KEY` for the Fernet-encrypted user tokens.

### B. Rotation order that never bricks a deployment

1. Take a verified database backup (prove it restores — see the
   [Disaster Recovery runbook](/operate/disaster-recovery)).
2. Re-encrypt encrypted column data (if the secret is a data key).
3. Update the secret in the vault / `existingSecret`; **do not** edit plaintext into
   `values.yaml` in production.
4. Rolling-restart; confirm `/v1/ready` is `200`.
5. Verify: a BYOK credential resolves and an existing OAuth token still decrypts before deleting
   the old key from the vault.

### C. Supply chain: dependency audit & SBOM

* **Dependency audit.** `uvx pip-audit -r <(uv export --no-dev --no-emit-workspace --no-emit-project
  --format requirements-txt)` reports **no known vulnerabilities** in the third-party Python
  dependencies, and `pnpm audit --prod` reports **no known vulnerabilities** in shipped Node
  dependencies. The full `pnpm audit` does surface advisories in the `mintlify` documentation
  toolchain (a build-time-only dependency that never ships in the runtime image); it is not part
  of the production attack surface.
* **SBOM.** `scripts/generate_sbom.py` emits a deterministic CycloneDX 1.5 document from
  `uv.lock`, `pnpm-lock.yaml` and the Dockerfile base images; `release.yml` runs it on every tag
  and attaches the artifact (and best-effort Syft image SBOMs) to the GitHub Release, so every
  release is accompanied by a machine-readable bill of materials.
* **Base images.** Both runtime images track maintained LTS lines (`python:3.12-slim-bookworm`,
  `node:22-alpine`). The one unpinned supply-chain input, the `uv` builder image, is pinned to
  the toolchain version the lockfiles were produced with rather than `uv:latest`.



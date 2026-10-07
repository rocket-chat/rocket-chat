# GitHub App Setup Guide

This guide details how to create and configure a GitHub App to enable automated bug fixing via issue labels (e.g. `ai-fix`), cryptographic commit signing with GitHub's **Verified** badge, and automated session resumption when comments are posted on pull requests.

---

## 1. Registering the GitHub App

1. Navigate to your GitHub Organization or Personal account **Settings** > **Developer settings** > **GitHub Apps**.
2. Click **New GitHub App**.
3. Configure core details:
   - **GitHub App name:** `Rocket Chat Autonomous Engineer` (or your preferred name).
   - **Homepage URL:** `https://your-domain.com`.

---

## 2. Configure Webhook Ingress

1. In the **Webhook** section:
   - Check **Active**.
   - **Webhook URL:** `https://<your-rocket-chat-domain>/v1/webhooks/github` (e.g. `https://rocket.company.com/v1/webhooks/github`).
   - **Webhook secret:** Generate a 32-character random string:
     ```bash
     openssl rand -hex 20
     ```
     Save this string; this is your `GITHUB_WEBHOOK_SECRET`.

---

## 3. Configure Repository Permissions

Under **Permissions** > **Repository permissions**, configure the following access levels:

| Permission | Access Level | Purpose |
| :--- | :--- | :--- |
| **Issues** | **Read & Write** | Read issue descriptions, inspect labels (`ai-fix`), and post progress updates. |
| **Pull requests** | **Read & Write** | Open automated pull requests and update PR conversation threads. |
| **Contents** | **Read & Write** | Clone target repositories, checkout branches, and push signed commits. |
| **Metadata** | **Read-only** | Mandatory default for GitHub Apps. |

---

## 4. Subscribe to Webhook Events

Under **Subscribe to events**, select:

- [x] **Issues** (triggers on `labeled` events when `ai-fix` is applied).
- [x] **Issue comment** (triggers on `created` events to wake up hibernated sandboxes when engineers post feedback).
- [x] **Pull request** (optional: enables tracking PR merges and reviews).

---

## 5. Generate Private Key & Retrieve App ID

1. Click **Create GitHub App**.
2. On the app summary page, note the **App ID** (numeric, e.g. `1048291`). This is your `GITHUB_APP_ID`.
3. Scroll down to **Private keys** and click **Generate a private key**.
4. Download the generated `.pem` file. The file contents represent your `GITHUB_APP_PRIVATE_KEY`.

---

## 6. Install the App on Target Repositories

1. In the left navigation, click **Install App**.
2. Select your Organization or User account.
3. Choose either **All repositories** or select specific repositories where you want autonomous pair-programming enabled.
4. Click **Install**.

---

## 7. Rocket Chat Configuration

Set the environment variables in your deployment:

```bash
GITHUB_APP_ID="1048291"
GITHUB_WEBHOOK_SECRET="your-generated-webhook-secret"
GITHUB_TRIGGER_LABEL="ai-fix"
GITHUB_APP_PRIVATE_KEY="-----BEGIN RSA PRIVATE KEY-----
MIIEowIBAAKCAQEA0k...
...
-----END RSA PRIVATE KEY-----"

# Optional: User-level OAuth Client for personal PR creation
GITHUB_CLIENT_ID="gh-oauth-client-id"
GITHUB_CLIENT_SECRET="gh-oauth-client-secret"
```

---

## 8. Dual-Layer GitHub Integration Architecture

Rocket Chat implements a two-tier hybrid GitHub engine designed for enterprise compliance and developer convenience:

### Layer 1: Organization GitHub App (System Infrastructure)
* **Scope**: Org-wide installation on managed repositories.
* **Role**: Primary webhook ingress, automated branch triggers (e.g. `ai-fix` label), repo structure indexing, and fallback execution.
* **Commit Signing**: Cryptographically signed commits using the GitHub App's private key, earning GitHub's green **Verified** badge.
* **Dynamic Installation Tokens**: When a user is unauthenticated or has not linked a personal OAuth token, the backend dynamically requests an ephemeral installation access token from the GitHub App with granular repository permissions configurable in Organization Settings (defaulting to `contents:read`, `pull_requests:read`).

### Layer 2: User Personal OAuth & Git Credential Broker
* **Scope**: Individual developer accounts linked via the Cockpit Settings UI (`/settings/github`).
* **Role**: Allows the AI agent to open Pull Requests and push commits **as the authenticated user**, preserving developer identity in git log and PR metrics.
* **Loopback Credential Broker**: Inside sandboxes, git credentials are not stored as plain-text tokens on disk. Instead, git operations query a loopback credential helper (`http://127.0.0.1:4099/git-credentials`) backed by ephemeral, scoped bearer tokens.

---

## 9. User Configuration & Cascading Settings (`/settings/github`)

Each developer can configure their personal git workflow preferences, which automatically inherit from organization-wide defaults set by administrators:

1. **Commit Authorship Policy**:
   - `hybrid_coauthor` (*Default*): Main commit authored by user with `Co-authored-by: Rocket Chat <bot@rocket-chat.dev>`.
   - `user_oauth`: 100% authored and committed by the logged-in user.
   - `app`: Authored and pushed directly as the GitHub App bot.

2. **Commit Signing Policy**:
   - `app_verified`: Use GitHub App key for automated **Verified** badges.
   - `user_gpg`: Inject the developer's GPG signing key for personal signatures.
   - `disabled`: Standard unsigned git commits.

3. **Pull Request Routing**:
   - Customizable default target branches (e.g. `main`, `develop`).
   - Automated reviewer assignments upon PR creation.

---

## 10. Verifying the Automated Workflow

1. Navigate to an installed repository on GitHub.
2. Open a new issue titled:
   ```text
   Fix broken unit tests in tests/unit/test_auth.py
   ```
3. Add the label **`ai-fix`** to the issue.
4. Rocket Chat receives the HMAC-signed webhook, provisions a sandbox, reproduces the failure, creates a signed commit with `Co-authored-by:` trailers, pushes a branch `rocket/fix-issue-...`, and opens a pull request.

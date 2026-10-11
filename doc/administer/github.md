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
- [x] **Pull request** (optional: lets sessions follow a pull request until it is merged or closed).
- [x] **Pull request review**, **Pull request review comment** and **Check suite** (optional: lets sessions follow reviews, comments and CI results; see [Sessions that follow a pull request](#sessions-that-follow-a-pull-request)).

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

## Mapping installations to organizations

One GitHub App serves the whole platform, but an installation belongs to one GitHub account's repositories, so each organization maps its own:

1. Set `GITHUB_APP_SLUG` (the `github.com/apps/<slug>` part of your App's URL).
2. As an organization administrator open **Settings > GitHub > GitHub App installations** and choose **Install on a GitHub account**. The link carries a signed, short-lived `state` naming your organization and you.
3. GitHub sends you back with the installation id; Rocket Chat confirms with GitHub that it is a real installation of this App and maps it to your organization.

Agents of an organization only ever use the installations mapped to it (matched to the repository owner), and GitHub webhook events are attributed to the organization that mapped the installation they came through. An organization with no mapping gets no App token. Single-organization installs that predate mapping can keep the old behaviour with `GITHUB_APP_ALLOW_UNMAPPED_INSTALLATIONS=true` (`github.allowUnmappedInstallations` in the Helm chart) and set `ROCKET_WEBHOOK_ORG_ID` for webhook sessions.

## Sessions that follow a pull request

A session can follow a pull request (or a branch) and hear about it without polling: CI results (check suites), reviews, comments, and the pull request being merged or closed. The agent follows a pull request it opens with `git_open_pr` automatically, and can follow or stop following any other with the `github_watch` / `github_unwatch` tools - for example "wait for CI on #123, then tell me".

* An event reaches a session only if that session follows the pull request or branch, belongs to the organization that mapped the App installation, and is not archived. An archived or deleted session stops following.
* The event appears as a line in the session. If the session is idle and the event comes from a repository insider (owner, member, collaborator, or CI), it also starts a turn; otherwise it is kept and shown to the agent at its next turn. Text written by anyone else is passed to the agent as information, never as instructions.
* Each watch wakes the agent at most five times, so a flapping CI cannot keep it busy forever; following ends by itself when the pull request is merged or closed.
* Needs the webhook events above (Pull request, Pull request review, Pull request review comment, Issue comment, Check suite).

## How an agent gets the code

A sandbox always starts **blank**, and many tasks need no repository at all. Nobody has to choose repositories up front: the agent works out *whether* and *which* one a task needs - from your request, the organization's knowledge bases (`kb_search`), the skills it can read and three read-only GitHub tools that need no sandbox (**`github_find_repositories`**, **`github_read_file`** for a README or directory listing, **`github_issues`** for an issue or pull request and its discussion; a connected GitHub MCP server adds its own `mcp__github__*` tools) - and only then calls the **`clone_repository`** tool (`owner/name` or an `https://github.com/owner/name` URL, optional branch and folder). Each lands in `/workspace/<name>`, repeating the call refreshes an existing clone, and git is installed first if the image lacks it. Authentication is automatic (your linked GitHub account, else the organization's mapped GitHub App installation); if GitHub refuses, the agent carries on without that repository where it can and tells you what it could not reach, rather than searching for tokens. The agent is also told that `ask_question` blocks the turn until you answer, so it is a last resort: it should look things up, or pick a reasonable option and state its assumption. To make this reliable for your organization, put the repository map in a knowledge base or skill ("the billing service is `acme/billing-api`").

## Templates, schedules and webhooks name a repository for you

A session never starts from a repository: the workspace is always blank and the agent clones what the task needs. Sources that know the repository in advance say so in the instruction instead: a session template or a schedule with a **Repository** (and optional branch) begins its prompt with *"Repository for this task: `owner/name`. Clone it with `clone_repository` first."*, and a GitHub webhook does the same for the repository of the issue or pull request (on a new `agent/issue-<n>-fix` branch unless the event names one). Repositories are `owner/name` or an `https://github.com/owner/name` URL; other schemes, credentials in the URL and local paths are refused. Your branch prefix (**Settings > Git & version control**) is part of the agent's context, so the branches it creates follow it.

### Repository setup

A repository can prepare its own workspace. Right after the agent clones a repository (the `clone_repository` tool), Rocket Chat runs the setup of that repository once, in its folder: `.rocket/setup.sh` if it has one, otherwise the `onCreateCommand` and `postCreateCommand` of `.devcontainer/devcontainer.json` (string, list or named-object forms; comments allowed). Only those commands are honoured - the image, features and mounts of a dev container are the platform's choice (the sandbox catalogue). Setup runs as the sandbox user with the GitHub token removed from its environment, has 15 minutes, reports progress and failures in the session, and is not repeated while the file is unchanged. Organizations can turn it off with `sandboxes.run_repo_setup`.

**Dependency caching.** Installing dependencies on every new session is slow; the supported answer is an image that already has them: build your own from the devbox (`FROM ghcr.io/<owner>/sandbox`), add the toolchains and warm caches your repositories need, and add it to the sandbox catalogue (**Settings → Organization → Sandboxes**). A session's installed dependencies persist with its workspace across hibernation and restarts. There is deliberately no cache volume shared between sessions: a writable shared cache lets one session poison what another builds from, and needs ReadWriteMany storage.


## When the agent cannot reach a repository

Work through these in order; the agent's own explanation in the session usually names the first one.

1. **The repository was never cloned.** Naming `owner/name` in the prompt does not clone it by itself; the agent has to call `clone_repository`. Ask it to, or check its steps in the session for the refusal it reports.
2. **The linked GitHub token expired.** A GitHub App user token lives eight hours (OAuth Apps' tokens do not expire). Rocket Chat stores the expiry and an encrypted single-use refresh token when you link GitHub and renews the token before it is used, so a link made with this version keeps working. A link made earlier, or one GitHub revoked, is detected (`GET /user` is rejected), **withheld from the sandbox**, reported in the session ("Your linked GitHub token is no longer valid…") and the App installation token is used instead. Re-link under **Settings > Git & version control**.
3. **No App installation is mapped to the organization.** The App fallback only mints tokens for installations mapped under **Settings > GitHub > GitHub App installations**. A single-organization deployment that predates mapping can set `GITHUB_APP_ALLOW_UNMAPPED_INSTALLATIONS=true` (the Helm chart's `github.allowUnmappedInstallations`); never enable it on a multi-organization deployment.
4. **The image has no `git`.** Minimal images such as `python:3.12-slim` ship without it. Cloning installs `git` and CA certificates first (`apt-get` or `apk`, which needs a root sandbox); with `sandboxes.require_non_root` use an image that already includes git, such as the published devbox.

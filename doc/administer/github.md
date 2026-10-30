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

### Sign-in and installation redirects

The App does two jobs that each need GitHub to send a person back to Rocket Chat:

* **Members link their GitHub account** (an OAuth sign-in through the App), which gives the agent a token acting as them.
* **Administrators map an installation** to their organization (see [Mapping installations to organizations](#mapping-installations-to-organizations)).

On the App's settings page, **Identifying and authorizing users** holds the **Callback URL** and the checkbox **Request user authorization (OAuth) during installation**. **Post installation** holds the **Setup URL**. While that checkbox is ticked, GitHub disables the Setup URL and sends administrators to the Callback URL after an installation instead. You don't need both: Rocket Chat handles an installation arriving at either.

Configure it this way (one URL for both jobs):

| GitHub App setting | Value |
| :--- | :--- |
| **Callback URL** | `https://<your Rocket Chat>/api/auth/github/callback` (add one line per address Rocket Chat is reached at). |
| **Expire user authorization tokens** | On (Rocket Chat refreshes linked tokens). |
| **Request user authorization (OAuth) during installation** | **On.** After an installation or a configuration change, GitHub sends the administrator to the Callback URL with the installation, and Rocket Chat maps it. |
| **Enable Device Flow** | Off. |
| **Setup URL** | Leave empty (GitHub disables it while the checkbox above is on). |

If you prefer to keep **Request user authorization (OAuth) during installation** off, set **Setup URL** to `https://<your Rocket Chat>/api/v1/github/setup` and tick **Redirect on update**, so configuring an existing installation also comes back. Members still link their account through the Callback URL.

Then, on the same page, generate a **client secret**. The App's **Client ID** and that secret are `GITHUB_CLIENT_ID` and `GITHUB_CLIENT_SECRET`: no separate OAuth App is needed. The App's URL name (`github.com/apps/<slug>`) is `GITHUB_APP_SLUG`, which shows the install link in **Settings > Organization > Git & GitHub**.

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
| **Actions** | **Read & Write** (optional) | Read workflow runs and logs; write lets the agent re-run, cancel or start workflows. |
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
- [x] **Workflow runs** (optional: for triggers on a failed workflow run, and CI results of sessions that follow a pull request).

What each event starts is the organization's choice: see [Triggers](/use/automation#triggers). GitLab, Jira and Linear send their webhooks to `/v1/webhooks/gitlab`, `/v1/webhooks/jira` and `/v1/webhooks/linear` (with `?org=<organization id>`), signed with the secrets in the [configuration reference](/install/configuration).

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

# The App's URL name (github.com/apps/<slug>): enables the install link in Settings > Organization > Git & GitHub.
GITHUB_APP_SLUG="rocket-chat-engineer"

# Members link their GitHub account through the App: its Client ID and a client secret.
GITHUB_CLIENT_ID="Iv23li..."
GITHUB_CLIENT_SECRET="generated-on-the-app-page"
```

---

## 8. Dual-Layer GitHub Integration Architecture

Rocket Chat implements a two-tier hybrid GitHub engine designed for enterprise compliance and developer convenience:

### Layer 1: Organization GitHub App (System Infrastructure)
* **Scope**: Org-wide installation on managed repositories.
* **Role**: Primary webhook ingress, automated branch triggers (e.g. `ai-fix` label), repo structure indexing, and fallback execution.
* **Commit Signing**: with `signing: github` the agent's commits are re-created through GitHub's API when the branch is published, so GitHub signs them and they earn the green **Verified** badge, made by the App (see *Publishing and signing* below). This needs the App to have **Contents: Read & write**.
* **Dynamic Installation Tokens**: the backend requests an ephemeral, read-only installation access token (granular permissions configurable in Organization Settings, defaulting to `contents:read`, `pull_requests:read`) for every session whose repository the App can read, whether or not the member linked a personal token. The permissions come from the `app_token_permissions` setting but are **always lowered to read** before the token is requested, so a `write` or `admin` there cannot give the sandbox a way to push: add reads such as `actions`, `checks` or `statuses` to let the agent follow CI. Publishing mints a separate, short-lived token with `contents: write` for the one repository, outside the sandbox.

### Layer 2: User Personal OAuth & Git Credential Broker
* **Scope**: Each member's own GitHub account, linked under **Settings → Git identity** (`/settings/github`).
* **Role**: Allows the AI agent to open Pull Requests and publish commits **as the authenticated user**, preserving developer identity in git log and PR metrics. Commits made as the member carry their GitHub **username** and e-mail by default (their primary e-mail, else the account's `id+login@users.noreply.github.com` address); the member can change either under **Settings → Git identity**.
* **Credentials never rest in the sandbox**: git reads the token from the command's environment through a credential helper, so nothing is written to disk. The sandbox holds no GitHub token at all: its git reads through the platform's git gateway, and every token stays on the platform.

---

## 9. Publishing and signing settings

Two settings decide how the agent's work leaves the sandbox (**Settings → Organization → Git & GitHub**):

1. **Publish as** (`publish_as`): `member` (*default*) opens pull requests with the member's linked account when there is one; `bot` opens them with the GitHub App. It is an organization default: a member may choose their own value in **Settings → Git identity**, unless an administrator locks it, and choosing `bot` needs the `publish_as_bot` capability (administrators by default). Either way the commits credit the member with a `Co-authored-by` trailer, added when the branch is published whatever made the commit.
2. **Commit signing** (`signing`), decided by administrators only:
   - `github` (*default*): commits are re-created through GitHub's API when published, so GitHub signs them (**Verified**, made by the GitHub App).
   - `member_gpg`: a member who imported a GPG key (**Settings → Git identity → Commit signing**) commits as themselves, signed with that key; a member without one gets the `github` behaviour.
   - `none`: standard unsigned commits, pushed as they are.

The earlier settings `commit_authorship_policy`, `pr_creation_policy`, `commit_signing_mode` and the member's **Commit as me** were merged into these two; stored values were moved by the upgrade.

The bot's identity (the deployment's `GIT_BOT_NAME` and `GIT_BOT_EMAIL` are its default), the default branch (`GIT_DEFAULT_BRANCH`) and the GitHub App token's permissions (`GITHUB_APP_DEFAULT_PERMISSIONS`) and repositories stay the organization's.

The read tools need the App's **Pull requests** and **Contents** read permissions; with **Issues** read too, a pull request's view also shows its conversation.

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
2. As an organization administrator open **Settings > Organization > Git & GitHub > GitHub App installations** and choose **Install on a GitHub account**. The link carries a signed, short-lived `state` naming your organization and you.
3. GitHub sends you back with the installation id; Rocket Chat confirms with GitHub that it is a real installation of this App and maps it to your organization.

GitHub sends you back through the App's Callback URL or its Setup URL, as configured in [Sign-in and installation redirects](#sign-in-and-installation-redirects).

To map an installation that already exists, use the same link: GitHub shows the account's existing installation; open its configuration and save it. **Settings > Organization > Git & GitHub** then says whether the installation was mapped, and if not, why.

Agents of an organization only ever use the installations mapped to it (matched to the repository owner), and GitHub webhook events are attributed to the organization that mapped the installation they came through. An organization with no mapping gets no App token. Single-organization installs that predate mapping can keep the old behaviour with `GITHUB_APP_ALLOW_UNMAPPED_INSTALLATIONS=true` (`github.allowUnmappedInstallations` in the Helm chart) and set `ROCKET_WEBHOOK_ORG_ID` for webhook sessions.

## Sessions that follow a pull request

A session can follow a pull request (or a branch) and hear about it without polling: CI results (check suites), reviews, comments, and the pull request being merged or closed. The agent follows a pull request it opens with `git_open_pr` automatically, and can follow or stop following any other with the `github_watch` / `github_unwatch` tools - for example "wait for CI on #123, then tell me".

* An event reaches a session only if that session follows the pull request or branch, belongs to the organization that mapped the App installation, and is not archived. An archived or deleted session stops following.
* The event appears as a line in the session. If the session is idle and the event comes from a repository insider (owner, member, collaborator, or CI), it also starts a turn; otherwise it is kept and shown to the agent at its next turn. Text written by anyone else is passed to the agent as information, never as instructions.
* Each watch wakes the agent at most five times, so a flapping CI cannot keep it busy forever; following ends by itself when the pull request is merged or closed.
* Needs the webhook events above (Pull request, Pull request review, Pull request review comment, Issue comment, Check suite).

## How an agent gets the code

A sandbox always starts **blank**, and many tasks need no repository at all. Nobody has to choose repositories up front: the agent works out *whether* and *which* one a task needs - from your request, the organization's knowledge bases (`kb_search`), the skills it can read and three read-only GitHub tools that need no sandbox (**`github_find_repositories`**, **`github_read_file`** for a README or directory listing, **`github_issues`** for an issue or pull request and its discussion; a connected GitHub MCP server adds its own `mcp__github__*` tools) - and only then calls the **`clone_repository`** tool (`owner/name` or an `https://github.com/owner/name` URL, optional branch and folder). Each lands in `/workspace/<name>`, repeating the call refreshes an existing clone, and git is installed first if the image lacks it. Authentication is automatic and happens on the platform, never in the sandbox: the sandbox holds no GitHub credential, and its git reaches GitHub through the platform's read-only **git gateway**, which adds the organization's mapped GitHub App installation token (read-only) or your linked account's token. Private dependencies a build pulls from GitHub over git work the same way. Clones are partial (the whole history, file contents fetched when first needed), so they cost about what a shallow clone did; `depth` makes them shallow too for very large repositories. If GitHub refuses, the agent carries on without that repository where it can and tells you what it could not reach, rather than searching for tokens. The agent is also told that `ask_question` blocks the turn until you answer, so it is a last resort: it should look things up, or pick a reasonable option and state its assumption. To make this reliable for your organization, put the repository map in a knowledge base or skill ("the billing service is `acme/billing-api`").

## Working on GitHub from a session

The sandbox has no GitHub credential, so the `gh` CLI cannot work there: the sandbox image ships a `gh` that only explains which tool to use instead. The agent works on GitHub through tools that run on the platform, never in the sandbox:

| What `gh` did | Tool and action |
| :--- | :--- |
| `gh repo list`, `gh repo view`, `gh api …/contents` | `github_find_repositories`, `github_read_file` |
| `gh issue view/list/create/comment/edit/close` | `github_issues` (reads an issue or lists them with no `action`; `create`, `comment`, `edit`, `close`, `reopen`) |
| `gh pr create` | `git_open_pr` (publishes the branch first) |
| `gh pr view/diff/checks/list` | `github_pull_request` (`view`, `files`, `diff` paged by file, `checks`, `list`) |
| reading and answering review comments | `github_pull_request` (`review_threads`, `reply_to_thread`, `resolve_thread`) |
| `gh pr edit/ready/review/comment/close/reopen/merge` | `github_pull_request` (`edit`, `ready`, `draft`, `request_reviewers`, `review`, `comment`, `close`, `reopen`, `merge`) |
| `gh run list/view/view --log-failed/rerun/cancel`, `gh workflow run` | `github_actions` (`runs`, `run_view`, `job_logs`, `rerun_failed`, `cancel`, `dispatch_workflow`) |
| `gh release list/view/create/edit` | `github_releases` |
| `gh search code/issues/prs/commits` | `github_search` |
| `gh api` | `github_api` (REST or GraphQL) |

**Credentials.** Reads use the session's read credential: the organization's GitHub App installation (read-only) or the member's linked account, else public data only. Changes use a write credential the platform resolves for the one repository, on the platform. With `publish_as: member` and a linked account, that is the member's account. Otherwise it is the organization's App installation for that repository (only for repositories `app_token_target_repositories` allows), falling back to the member's account. Each result says who acted ("as @octocat" or "as the organization's GitHub App"). For changes through the App, give it **Issues**, **Pull requests** and **Contents** *Read & write* (Contents for releases), and **Actions** *Read & write* to re-run, cancel or start workflows. Without a credential that may make the change, the tool says so and the agent reports what it could not do. Tokens never appear in tool arguments, results or events.

**Approval.** Reading never needs approval, even in *Plan* mode. Every change (a comment, a review, an edit, a release, a re-run, a `github_api` call other than `GET`) is a publish: *Ask* and *Auto-edit* request approval and *Plan* refuses it. Merging a pull request and any `github_api` `DELETE` always ask, in every mode except *Bypass*.

**What `github_api` refuses.** Some endpoints are refused in every mode, even for reading: secrets and variables (Actions, Dependabot, Codespaces, environments), deploy keys, webhooks, App installations and tokens, authorizations, enterprise and site administration, and a user's keys and e-mails. Changes are refused for repository settings, deletion and transfer, collaborators, teams and invitations, branch protection and rulesets, and anything under an organization or user. GraphQL mutations that touch the same resources are refused too, and a mutation must name the repository it changes. Changes are only made on repository resources (`/repos/owner/name/…`).

## Templates, schedules and webhooks name a repository for you

A session never starts from a repository: the workspace is always blank and the agent clones what the task needs. Sources that know the repository in advance say so in the instruction instead: a session template or a schedule with a **Repository** (and optional branch) begins its prompt with *"Repository for this task: `owner/name`. Clone it with `clone_repository` first."*, and a GitHub webhook does the same for the repository of the issue or pull request (on a new `agent/issue-<n>-fix` branch unless the event names one). Repositories are `owner/name` or an `https://github.com/owner/name` URL; other schemes, credentials in the URL and local paths are refused. Your branch prefix (**Settings > Git identity**) is part of the agent's context, so the branches it creates follow it.

### Repository setup

A repository can prepare its own workspace. Right after the agent clones a repository (the `clone_repository` tool), Rocket Chat runs the setup of that repository once, in its folder: `.rocket/setup.sh` if it has one, otherwise the `onCreateCommand` and `postCreateCommand` of `.devcontainer/devcontainer.json` (string, list or named-object forms; comments allowed). Only those commands are honoured - the image, features and mounts of a dev container are the platform's choice (the sandbox catalogue). Setup runs as the sandbox user with the GitHub token removed from its environment, has 15 minutes, reports progress and failures in the session, and is not repeated while the file is unchanged. Organizations can turn it off with `sandboxes.run_repo_setup`.

**Setups your organization defines.** A repository you cannot change, or a monorepo whose services each need their own preparation, gets its setup from **Settings → Organization → Sandboxes → Repository setups** (administrators). Each setup names a repository (`owner/name` or a pattern such as `acme/*`), a folder inside it (a path, `*` for each top-level folder, or empty for the root) and the bash commands that prepare it. The platform runs them in that folder right before the agent's first command there (`bash_exec`, tests, builds, package installs, background processes), once per workspace, and again when the commands change or the workspace was released; the agent sees one line saying it ran. If the setup fails, the agent's command does not run and the agent is shown the failing output, so it fixes the setup instead of building blind. These commands are written by your administrators, so unlike a repository's own `.rocket/setup.sh` they run with the agent's own access, private modules included. Example, for a Go monorepo whose services need generated code:

```bash
[ -d _tools ] || vendir sync -l
go mod download
make base && make mocks
```

**Dependency caching.** Installing dependencies on every new session is slow; the supported answer is an image that already has them: build your own from the devbox (`FROM ghcr.io/<owner>/sandbox`), add the toolchains and warm caches your repositories need, and add it to the sandbox catalogue (**Settings → Organization → Sandboxes**). A session's installed dependencies persist with its workspace across hibernation and restarts. There is deliberately no cache volume shared between sessions: a writable shared cache lets one session poison what another builds from, and needs ReadWriteMany storage.


## When the agent cannot reach a repository

Work through these in order; the agent's own explanation in the session usually names the first one.

1. **The repository was never cloned.** Naming `owner/name` in the prompt does not clone it by itself; the agent has to call `clone_repository`. Ask it to, or check its steps in the session for the refusal it reports.
2. **The linked GitHub token expired.** A GitHub App user token lives eight hours (OAuth Apps' tokens do not expire). Rocket Chat stores the expiry and an encrypted single-use refresh token when you link GitHub and renews the token before it is used, so a link made with this version keeps working. A link made earlier, or one GitHub revoked, is detected (`GET /user` is rejected), **withheld from the sandbox**, reported in the session ("Your linked GitHub token is no longer valid…") and the App installation token is used instead. Re-link under **Settings > Git identity**.
3. **No App installation is mapped to the organization.** The App fallback only mints tokens for installations mapped under **Settings > Organization > Git & GitHub > GitHub App installations**. A single-organization deployment that predates mapping can set `GITHUB_APP_ALLOW_UNMAPPED_INSTALLATIONS=true` (the Helm chart's `github.allowUnmappedInstallations`); never enable it on a multi-organization deployment.
4. **The image has no `git`.** Minimal images such as `python:3.12-slim` ship without it. Cloning installs `git` and CA certificates first (`apt-get` or `apk`, which needs a root sandbox); with `sandboxes.require_non_root` use an image that already includes git, such as the published devbox.

---

## 10. Publishing and signing

### Which repository the tools act on

A session can hold several clones, each in `/workspace/<name>`. The first repository the agent clones becomes the session's **active repository** (a session from a template, schedule or webhook that names a repository starts with that repository's folder): the git tools (`git_status`, `git_diff`, `git_log`, `git_commit`, `git_branch`, `git_checkpoint`, `git_revert`, `git_push`, `git_open_pr`), the project tools (`run_tests`, `lint`, `typecheck`, `build`, `package_manager`) and `bash_exec` act on it unless the agent names another folder (`path`, or `cwd` for `bash_exec`). Without an active repository they use `/workspace` if it is a repository, otherwise the only repository under it; with several and no choice the tool says which ones exist instead of guessing. The agent can switch with `set_active_repository`; the choice holds for the session's later turns. Relative paths given to the file tools (`file_read`, `file_write`, `file_edit`, `apply_patch`, `list_dir`, `grep_search`, `glob_files`, `code_search`) are inside the active repository, like the folder `bash_exec` runs in. Their results show the absolute path used, and an absolute `/workspace/...` path reaches anything else. Committing never creates a repository: a commit outside one is refused. The automatic checkpoint before a turn's first change covers every repository in the workspace, and the checkpoints panel shows the active one.

### Publishing

The agent never pushes with its own credentials. Commits leave the sandbox through the **`git_push`** tool (and `git_open_pr`, which publishes first). The sandbox only packs the branch's new commits into a bundle; the platform imports it into a throwaway repository of its own (the one commit the work builds on, without file contents, so this is quick for any repository size), applies the signing mode and publishes from there. Nothing the agent configures in the sandbox (hooks, credential helpers, push URLs, `insteadOf` rules) takes part:

* The sandbox holds no GitHub credential in any mode, so it cannot push: the git gateway serves reads only and refuses a push, and a hook explains that `git_push` is the way.
* **`github` signing** replays the branch's new commits through GitHub's GraphQL `createCommitOnBranch` mutation, in order, with the same messages (including `Co-authored-by` trailers). GitHub signs them. The App needs **Contents: Read & write** and its installation must be **mapped to your organization** (Settings → Organization → Git & GitHub → GitHub App installations). When no App token can be minted, the tool publishes with the member's own linked account instead (GitHub then shows them as the committer) and says so; with neither, it says what to check. Limits: symbolic links and submodules cannot be created this way (the tool refuses and says which file), a new executable file loses its executable bit (reported), merge commits must be rebased away, and one commit's file contents are limited to about 20 MB. Afterwards the sandbox's branch is moved onto the commits GitHub created, so local hashes change; uncommitted work is untouched.
* **`member_gpg` signing** re-creates the new commits signed with the member's key (same tree, parents, author, committer, dates and message; the local hashes change and the sandbox's branch follows), then pushes them with the member's token. **`none`** pushes the commits as they are, with the member's token (else the App's).
* `force_with_lease` lets `git_push` replace a remote branch that moved on, only from the remote head the sandbox last fetched. A repository that has no commits yet receives the whole branch (except with `github` signing, which can only add to an existing branch and says so).

### Member signing keys (`member_gpg` signing)

A member imports an armored private key under **Settings → Git identity → Commit signing**. The key is checked by GnuPG itself in a throwaway keyring (it must be a usable, unexpired signing key; its passphrase must work; the commit e-mail must be one of its identities), stored encrypted, and never shown again; the page shows the fingerprint, the identities and the **public key** to add to GitHub (*Settings → SSH and GPG keys*). GitHub marks a commit **Verified** only when that public key is on the author's account and the commit e-mail is verified there.

The key never enters a sandbox. The agent's commits in the sandbox are unsigned; when it publishes, the platform signs them with the key in a temporary keyring (memory-backed where the host offers one) that is removed right after. Deployments need GnuPG in the API image (included); the sandbox image no longer needs it for signing.

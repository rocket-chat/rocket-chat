---
title: REST API
description: Every endpoint the Rocket Chat API serves, generated from its OpenAPI schema.
---

# REST API

Every endpoint below is generated from the API's own OpenAPI schema, so it is always the
current surface. The machine-readable schema is [`openapi.json`](/api/openapi.json); a running
API also serves interactive documentation at `/docs` and `/redoc`.

**Authentication.** Browsers use the session cookie set at sign-in; programmatic clients send
`Authorization: Bearer <token>`. Every endpoint is scoped to the caller's organization.
Endpoints under `/v1/admin` need an administrator; a *read-only* member can read but not change
sessions. Errors are JSON `{"detail": ...}` with the usual HTTP status codes (`401` not signed
in, `403` not allowed, `404` not found or not yours, `422` invalid input).

For live updates use the [WebSocket protocol](/api/websocket).

## Admin · Users

### `GET /v1/admin/users`

List the organization's members (admin/owner only).

**Parameters**

| Name | In | Required | Type |
| :--- | :--- | :--- | :--- |
| q | query | no | string |
| role | query | no | string |
| limit | query | no | integer |

**Responses:** `200`

### `POST /v1/admin/users`

Create a password-login member with a one-time temporary password (admin/owner only).

**Request body**

| Field | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| email | string | yes |  |
| name | string | yes |  |
| role | string | no |  |

**Responses:** `200`

### `PATCH /v1/admin/users/{user_id}`

Change a member's role (admin/owner only). Takes effect on their next login.

**Parameters**

| Name | In | Required | Type |
| :--- | :--- | :--- | :--- |
| user_id | path | yes | string |

**Request body**

| Field | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| role | string | yes | One of owner \| admin \| developer \| readonly \| guest |

**Responses:** `200`

### `POST /v1/admin/users/{user_id}/reset-password`

Issue a new one-time password for a member and unlock their account (admin/owner only).

**Parameters**

| Name | In | Required | Type |
| :--- | :--- | :--- | :--- |
| user_id | path | yes | string |

**Responses:** `200`

## Agents

### `GET /v1/agents`

List the built-in personas plus the organization's and the caller's own.

**Responses:** `200`

### `POST /v1/agents`

Create or update a custom agent persona, enforcing scope boundaries.

A non-administrator who does not name a scope gets a personal persona. The returned
persona carries the stored id, which can differ from the proposed one.

**Request body**

| Field | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| id | string | yes | Unique persona identifier |
| name | string | yes | Display name of persona |
| role_title | string | yes | Role title |
| description | string | no | Detailed persona description |
| system_prompt | string | yes | Agent system prompt / instructions |
| model | string | no | Model ID; empty inherits the user/org default |
| whitelisted_tools | list of string | no | List of allowed tool names |
| skill_ids | list of string | no | Attached custom skill IDs |
| knowledge_base_ids | list of string | no | Attached knowledge base IDs |
| scope | string | no | 'org' or 'user' |
| user_id | string | no | Owner user ID for personal agents |
| built_in | boolean | no | Shipped with the platform; an administrator can edit it for the organization |
| customized | boolean | no | A built-in agent whose organization edit is in effect (it can be reset) |
| icon | string | no | Avatar/icon key |
| sandbox_image | string | no | Sandbox image; empty inherits the organization default |
| cpu_limit | string | no | CPU cores; empty inherits the org setting |
| memory_limit | string | no | Memory limit; empty inherits the org setting |
| temperature | number | no | Sampling temperature override |
| thinking_budget_tokens | integer | no | Extended thinking budget tokens override |
| reasoning_effort | string | no | Reasoning effort tier override ('low', 'medium', 'high') |
| max_turns | integer | no | Maximum reasoning steps per turn for this agent; empty inherits the organization's limit, and a value above it is refused |
| tool_loading | `all` / `on_demand` / `auto` | no | Which allowed tools every step carries: all, on_demand (the platform's tools and active_tools; the others are loaded when needed) or auto; empty inherits the organization's setting |
| active_tools | list of string | no | Tools always loaded, by name or pattern (mcp__signoz*__*search*); with on_demand, only these and the loading tools are, when set |

**Responses:** `200`

### `GET /v1/agents/limits`

The ceilings an agent's own settings must stay under (today: steps per turn).

**Responses:** `200`

### `DELETE /v1/agents/{persona_id}`

Delete a custom agent persona.

**Parameters**

| Name | In | Required | Type |
| :--- | :--- | :--- | :--- |
| persona_id | path | yes | string |

**Responses:** `200`

### `GET /v1/subagents`

List the organization's subagent roles (built-in defaults plus its overrides).

**Responses:** `200`

### `PUT /v1/subagents/{subagent_id}`

Override a subagent role for this organization (admin only; other orgs are unaffected).

**Parameters**

| Name | In | Required | Type |
| :--- | :--- | :--- | :--- |
| subagent_id | path | yes | string |

**Request body**

| Field | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| id | string | yes |  |
| name | string | yes |  |
| role_title | string | yes |  |
| description | string | yes |  |
| system_prompt | string | yes |  |
| model | string | no |  |
| max_turns | integer | no | Steps the subagent may take before it must report. |
| whitelisted_tools | list of string | no |  |
| temperature | number | no |  |
| enabled | boolean | no |  |

**Responses:** `200`

## Attachments

### `GET /v1/sessions/{session_id}/attachments`

List the attachments already stored for a session (cockpit reload).

**Parameters**

| Name | In | Required | Type |
| :--- | :--- | :--- | :--- |
| session_id | path | yes | string |

**Responses:** `200`

### `POST /v1/sessions/{session_id}/attachments`

Validate and store one uploaded file for the current turn.

**Parameters**

| Name | In | Required | Type |
| :--- | :--- | :--- | :--- |
| session_id | path | yes | string |

**Responses:** `200`

### `GET /v1/sessions/{session_id}/attachments/{attachment_id}`

Signed retrieval of an attachment's bytes for a preview or download.

**Parameters**

| Name | In | Required | Type |
| :--- | :--- | :--- | :--- |
| session_id | path | yes | string |
| attachment_id | path | yes | string |
| token | query | yes | string |

**Responses:** `200`

## authentication

### `GET /api/auth/configured-providers`

Return available SSO providers configured in environment.

**Responses:** `200`

### `POST /api/auth/dev-login`

Offline development login setting session cookie directly.

**Request body**

| Field | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| username | string | no |  |
| org_id | string | no |  |
| roles | list of string | no |  |

**Responses:** `200`

### `POST /api/auth/local/login`

Sign in with an e-mail and password (self-hosted installs without an identity provider).

**Request body**

| Field | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| email | string | yes |  |
| password | string | yes |  |

**Responses:** `200`

### `POST /api/auth/local/password`

Change the signed-in member's password (also clears an administrator-issued one).

**Request body**

| Field | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| current_password | string | yes |  |
| new_password | string | yes |  |

**Responses:** `200`

### `POST /api/auth/logout`

Terminate current user session by clearing the cookie.

**Responses:** `200`

### `GET /api/auth/me`

Return identity and tenancy claims of currently authenticated session.

**Responses:** `200`

### `GET /api/auth/providers`

Return available SSO providers configured in environment.

**Responses:** `200`

### `GET /api/auth/{provider}/callback`

Handle OAuth 2.0 authorization callback, exchange token, provision user, and set session.

A GitHub App that asks for user authorization during installation sends the administrator
here instead of to its Setup URL: that redirect carries our signed install state and the
installation id, and is handed to the installation mapping.

**Parameters**

| Name | In | Required | Type |
| :--- | :--- | :--- | :--- |
| provider | path | yes | string |
| code | query | no | string |
| state | query | no | string |
| error | query | no | string |
| installation_id | query | no | integer |

**Responses:** `200`

### `GET /api/auth/{provider}/login`

Initiate OAuth 2.0 / OIDC authorization code flow.

**Parameters**

| Name | In | Required | Type |
| :--- | :--- | :--- | :--- |
| provider | path | yes | string |
| redirect_uri | query | no | string |
| link_only | query | no | boolean |

**Responses:** `200`

## Checkpoints

### `GET /v1/sessions/{session_id}/checkpoints`

Checkpoints of the session's workspace, newest first.

**Parameters**

| Name | In | Required | Type |
| :--- | :--- | :--- | :--- |
| session_id | path | yes | string |

**Responses:** `200`

### `POST /v1/sessions/{session_id}/checkpoints`

Snapshot the workspace now (including uncommitted work).

**Parameters**

| Name | In | Required | Type |
| :--- | :--- | :--- | :--- |
| session_id | path | yes | string |

**Request body**

| Field | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| label | string | no |  |

**Responses:** `200`

### `POST /v1/sessions/{session_id}/checkpoints/rewind`

Restore the workspace to a checkpoint the platform created.

**Parameters**

| Name | In | Required | Type |
| :--- | :--- | :--- | :--- |
| session_id | path | yes | string |

**Request body**

| Field | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| reference | string | yes |  |
| mode | string | no | 'files' restores tracked files; 'hard' also resets the worktree and index |

**Responses:** `200`

## Diffs

### `POST /v1/sessions/{session_id}/diffs/apply`

Apply one file's hunks of a proposed multi-file diff to the session workspace.

The caller's click is the approval; the request is still authenticated and ACL-checked
against the session, and RLS scopes the workspace to the caller's organization.

**Parameters**

| Name | In | Required | Type |
| :--- | :--- | :--- | :--- |
| session_id | path | yes | string |

**Request body**

| Field | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| path | string | yes | Workspace-relative path to apply |
| diff_content | string | yes | Unified diff; only hunks for `path` are applied |

**Responses:** `200`

## GitHub App

### `GET /v1/admin/github/install`

Send the administrator to GitHub to install the App, with a fresh signed state.

**Responses:** `200`

### `GET /v1/admin/github/installations`

List Installations

**Responses:** `200`

### `DELETE /v1/admin/github/installations/{installation_id}`

Remove Installation

**Parameters**

| Name | In | Required | Type |
| :--- | :--- | :--- | :--- |
| installation_id | path | yes | integer |

**Responses:** `200`

### `GET /v1/github/setup`

GitHub's post-install redirect: map the installation to the organization in ``state``.

**Parameters**

| Name | In | Required | Type |
| :--- | :--- | :--- | :--- |
| installation_id | query | yes | integer |
| state | query | no | string |
| setup_action | query | no | string |

**Responses:** `200`

## GitHub Webhooks

### `POST /api/v1/webhooks/github`

Ingests signed GitHub webhooks (issues.labeled, issue_comment.created).
Automatically wakes or initializes Docker/K8s sandbox workspaces and resumes ReAct agent loops.

**Parameters**

| Name | In | Required | Type |
| :--- | :--- | :--- | :--- |
| X-Hub-Signature-256 | header | no | string |
| X-GitHub-Event | header | no | string |

**Responses:** `200`

### `POST /v1/webhooks/github`

Ingests signed GitHub webhooks (issues.labeled, issue_comment.created).
Automatically wakes or initializes Docker/K8s sandbox workspaces and resumes ReAct agent loops.

**Parameters**

| Name | In | Required | Type |
| :--- | :--- | :--- | :--- |
| X-Hub-Signature-256 | header | no | string |
| X-GitHub-Event | header | no | string |

**Responses:** `200`

## Governance & Audit

### `GET /v1/admin/audit`

List the caller organisation's audit trail, newest first (admin only).

**Parameters**

| Name | In | Required | Type |
| :--- | :--- | :--- | :--- |
| limit | query | no | integer |
| cursor | query | no | string |

**Responses:** `200`

## MCP

### `GET /v1/mcp/oauth/callback`

Exchange the PKCE code for tokens and store them encrypted on the authorized server.

The state was minted for one authenticated member of one organization; the callback
must come from that same member, or a forwarded link could bind the attacker's account
to the victim's server (or the reverse).

**Parameters**

| Name | In | Required | Type |
| :--- | :--- | :--- | :--- |
| code | query | yes | string |
| state | query | yes | string |

**Responses:** `200`

### `GET /v1/mcp/runner`

Whether command (stdio) MCP servers can run on this installation (the MCP runner pod).

**Responses:** `200`

### `GET /v1/mcp/servers`

List registered MCP servers scoped to tenant organization and current user.

**Responses:** `200`

### `POST /v1/mcp/servers`

Register or update an MCP server, securely encrypting secrets with AES-256-GCM.

**Request body**

| Field | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| id | string | no |  |
| name | string | yes |  |
| description | string | no |  |
| transport | string | no |  |
| endpoint_or_command | string | yes |  |
| guidance | string | no |  |
| scope | string | no |  |
| auth | McpAuthInput | no |  |
| tools | list of McpToolItem | no |  |

**Responses:** `200`

### `POST /v1/mcp/servers/validate`

Validate MCP connection and probe tools prior to saving.

**Request body**

| Field | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| id | string | no | A registered server being edited: secrets left out of `auth` are kept. |
| transport | string | no |  |
| endpoint_or_command | string | yes |  |
| scope | string | no |  |
| auth | McpAuthInput | no |  |

**Responses:** `200`

### `DELETE /v1/mcp/servers/{server_id}`

Delete a registered MCP server.

**Parameters**

| Name | In | Required | Type |
| :--- | :--- | :--- | :--- |
| server_id | path | yes | string |

**Responses:** `200`

### `GET /v1/mcp/servers/{server_id}/env`

The variables of an organization command server that the caller may set for themselves.

**Parameters**

| Name | In | Required | Type |
| :--- | :--- | :--- | :--- |
| server_id | path | yes | string |

**Responses:** `200`

### `PUT /v1/mcp/servers/{server_id}/env`

Set (or, with an empty value, clear) the caller's own values; they are stored encrypted.

**Parameters**

| Name | In | Required | Type |
| :--- | :--- | :--- | :--- |
| server_id | path | yes | string |

**Request body**

| Field | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| values | object | no | Variable name to the member's value; an empty value removes the override |

**Responses:** `200`

### `GET /v1/mcp/servers/{server_id}/oauth/authorize`

Browser-friendly GET that redirects the user to the provider's consent screen.

**Parameters**

| Name | In | Required | Type |
| :--- | :--- | :--- | :--- |
| server_id | path | yes | string |

**Responses:** `200`

### `POST /v1/mcp/servers/{server_id}/oauth/authorize`

Generate RFC 9728 / PKCE authorization redirect URL for an MCP server.

**Parameters**

| Name | In | Required | Type |
| :--- | :--- | :--- | :--- |
| server_id | path | yes | string |

**Responses:** `200`

### `POST /v1/mcp/servers/{server_id}/probe`

Probe an MCP server and refresh its discovered tools.

**Parameters**

| Name | In | Required | Type |
| :--- | :--- | :--- | :--- |
| server_id | path | yes | string |

**Responses:** `200`

## Memory

### `GET /v1/memories`

List the caller's visible long-term memories for the cockpit inspector.

**Parameters**

| Name | In | Required | Type |
| :--- | :--- | :--- | :--- |
| scope | query | no | string |
| limit | query | no | integer |

**Responses:** `200`

### `DELETE /v1/memories/{memory_id}`

Delete one memory the caller can see (the inspector's "forget" action).

**Parameters**

| Name | In | Required | Type |
| :--- | :--- | :--- | :--- |
| memory_id | path | yes | string |

**Responses:** `200`

## Missions

### `GET /v1/missions`

List Missions

**Parameters**

| Name | In | Required | Type |
| :--- | :--- | :--- | :--- |
| all | query | no | boolean |

**Responses:** `200`

### `POST /v1/missions`

Create Mission

**Request body**

| Field | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| name | string | yes |  |
| prompt | string | yes |  |
| cron | string | yes | Five-field cron, e.g. '0 9 * * 1-5' |
| timezone | string | no |  |
| agent_id | string | no |  |
| git_repo | string | no | owner/name or https GitHub URL |
| git_branch | string | no |  |
| permission_mode | string | no |  |
| slack_channel | string | no | Slack channel id (C..., G... or D...) to post each run's result to |
| enabled | boolean | no |  |

**Responses:** `200`

### `PATCH /v1/missions/{mission_id}`

Update Mission

**Parameters**

| Name | In | Required | Type |
| :--- | :--- | :--- | :--- |
| mission_id | path | yes | string |

**Request body**

| Field | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| name | string | no |  |
| prompt | string | no |  |
| cron | string | no |  |
| timezone | string | no |  |
| enabled | boolean | no |  |
| permission_mode | string | no |  |
| slack_channel | string | no |  |

**Responses:** `200`

### `DELETE /v1/missions/{mission_id}`

Delete Mission

**Parameters**

| Name | In | Required | Type |
| :--- | :--- | :--- | :--- |
| mission_id | path | yes | string |

**Responses:** `200`

### `POST /v1/missions/{mission_id}/run`

Start a run immediately, without moving the schedule.

**Parameters**

| Name | In | Required | Type |
| :--- | :--- | :--- | :--- |
| mission_id | path | yes | string |

**Responses:** `200`

## Operations

### `GET /metrics`

Prometheus text exposition of the API's counters, gauges and histograms.

**Responses:** `200`

### `POST /v1/drain`

Marks this replica as draining: new work is refused with 503 while running turns finish. Called by the pod's preStop hook; administrators only.

**Responses:** `200`

### `GET /v1/ready`

Deep readiness: pings the database and reports code-index stats. A pod that is draining answers 503 so the load balancer stops sending it traffic.

**Responses:** `200`

## Other

### `GET /health`

Static **liveness** signal: healthy while the process runs, no dependency is checked.

Use `/v1/ready` for the database check; conflating the two would restart pods for a
database outage they cannot fix.

**Responses:** `200`

### `GET /v1/health`

Static **liveness** signal: healthy while the process runs, no dependency is checked.

Use `/v1/ready` for the database check; conflating the two would restart pods for a
database outage they cannot fix.

**Responses:** `200`

## Preview

### `POST /v1/sessions/{session_id}/preview`

Create Preview Link

**Parameters**

| Name | In | Required | Type |
| :--- | :--- | :--- | :--- |
| session_id | path | yes | string |

**Request body**

| Field | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| port | integer | yes | A port the app listens on |

**Responses:** `200`

## Processes

### `GET /v1/sessions/{session_id}/processes`

List Session Processes

**Parameters**

| Name | In | Required | Type |
| :--- | :--- | :--- | :--- |
| session_id | path | yes | string |

**Responses:** `200`

### `GET /v1/sessions/{session_id}/processes/{process_id}/output`

Read Process Output

**Parameters**

| Name | In | Required | Type |
| :--- | :--- | :--- | :--- |
| session_id | path | yes | string |
| process_id | path | yes | string |
| lines | query | no | integer |

**Responses:** `200`

### `POST /v1/sessions/{session_id}/processes/{process_id}/stop`

Stop Session Process

**Parameters**

| Name | In | Required | Type |
| :--- | :--- | :--- | :--- |
| session_id | path | yes | string |
| process_id | path | yes | string |

**Responses:** `200`

## Sandboxes

### `POST /v1/admin/sandboxes`

Create Sandbox

**Request body**

| Field | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| name | string | yes |  |
| image | string | yes |  |
| description | string | no |  |
| capabilities | list of string | no |  |
| is_default | boolean | no |  |
| profile | string | no |  |
| cpu_limit | string | no |  |
| memory_limit | string | no |  |
| disk_size | string | no |  |
| release_after_days | integer | no |  |

**Responses:** `200`

### `GET /v1/admin/sandboxes/profiles`

The placement profiles this deployment's operator defined, to choose per sandbox type.

**Responses:** `200`

### `PATCH /v1/admin/sandboxes/{sandbox_id}`

Update Sandbox

**Parameters**

| Name | In | Required | Type |
| :--- | :--- | :--- | :--- |
| sandbox_id | path | yes | string |

**Request body**

| Field | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| name | string | no |  |
| image | string | no |  |
| description | string | no |  |
| capabilities | list of string | no |  |
| is_default | boolean | no |  |
| profile | string | no |  |
| cpu_limit | string | no |  |
| memory_limit | string | no |  |
| disk_size | string | no |  |
| release_after_days | integer | no |  |

**Responses:** `200`

### `DELETE /v1/admin/sandboxes/{sandbox_id}`

Delete Sandbox

**Parameters**

| Name | In | Required | Type |
| :--- | :--- | :--- | :--- |
| sandbox_id | path | yes | string |

**Responses:** `200`

### `GET /v1/sandboxes`

List Sandboxes

**Responses:** `200`

## Sessions

### `GET /v1/sessions`

List one keyset page of sessions, newest ``(updated_at, session_id)`` first.

The list is always the authenticated identity's organization. Administrators see their
own sessions like everyone else unless they ask for the whole organization.

**Parameters**

| Name | In | Required | Type |
| :--- | :--- | :--- | :--- |
| scope | query | no | `mine` / `organization` |
| limit | query | no | integer |
| cursor | query | no | string |
| archived | query | no | boolean |
| q | query | no | string |

**Responses:** `200`

### `PATCH /v1/sessions/{session_id}/flags`

Pin or archive one session and broadcast the change to other tabs.

**Parameters**

| Name | In | Required | Type |
| :--- | :--- | :--- | :--- |
| session_id | path | yes | string |

**Request body**

| Field | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| is_pinned | boolean | no | New pinned state |
| is_archived | boolean | no | New archived state |

**Responses:** `200`

### `POST /v1/sessions/{session_id}/fork`

Branch a session into a new one with its own history and workspace.

**Parameters**

| Name | In | Required | Type |
| :--- | :--- | :--- | :--- |
| session_id | path | yes | string |

**Request body**

| Field | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| up_to_turn | integer | no | Copy history through the Nth user turn, inclusive of its assistant response. Omit to copy the entire conversation. |
| title | string | no | Optional title for the fork |

**Responses:** `200`

## Sessions & Telemetry

### `PUT /v1/admin/budget`

Set the organization's monthly spend cap (shorthand for the ``budgets`` settings domain).

**Responses:** `200`

### `GET /v1/admin/model-keys`

Provider keys shared by the organization (administrators).

**Responses:** `200`

### `PUT /v1/admin/model-keys/{provider}`

Put Org Model Key

**Parameters**

| Name | In | Required | Type |
| :--- | :--- | :--- | :--- |
| provider | path | yes | string |

**Request body**

| Field | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| api_key | string | yes | The provider API key |
| api_base | string | no | Endpoint for self-hosted or proxied providers (Ollama, vLLM, ...) |

**Responses:** `200`

### `DELETE /v1/admin/model-keys/{provider}`

Delete Org Model Key

**Parameters**

| Name | In | Required | Type |
| :--- | :--- | :--- | :--- |
| provider | path | yes | string |

**Responses:** `200`

### `GET /v1/admin/settings/{domain}`

Retrieve organization-wide policy configuration and locked keys (admin only).

**Parameters**

| Name | In | Required | Type |
| :--- | :--- | :--- | :--- |
| domain | path | yes | string |

**Responses:** `200`

### `PUT /v1/admin/settings/{domain}`

Replace the organization's defaults and locked keys for a domain (admin only).

**Parameters**

| Name | In | Required | Type |
| :--- | :--- | :--- | :--- |
| domain | path | yes | string |

**Request body**

| Field | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| config | object | yes | Organization default settings |
| locked_keys | list of string | no | Keys that users are prohibited from overriding |

**Responses:** `200`

### `GET /v1/knowledge/bases`

List all Knowledge Bases accessible to the organization and current user.

**Responses:** `200`

### `POST /v1/knowledge/bases`

Create a new pgvector semantic knowledge base collection.

**Request body**

| Field | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| name | string | yes | Collection name |
| description | string | no | Collection description |
| scope | string | no | 'org' or 'user' |
| vector_index | string | no | Index name |
| embedding_model | string | no | Embedding model identifier |
| hybrid_search | boolean | no | Enable hybrid BM25/vector search |

**Responses:** `200`

### `DELETE /v1/knowledge/bases/{kb_id}`

Delete a Knowledge Base collection and cascade delete documents and chunks.

**Parameters**

| Name | In | Required | Type |
| :--- | :--- | :--- | :--- |
| kb_id | path | yes | string |

**Responses:** `200`

### `POST /v1/knowledge/bases/{kb_id}/documents`

Ingest, chunk, and embed a document into the Knowledge Base.

**Parameters**

| Name | In | Required | Type |
| :--- | :--- | :--- | :--- |
| kb_id | path | yes | string |

**Request body**

| Field | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| title | string | yes | Document title |
| content | string | yes | Raw text or markdown content |
| source_type | string | no | Document source type (text, file, url) |
| metadata | object | no | Metadata dictionary |

**Responses:** `200`

### `POST /v1/knowledge/bases/{kb_id}/query`

Execute cosine similarity search against pgvector chunks.

**Parameters**

| Name | In | Required | Type |
| :--- | :--- | :--- | :--- |
| kb_id | path | yes | string |

**Request body**

| Field | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| query | string | yes | Natural language search query |
| limit | integer | no | Max result chunks |

**Responses:** `200`

### `GET /v1/models`

Return available LLM models and sandbox execution driver status.

**Responses:** `200`

### `POST /v1/sessions`

Create a new agent session with optional sandbox provisioning.

**Request body**

| Field | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| title | string | no | Session title or task name |
| tenant_org_id | string | no | Organization ID |
| tenant_user_id | string | no | User ID |
| agent_id | string | no | Active custom agent persona ID |
| model | string | no | Optional model override |
| mode | `agent` / `direct` | no | 'direct' runs the first turn without an agent, on `model` with every tool |
| initial_prompt | string | no | Initial prompt to ignite the first turn on creation |
| sandbox_id | string | no | A sandbox from the organization's catalogue (GET /v1/sandboxes); empty uses the catalogue's default |
| container_image | string | no | An image from the catalogue; prefer sandbox_id |
| is_shared | boolean | no | Whether session is shared with organization |
| collaborators | list of string | no | List of user IDs with shared access |
| permission_mode | string | no | Supervision level: plan, ask, auto_edit, auto or bypass (default follows settings) |
| metadata | object | no | Client-settable session metadata (inference_mode, selected_model, agent_id) |

**Responses:** `200`

### `POST /v1/sessions/cleanup`

Purge sessions, destroy orphan sandbox workspaces, and perform database maintenance.

**Responses:** `200`

### `POST /v1/sessions/reset`

Delete every session the caller owns and seat them in a fresh blank session.

Only the caller's own sessions go, administrators included: sessions shared with them
and other members' sessions are kept.

**Responses:** `200`

### `GET /v1/sessions/{session_id}`

Retrieve session record and metadata.

**Parameters**

| Name | In | Required | Type |
| :--- | :--- | :--- | :--- |
| session_id | path | yes | string |

**Responses:** `200`

### `PATCH /v1/sessions/{session_id}`

Update an existing session's bound agent persona, title, sharing status, or metadata.

**Parameters**

| Name | In | Required | Type |
| :--- | :--- | :--- | :--- |
| session_id | path | yes | string |

**Request body**

| Field | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| title | string | no | Updated session title |
| agent_id | string | no | Updated bound agent persona ID |
| is_shared | boolean | no | Updated session sharing status |
| collaborators | list of string | no | Updated collaborator IDs |
| checklist | list of object | no | Updated task checklist |
| conversation_history | list of object | no | Updated conversation messages |
| metadata | object | no | Metadata updates |

**Responses:** `200`

### `DELETE /v1/sessions/{session_id}`

Delete a session record and tear down any associated sandbox workspace.

**Parameters**

| Name | In | Required | Type |
| :--- | :--- | :--- | :--- |
| session_id | path | yes | string |

**Responses:** `200`

### `POST /v1/sessions/{session_id}/approvals/{action_id}`

Approve or deny an agent confirmation gate.

**Parameters**

| Name | In | Required | Type |
| :--- | :--- | :--- | :--- |
| session_id | path | yes | string |
| action_id | path | yes | string |

**Request body**

| Field | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| approved | boolean | yes | True if approved, False if denied |

**Responses:** `200`

### `POST /v1/sessions/{session_id}/questions/{question_id}`

Submit choice or write-in to an in-stream Interactive Decision Card.

**Parameters**

| Name | In | Required | Type |
| :--- | :--- | :--- | :--- |
| session_id | path | yes | string |
| question_id | path | yes | string |

**Request body**

| Field | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| selected_options | list of string | yes | Selected options from choices |
| custom_text | string | no | Optional custom user clarification text |

**Responses:** `200`

### `POST /v1/sessions/{session_id}/share`

Share session across organization members or configure specific collaborators.

**Parameters**

| Name | In | Required | Type |
| :--- | :--- | :--- | :--- |
| session_id | path | yes | string |

**Request body**

| Field | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| is_shared | boolean | yes | Whether session is shared across organization members |
| collaborators | list of string | no | List of authorized user IDs |

**Responses:** `200`

### `POST /v1/sessions/{session_id}/turns`

Dispatch user prompt to the ReAct engine in the background.

**Parameters**

| Name | In | Required | Type |
| :--- | :--- | :--- | :--- |
| session_id | path | yes | string |

**Request body**

| Field | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| prompt | string | yes | Instruction or steer message for the agent |
| model | string | no | Override LLM model name |
| agent_id | string | no | The agent for this turn; empty keeps the session's |
| mode | `agent` / `direct` | no | 'direct' runs this turn without an agent, on `model` with every tool; empty keeps the session's mode |
| attachment_ids | list of string | no | Attachment ids to attach to this turn only (never re-sent on later turns) |

**Responses:** `200`

### `DELETE /v1/sessions/{session_id}/turns`

Halt current autonomous execution immediately.

**Parameters**

| Name | In | Required | Type |
| :--- | :--- | :--- | :--- |
| session_id | path | yes | string |

**Responses:** `200`

### `GET /v1/settings-schema`

Every settings domain's fields (type, scope, default, constraints) for form rendering.

The cockpit builds its organization and member settings forms from this, so a field
exists in the UI if and only if it is declared in the registry.

**Responses:** `200`

### `GET /v1/settings/org/usage`

This month's token and dollar spend for the organization, from the persisted ledger.

**Responses:** `200`

### `GET /v1/settings/user/usage`

This month's personal token and cost usage, from the persisted ledger.

**Responses:** `200`

### `GET /v1/settings/{domain}`

Retrieve cascading settings for the current user and organization.

**Parameters**

| Name | In | Required | Type |
| :--- | :--- | :--- | :--- |
| domain | path | yes | string |

**Responses:** `200`

### `PUT /v1/settings/{domain}`

Save member overrides for a domain; org-managed or locked keys are rejected.

**Parameters**

| Name | In | Required | Type |
| :--- | :--- | :--- | :--- |
| domain | path | yes | string |

**Request body**

| Field | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| overrides | object | yes | Map of key-value user overrides |

**Responses:** `200`

### `DELETE /v1/settings/{domain}`

Reset member overrides for a domain back to organization defaults.

**Parameters**

| Name | In | Required | Type |
| :--- | :--- | :--- | :--- |
| domain | path | yes | string |

**Responses:** `200`

### `DELETE /v1/settings/{domain}/{key}`

Remove one personal override: the setting follows the organization's value again,
including its later changes.

**Parameters**

| Name | In | Required | Type |
| :--- | :--- | :--- | :--- |
| domain | path | yes | string |
| key | path | yes | string |

**Responses:** `200`

### `GET /v1/skills`

List all custom skills registered for the organization and current user.

**Responses:** `200`

### `POST /v1/skills`

Create or update a custom prompt skill with scoping.

**Request body**

| Field | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| name | string | no | Skill name; the title is used when omitted (one of the two) |
| title | string | no | Display title; the name when omitted |
| description | string | no | Skill purpose summary |
| body | string | no | Prompt instructions and guidelines |
| code | string | no | Legacy code fallback |
| signature | string | no | Function signature |
| scope | string | no | 'org' or 'user' |
| is_active | boolean | no | Whether skill is active |

**Responses:** `200`

### `POST /v1/skills/import`

Create or update a prompt skill from a ``SKILL.md`` document (front matter + body).

**Request body**

| Field | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| markdown | string | yes | Contents of a SKILL.md file |
| scope | string | no | 'org' (administrators) or 'user' |

**Responses:** `200`

### `DELETE /v1/skills/{skill_id}`

Delete a custom skill.

**Parameters**

| Name | In | Required | Type |
| :--- | :--- | :--- | :--- |
| skill_id | path | yes | string |

**Responses:** `200`

### `POST /v1/skills/{skill_id}/test`

Validate skill prompt and readiness.

**Parameters**

| Name | In | Required | Type |
| :--- | :--- | :--- | :--- |
| skill_id | path | yes | string |

**Request body**

| Field | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| test_arguments | object | no | Arguments to invoke skill with |

**Responses:** `200`

### `GET /v1/slack/status`

Return real connection state of embedded Slack Socket Mode Assistant.

**Responses:** `200`

### `POST /v1/slack/test`

Test real Slack API authentication and Socket Mode readiness.

**Responses:** `200`

### `GET /v1/user/credentials/github`

Return status of currently linked GitHub account or fallback.

**Responses:** `200`

### `DELETE /v1/user/credentials/github`

Unlink and delete stored GitHub credentials for the current user.

**Responses:** `200`

### `POST /v1/user/credentials/github/link`

Link a GitHub account by storing its access token encrypted.

**Request body**

| Field | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| access_token | string | yes |  |
| username | string | no |  |
| avatar_url | string | no |  |
| scopes | list of string | no |  |
| auth_type | string | no |  |

**Responses:** `200`

### `POST /v1/user/credentials/github/pat`

Validate and store a Personal Access Token (PAT) for the current user.

**Request body**

| Field | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| pat | string | yes |  |

**Responses:** `200`

### `POST /v1/user/credentials/github/test`

Test a provided token or current stored token against GitHub API.

**Request body**

| Field | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| pat | string | no |  |

**Responses:** `200`

### `GET /v1/user/git-signing`

The member's imported GPG signing key (never the private key) and whether it is in use.

**Responses:** `200`

### `PUT /v1/user/git-signing`

Import a GPG private key: it is validated by GnuPG, then stored encrypted.

**Request body**

| Field | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| private_key | string | yes | Armored GPG private key (gpg --armor --export-secret-keys KEY_ID) |
| passphrase | string | no | The key's passphrase, if it has one |
| commit_email | string | no | The e-mail commits carry; must be an identity of the key. Optional when the key has exactly one. |

**Responses:** `200`

### `DELETE /v1/user/git-signing`

Remove the member's imported signing key.

**Responses:** `200`

### `GET /v1/user/model-keys`

Provider keys the signed-in member stored (fingerprints only, never the secret).

**Responses:** `200`

### `PUT /v1/user/model-keys/{provider}`

Store or replace the member's own key for a provider.

**Parameters**

| Name | In | Required | Type |
| :--- | :--- | :--- | :--- |
| provider | path | yes | string |

**Request body**

| Field | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| api_key | string | yes | The provider API key |
| api_base | string | no | Endpoint for self-hosted or proxied providers (Ollama, vLLM, ...) |

**Responses:** `200`

### `DELETE /v1/user/model-keys/{provider}`

Delete My Model Key

**Parameters**

| Name | In | Required | Type |
| :--- | :--- | :--- | :--- |
| provider | path | yes | string |

**Responses:** `200`

## Sharing

### `GET /v1/sessions/{session_id}/sharing`

Get Sharing

**Parameters**

| Name | In | Required | Type |
| :--- | :--- | :--- | :--- |
| session_id | path | yes | string |

**Responses:** `200`

### `PUT /v1/sessions/{session_id}/sharing`

Share with the whole organization and/or exactly the members with these emails.

**Parameters**

| Name | In | Required | Type |
| :--- | :--- | :--- | :--- |
| session_id | path | yes | string |

**Request body**

| Field | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| org_wide | boolean | no |  |
| emails | list of string | no |  |

**Responses:** `200`

### `POST /v1/sessions/{session_id}/sharing/link`

Create (or replace) the read-only public link; the previous link stops working.

**Parameters**

| Name | In | Required | Type |
| :--- | :--- | :--- | :--- |
| session_id | path | yes | string |

**Request body**

| Field | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| expires_in_days | integer | no |  |

**Responses:** `200`

### `DELETE /v1/sessions/{session_id}/sharing/link`

Revoke Public Link

**Parameters**

| Name | In | Required | Type |
| :--- | :--- | :--- | :--- |
| session_id | path | yes | string |

**Responses:** `200`

### `GET /v1/shared/{token}`

The conversation behind a public link: no sign-in, read-only, never cached.

**Parameters**

| Name | In | Required | Type |
| :--- | :--- | :--- | :--- |
| token | path | yes | string |

**Responses:** `200`

## Slack

### `GET /v1/admin/slack/bots`

List all configured Slack bots for the caller's organization.

**Responses:** `200`

### `POST /v1/admin/slack/bots`

Register a new Slack bot with its encrypted tokens and identity.

**Request body**

| Field | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| name | string | yes |  |
| bot_token | string | yes | Slack bot token starting with xoxb- |
| app_token | string | yes | Slack app-level token starting with xapp- |
| slash_command | string | no | Empty: named after the bot's handle |
| agent_id | string | no |  |
| response_mode | string | no |  |
| respond_in_dms | boolean | no |  |
| listen_channels | list of string | no |  |
| private_reaction | string | no |  |
| private_dm_result | boolean | no |  |
| router | object | no |  |
| policy | object | no |  |
| enabled | boolean | no |  |

**Responses:** `201`

### `GET /v1/admin/slack/bots/{bot_id}`

Get details of a single Slack bot (tokens omitted).

**Parameters**

| Name | In | Required | Type |
| :--- | :--- | :--- | :--- |
| bot_id | path | yes | string |

**Responses:** `200`

### `PUT /v1/admin/slack/bots/{bot_id}`

Update a Slack bot's configuration or rotate its tokens.

**Parameters**

| Name | In | Required | Type |
| :--- | :--- | :--- | :--- |
| bot_id | path | yes | string |

**Request body**

| Field | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| name | string | no |  |
| bot_token | string | no |  |
| app_token | string | no |  |
| slash_command | string | no |  |
| agent_id | string | no |  |
| response_mode | string | no |  |
| respond_in_dms | boolean | no |  |
| listen_channels | list of string | no |  |
| private_reaction | string | no |  |
| private_dm_result | boolean | no |  |
| router | object | no |  |
| policy | object | no |  |
| enabled | boolean | no |  |

**Responses:** `200`

### `DELETE /v1/admin/slack/bots/{bot_id}`

Remove a Slack bot and stop its background connection.

**Parameters**

| Name | In | Required | Type |
| :--- | :--- | :--- | :--- |
| bot_id | path | yes | string |

**Responses:** `200`

### `GET /v1/admin/slack/bots/{bot_id}/decisions`

List recent routing decisions recorded for this bot.

**Parameters**

| Name | In | Required | Type |
| :--- | :--- | :--- | :--- |
| bot_id | path | yes | string |
| limit | query | no | integer |

**Responses:** `200`

### `GET /v1/admin/slack/bots/{bot_id}/manifest`

Generate the Slack app manifest JSON for this bot based on its configuration.

**Parameters**

| Name | In | Required | Type |
| :--- | :--- | :--- | :--- |
| bot_id | path | yes | string |

**Responses:** `200`

### `POST /v1/admin/slack/bots/{bot_id}/router/try`

Classify a sample message with the bot's router without storing a decision.

**Parameters**

| Name | In | Required | Type |
| :--- | :--- | :--- | :--- |
| bot_id | path | yes | string |

**Request body**

| Field | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| text | string | yes |  |
| thread_excerpt | string | no |  |

**Responses:** `200`

### `POST /v1/admin/slack/bots/{bot_id}/test`

Validate a bot's stored tokens with Slack, and refresh what Slack says about the bot
(its display name changes when the app's manifest does).

**Parameters**

| Name | In | Required | Type |
| :--- | :--- | :--- | :--- |
| bot_id | path | yes | string |

**Responses:** `200`

### `GET /v1/admin/slack/workspace`

Get Workspace Binding

**Responses:** `200`

### `PUT /v1/admin/slack/workspace`

Bind the currently connected Slack workspace to the administrator's organization.

**Responses:** `200`

### `DELETE /v1/admin/slack/workspace`

Unbind Connected Workspace

**Responses:** `200`

### `POST /v1/slack/pair`

Finish linking: the signed-in member claims the Slack user that issued the code.

**Request body**

| Field | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| code | string | yes | Code from the bot's `pair` command |

**Responses:** `200`

## Templates

### `GET /v1/templates`

List Templates

**Responses:** `200`

### `POST /v1/templates`

Create Template

**Request body**

| Field | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| name | string | yes |  |
| description | string | no |  |
| prompt | string | yes |  |
| scope | string | no |  |
| agent_id | string | no |  |
| git_repo | string | no |  |
| git_branch | string | no |  |
| permission_mode | string | no |  |

**Responses:** `200`

### `DELETE /v1/templates/{template_id}`

Delete Template

**Parameters**

| Name | In | Required | Type |
| :--- | :--- | :--- | :--- |
| template_id | path | yes | string |

**Responses:** `200`

### `POST /v1/templates/{template_id}/start`

Start a session from the template (the opening prompt runs immediately).

**Parameters**

| Name | In | Required | Type |
| :--- | :--- | :--- | :--- |
| template_id | path | yes | string |

**Responses:** `200`

## Tools

### `GET /v1/tools`

The full tool catalog (name, description, category, toolset).

**Responses:** `200`

## Turn Traces

### `GET /v1/sessions/{session_id}/turns`

List the persisted turn traces of one session for the cockpit turn inspector.

**Parameters**

| Name | In | Required | Type |
| :--- | :--- | :--- | :--- |
| session_id | path | yes | string |
| limit | query | no | integer |
| offset | query | no | integer |

**Responses:** `200`

## Workspace

### `GET /v1/sessions/{session_id}/workspace/file`

Read Workspace File

**Parameters**

| Name | In | Required | Type |
| :--- | :--- | :--- | :--- |
| session_id | path | yes | string |
| path | query | yes | string |

**Responses:** `200`

### `PUT /v1/sessions/{session_id}/workspace/file`

Write Workspace File

**Parameters**

| Name | In | Required | Type |
| :--- | :--- | :--- | :--- |
| session_id | path | yes | string |

**Request body**

| Field | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| path | string | yes |  |
| content | string | yes |  |

**Responses:** `200`

### `GET /v1/sessions/{session_id}/workspace/raw`

An image from the workspace (screenshots, assets) for the browser's preview.

Images only, served with a ``sandbox`` content-security-policy: an SVG is a document that
can carry script, and this is content the agent (or the repository) produced.

**Parameters**

| Name | In | Required | Type |
| :--- | :--- | :--- | :--- |
| session_id | path | yes | string |
| path | query | yes | string |

**Responses:** `200`

### `GET /v1/sessions/{session_id}/workspace/tree`

List Directory

**Parameters**

| Name | In | Required | Type |
| :--- | :--- | :--- | :--- |
| session_id | path | yes | string |
| path | query | no | string |

**Responses:** `200`

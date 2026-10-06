# REST API Reference

The Rocket Chat Control Plane exposes RESTful endpoints for session management, turn execution, human-in-the-loop decisions, and webhook ingress.

---

## 1. System Health

### `GET /health` or `GET /v1/health`
Checks control plane and database connectivity.

#### Response `200 OK`
```json
{
  "status": "healthy"
}
```

---

## 2. Session Management

### `POST /v1/sessions`
Initializes a new autonomous pair-programming session and provisions its sandbox.

#### Request Body
```json
{
  "repo_url": "https://github.com/org/repo.git",
  "branch": "main",
  "model": "openrouter/anthropic/claude-3.7-sonnet",
  "workspace_type": "repository"
}
```

#### Response `201 Created`
```json
{
  "session_id": "sess-9a8b7c6d",
  "status": "active",
  "created_at": "2026-10-03T09:00:00Z",
  "sandbox_status": "ready"
}
```

---

### `GET /v1/sessions/{session_id}`
Retrieves the session state, current plan checklist, and turn history.

#### Response `200 OK`
```json
{
  "session_id": "sess-9a8b7c6d",
  "status": "waiting_for_input",
  "model": "openrouter/anthropic/claude-3.7-sonnet",
  "plan": {
    "steps": [
      { "id": "1", "description": "Reproduce test failure", "status": "completed" },
      { "id": "2", "description": "Apply token refresh fix", "status": "in_progress" }
    ]
  },
  "token_count": 14250
}
```

---

## 3. Autonomous Turn Submission

### `POST /v1/sessions/{session_id}/turns`
Submits a new user instruction or bug report to an active session.

#### Request Body
```json
{
  "prompt": "Investigate why test_auth_token is failing and open a pull request."
}
```

#### Response `202 Accepted`
```json
{
  "turn_id": "turn-102",
  "status": "processing"
}
```

---

## 4. Decision Gates & Human Clarification

### `POST /v1/sessions/{session_id}/decision`
Submits user input in response to an `InteractiveQuestion` decision gate.

#### Request Body
```json
{
  "question_id": "q-45",
  "selected_options": ["Bypass cloud detachment latency using soft affinity"],
  "write_in_response": null
}
```

#### Response `200 OK`
```json
{
  "status": "resumed"
}
```

---

## 5. GitHub App Webhook Ingress

### `POST /v1/webhooks/github`
Receives HMAC SHA-256 signed events from the GitHub App.

#### Headers Required
- `X-Hub-Signature-256`: `sha256=<hex-hmac>`
- `X-GitHub-Event`: `issues` or `issue_comment`

#### Handled Events
1. `issues.labeled`: Triggers automated issue reproduction and bug fixing when labeled `ai-fix`.
2. `issue_comment.created`: Wakes up hibernated sandbox, pulls latest changes, appends feedback, and resumes execution.

### `POST /v1/sessions/{session_id}/share`

Owner (or admin) only. Body: `{"is_shared": true, "collaborators": ["user-id"]}`. Sessions are private by default; see [Route Permissions](../architecture/route-permissions.md).

### Task checklist

The agent maintains a checklist through the `update_task_checklist` tool. Each update is persisted on the session (`checklist` field of `GET /v1/sessions/{id}`) and streamed as a `plan_updated` WebSocket event: `{"tasks": [{"id", "title", "status", "description?"}]}` where `status` is `pending | in_progress | completed | failed`.

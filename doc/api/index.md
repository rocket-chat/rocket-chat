---
title: API
description: The REST API and the live WebSocket protocol.
---

# API

Everything the web app does goes through public interfaces you can use too.

| Interface | Use it for |
| :--- | :--- |
| [REST API](/api/rest) | Sessions, turns, settings, sandboxes, missions, templates, files, admin. Generated from the API's OpenAPI schema, so it is always current. |
| [WebSocket protocol](/api/websocket) | Streaming a session live: tokens, tool results, diffs, approvals, processes; sending turns and answers. |
| [`openapi.json`](/api/openapi.json) | The machine-readable schema, for client generators. |

A running API also serves interactive documentation at `/docs` and `/redoc`.

## Authentication

Browsers use the session cookie set at sign-in. Scripts send `Authorization: Bearer <token>` with a personal API token. Every request is scoped to the caller's organization; administrator endpoints live under `/v1/admin`.

### Personal API tokens

Create a token while signed in (with the session cookie); it is in the response only, so store it right away:

```bash
curl -s -X POST https://<host>/v1/user/api-tokens \
  -H "Content-Type: application/json" --cookie "rocket_session=<your session cookie>" \
  -d '{"name": "triage bot", "expires_in_days": 90}'
# => {"id": "1d85069603208786", "token": "rcpat_...", "expires_at": ..., ...}
```

- A token acts as you, with the role you hold when it is used: demoting you or removing your account takes effect on its next request.
- It works for every REST endpoint and, as `?token=`, for the WebSocket. It cannot create another token.
- It lasts `expires_in_days` (1 to 365, 90 by default); a member holds at most 20.
- `GET /v1/user/api-tokens` lists yours with when each was last used (to the minute); `DELETE /v1/user/api-tokens/{id}` revokes one at once. Administrators list and revoke every member's tokens under `/v1/admin/api-tokens`. Creating and revoking tokens is recorded in the audit trail.
- Only a hash of the token is stored. Tokens need the PostgreSQL store (the API answers `503` without it).

## A minimal client

Start a session with a prompt, then follow it:

```bash
curl -s -X POST http://localhost:8000/v1/sessions \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"initial_prompt": "List the files in the workspace"}'
# => {"session_id": "session_ab12cd34ef", ...}
```

Open `ws://<host>:8000/v1/sessions/<session_id>/ws?token=$TOKEN` to receive the events, or poll `GET /v1/sessions/<session_id>` for the stored conversation.

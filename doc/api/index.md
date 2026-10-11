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

Browsers use the session cookie set at sign-in. Scripts send `Authorization: Bearer <token>`. Every request is scoped to the caller's organization; administrator endpoints live under `/v1/admin`.

## A minimal client

Start a session with a prompt, then follow it:

```bash
curl -s -X POST http://localhost:8000/v1/sessions \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"initial_prompt": "List the files in the workspace"}'
# => {"session_id": "session_ab12cd34ef", ...}
```

Open `ws://<host>:8000/v1/sessions/<session_id>/ws?token=$TOKEN` to receive the events, or poll `GET /v1/sessions/<session_id>` for the stored conversation.

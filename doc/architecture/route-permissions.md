# Route Permissions & Session Access Audit

Sessions are **private to their owner by default**. Access is granted only through explicit sharing.

## Identity resolution

| Mode | Trigger | Identity source |
|------|---------|-----------------|
| Authenticated | `OIDC_JWKS_URL` or `OIDC_PUBLIC_KEY` set | `AuthMiddleware` validates the RS256 Bearer JWT and injects `org_id`, `user_id`, `roles` |
| Local development | no OIDC settings | `X-Tenant-User-Id`, `X-Tenant-Org-Id`, `X-User-Role` headers, defaulting to `dev_user` / `default_org` / `developer` |

!!! warning
    Never run a public deployment without OIDC configured: the header-based fallback is unauthenticated by design.

## Roles

| Role | Read own/shared | Create / write | Share / delete | Maintenance |
|------|-----------------|----------------|----------------|-------------|
| `admin` | all sessions in org | yes | any session | yes |
| `developer` | own + shared + collaborator | yes | own sessions only | no |
| `readonly` | own + shared + collaborator | **no** (403) | no | no |

## Session visibility

A session is visible to a user when they are the owner, `is_shared` is true (visible to the organization), or their id is in `collaborators`.
Sharing is managed by the owner (or an admin) via `POST /v1/sessions/{id}/share`.

## Route matrix

| Route | Auth | Authorization rule |
|-------|------|--------------------|
| `GET /health`, `/v1/health` | public | none |
| `/docs`, `/openapi.json` | public | none |
| `/v1/webhooks/*` | public | signature validation per provider |
| `POST /v1/sessions` | required | not `readonly` |
| `GET /v1/sessions` | required | filtered to visible sessions (admin: whole org) |
| `GET /v1/sessions/{id}` | required | owner / shared / collaborator / admin |
| `PATCH /v1/sessions/{id}` | required | visible + not `readonly` |
| `POST /v1/sessions/{id}/share` | required | owner or admin |
| `DELETE /v1/sessions/{id}` | required | owner or admin |
| `POST /v1/sessions/{id}/turns` | required | visible + not `readonly` |
| `DELETE /v1/sessions/{id}/turns` | required | visible + not `readonly` |
| `POST /v1/sessions/{id}/questions/*`, `/approvals/*` | required | visible + not `readonly` |
| `POST /v1/sessions/cleanup` | required | `admin` (or dev bypass) |
| `GET /v1/models`, `/v1/agents` | required | any authenticated user |
| `POST /v1/agents` | required | any authenticated user (**gap**, see below) |
| `WS /v1/sessions/{id}/ws` | token query param | **gap**, see below |

## Known gaps (tracked for the public release)

1. **WebSocket** `/v1/sessions/{id}/ws` does not yet validate the `token` or the session ACL; the HTTP middleware does not cover WebSockets.
2. **`POST /v1/agents`** is not restricted to `admin`; personas are global, not per-organization.
3. Session store in `main.py` is the in-memory store; the PostgreSQL store with RLS must be wired for production.
4. Organization scoping on single-session lookups relies on RLS under PostgreSQL only.
5. `/v1/sessions/cleanup` only purges `default_org`.

Tests: `tests/unit/test_checklist_and_access.py`.

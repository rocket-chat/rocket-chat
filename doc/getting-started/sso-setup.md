# Single Sign-On (SSO) & OIDC Setup Guide

This guide details how to configure **Single Sign-On (SSO)** across the **Frontend Web Cockpit** (via NextAuth) and the **Backend Control Plane** (via FastAPI RS256 JWT middleware).

Rocket Chat supports industry-standard **OpenID Connect (OIDC)** and OAuth2 providers, including:
- **Okta**
- **Microsoft Entra ID (Azure AD)**
- **Auth0**
- **Keycloak**
- **GitHub OAuth**
- **Google Workspace**

---

## 1. High-Level Architecture

```mermaid
flowchart TD
    User["Developer Browser"] -->|1. Sign In| Web["Next.js Cockpit (apps/web)"]
    Web -->|2. Authorize Code Flow| IdP["Corporate Identity Provider\n(Okta, Azure AD, Auth0, Keycloak)"]
    IdP -->|3. Issue ID Token & Access Token| Web
    Web -->|4. Authenticated REST & WebSocket\n(Bearer / Query Token)| API["FastAPI Control Plane (apps/api)"]
    API -->|5. Verify RS256 via JWKS| IdP
    API -->|6. Enforce Tenant RLS Context| DB[("PostgreSQL with Row-Level Security")]
```

1. **Frontend Authentication (`apps/web`):** NextAuth handles user authentication against your IdP using OpenID Connect or OAuth2, minting a secure session JWT.
2. **Backend Protection (`apps/api`):** The `AuthMiddleware` verifies RS256 JWT signatures against the IdP's JSON Web Key Set (`OIDC_JWKS_URL`) or an offline static public key (`OIDC_PUBLIC_KEY`).
3. **Tenant Context & RLS:** Verified token claims (`org_id`, `user_id`, `roles`) are injected into the request state, isolating database sessions and sandboxes per organization.

---

## 2. Setting Up Corporate OIDC (Okta, Azure AD, Auth0, Keycloak)

### Step 1: Register Application in Identity Provider

Create an OpenID Connect application in your IdP console with the following settings:

| Setting | Value |
| :--- | :--- |
| **Application Type** | Web Application |
| **Grant Types** | Authorization Code with PKCE |
| **Redirect URI (Callback)** | `https://<your-rocket-domain>/api/auth/callback/oidc` |
| **Scopes** | `openid`, `email`, `profile` |
| **Token Claims (Custom)** | Add `org_id` and optional `roles` to ID/Access tokens |

---

### Step 2: Configure Environment Variables

#### Frontend Environment (`apps/web`):
```bash
# NextAuth Core
NEXTAUTH_URL="https://rocket.company.com"
NEXTAUTH_SECRET="generate-a-strong-32-char-secret-openssl-rand-hex-32"

# Corporate OIDC Provider
OIDC_ISSUER="https://auth.company.com"                  # Or https://dev-xxx.okta.com, https://login.microsoftonline.com/<tenant>/v2.0
OIDC_CLIENT_ID="your-client-id"
OIDC_CLIENT_SECRET="your-client-secret"
```

#### Backend Environment (`apps/api`):
```bash
# OIDC Token Verification
OIDC_ISSUER="https://auth.company.com"
OIDC_AUDIENCE="your-client-id"                          # Or custom audience like "rocket-chat"
OIDC_JWKS_URL="https://auth.company.com/.well-known/jwks.json"

# Production Mode (Disables local developer mock bypass)
DEV_AUTH_BYPASS="false"
```

---

## 3. Setting Up GitHub OAuth

If your organization standardizes on GitHub Organization memberships:

### Step 1: Create GitHub OAuth App
1. Navigate to **GitHub Organization Settings** > **Developer settings** > **OAuth Apps** > **New OAuth App**.
2. **Application name:** `Rocket Chat Cockpit`
3. **Homepage URL:** `https://rocket.company.com`
4. **Authorization callback URL:** `https://rocket.company.com/api/auth/callback/github`

### Step 2: Configure Variables
```bash
# Frontend
NEXTAUTH_URL="https://rocket.company.com"
NEXTAUTH_SECRET="generate-a-strong-32-char-secret"
GITHUB_CLIENT_ID="gh-client-id"
GITHUB_CLIENT_SECRET="gh-client-secret"

# Backend
# If using GitHub App token validation or direct JWT bearer verification
DEV_AUTH_BYPASS="false"
```

---

## 4. Deploying SSO via Kubernetes Helm Chart

In production Kubernetes deployments, configure SSO directly in your Helm values (`prod-values.yaml`) or link them to external secrets:

```yaml
global:
  domain: "rocket.company.com"

frontend:
  auth:
    provider: "oidc" # or "github"
    nextauthUrl: "https://rocket.company.com"
    existingSecret: "corporate-sso-secrets"
    secretKey: "NEXTAUTH_SECRET"
    clientIdKey: "OIDC_CLIENT_ID"
    clientSecretKey: "OIDC_CLIENT_SECRET"

backend:
  auth:
    devAuthBypass: false
    oidcIssuer: "https://auth.company.com"
    oidcAudience: "rocket-chat"
    oidcJwksUrl: "https://auth.company.com/.well-known/jwks.json"
```

If using Kubernetes Secrets directly:
```yaml
apiVersion: v1
kind: Secret
metadata:
  name: corporate-sso-secrets
  namespace: rocket-chat
type: Opaque
stringData:
  NEXTAUTH_SECRET: "strong-random-32-characters"
  OIDC_CLIENT_ID: "your-client-id"
  OIDC_CLIENT_SECRET: "your-client-secret"
```

---

## 5. Token Claims & Role-Based Access Control (RBAC)

When users authenticate via OIDC, the backend inspects token claims:

| Claim | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| `sub` | string | Yes | Unique user identifier in your IdP. |
| `email` | string | Yes | Developer's corporate email address. |
| `org_id` | string | Recommended | Organization / tenant ID. Defaults to `default_org` if omitted. |
| `roles` | list | Optional | List of roles (e.g. `["developer", "admin"]`). |

### Admin Role Privileges
Users carrying the `"admin"` role in their claims are authorized to:
- Mutate organization-wide policies via `PUT /v1/admin/settings/{domain}`.
- Lock settings keys to prevent user overrides.
- Manage team-wide API credentials and quota caps.

---

## 6. Local Offline Development Mode

For local development or air-gapped environments without an active identity provider:
1. Leave `OIDC_ISSUER`, `GITHUB_CLIENT_ID`, and `OIDC_JWKS_URL` unset.
2. Ensure `DEV_AUTH_BYPASS="true"` is set in the backend environment.
3. Access the Cockpit at `http://localhost:3000/login` and use the **Local Developer Access** credentials form (e.g. Username: `developer`, Org: `default_org`).

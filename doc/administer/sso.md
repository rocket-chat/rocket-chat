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
    User["Developer Browser"] -->|1. Sign In / Click SSO| Web["Next.js Cockpit (apps/web)"]
    Web -->|2. Redirect to /api/auth/{provider}/login| API["FastAPI Control Plane (apps/api)"]
    API -->|3. Authorize Code Flow| IdP["Corporate Identity Provider\n(Okta, Azure AD, Auth0, Google, GitHub)"]
    IdP -->|4. Issue Code to /api/auth/{provider}/callback| API
    API -->|5. Provision UserModel & OrganizationModel| DB[("PostgreSQL Database")]
    API -->|6. Set HTTP-Only Session Cookie & Redirect /| User
    Web -->|7. Query /api/auth/me| API
```

1. **Unified Backend Authentication (`apps/api`):** The FastAPI Control Plane manages the entire OAuth 2.0 and OIDC lifecycle (`/api/auth/*`), issues secure signed HTTP-only session cookies (`rocket_session`), and automatically persists user accounts in PostgreSQL.
2. **Atomic Multi-Tenant Identity:** Users logging in via SSO or linking accounts are automatically provisioned with a matching `UserModel` and `OrganizationModel`, guaranteeing foreign key integrity for credentials, sessions, and sandboxes.
3. **Frontend Integration (`apps/web`):** The Next.js frontend acts as a clean, lightweight client using the `useAuth()` hook to consume `/api/auth/me`.

---

## 2. Setting Up Corporate OIDC (Okta, Azure AD, Auth0, Keycloak)

### Step 1: Register Application in Identity Provider

Create an OpenID Connect application in your IdP console with the following settings:

| Setting | Value |
| :--- | :--- |
| **Application Type** | Web Application |
| **Grant Types** | Authorization Code with PKCE |
| **Redirect URI (Callback)** | `https://<your-rocket-domain>/api/auth/oidc/callback` |
| **Scopes** | `openid`, `email`, `profile` |
| **Token Claims (Custom)** | Add `org_id` and optional `roles` to ID/Access tokens |

---

### Step 2: Configure Environment Variables

#### Backend & Platform Environment:
```bash
# Session Secret
SESSION_SECRET="generate-a-strong-32-char-secret-openssl-rand-hex-32"

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

## 3. Setting Up Google Workspace SSO & Domain Restriction

Rocket Chat supports native Google Workspace authentication with domain verification directly configurable from the UI.

### Step 1: Create OAuth Credentials in Google Cloud Console
1. Navigate to **Google Cloud Console** > **APIs & Services** > **OAuth consent screen**.
2. Select **User Type: Internal** (this ensures only accounts within your Google Workspace organization can authenticate).
3. Under **Credentials** > **Create Credentials** > **OAuth client ID**:
   - **Application type:** `Web application`
   - **Authorized redirect URI:** `https://<your-rocket-domain>/api/auth/callback/google`
4. Copy the generated **Client ID** and **Client Secret**.

### Step 2: Configure Environment Variables
```bash
# Frontend (apps/web)
NEXTAUTH_URL="https://rocket.company.com"
NEXTAUTH_SECRET="generate-a-strong-32-char-secret"
GOOGLE_CLIENT_ID="<your-client-id>.apps.googleusercontent.com"
GOOGLE_CLIENT_SECRET="<your-client-secret>"

# Optional: Host Domain hint to pre-filter Google account chooser
GOOGLE_ALLOWED_DOMAIN="company.com"

# Backend (apps/api)
OIDC_ISSUER="https://accounts.google.com"
OIDC_AUDIENCE="<your-client-id>.apps.googleusercontent.com"
OIDC_JWKS_URL="https://www.googleapis.com/oauth2/v3/certs"
DEV_AUTH_BYPASS="false"
```

### Step 3: Configure Domain Restriction in Organization Settings UI
Administrators can enforce and adjust domain restrictions at runtime without redeploying:
1. Navigate to **Settings** > **Organization** > **General & Compliance** (`/settings/org`).
2. Locate the **SSO & Corporate Email Domain Allowlist** card.
3. Toggle **Enforce Domain Matching**.
4. Enter your permitted corporate email domains (e.g. `company.com, birota.io`).
5. Click **Save Policies**. Any attempt to sign in with an email from an unlisted domain will be rejected by the NextAuth sign-in callback.

---

## 4. Setting Up GitHub OAuth

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
DEV_AUTH_BYPASS="false"
```

---

## 5. Deploying SSO via Kubernetes Helm Chart

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

### Initial Administrator Bootstrapping
When launching Rocket Chat for the first time, administrators can be bootstrapped seamlessly through two mechanisms:

1. **Zero-Configuration First User Rule:**
   If the PostgreSQL database contains no users, the very first user who signs in (via Google, GitHub, or OIDC) is automatically provisioned as an **Administrator** (`role="admin"` with roles `["admin", "owner", "developer"]`).
2. **Explicit Administrator Emails (`INITIAL_ADMIN_EMAIL` & `ADMIN_EMAILS`):**
   You can specify administrator emails via environment variables or Helm values:
   ```bash
   INITIAL_ADMIN_EMAIL="admin@company.com"
   ADMIN_EMAILS="lead@company.com, ops@company.com"
   ```
   Any user signing in whose email matches `INITIAL_ADMIN_EMAIL` or is included in `ADMIN_EMAILS` is automatically granted the `"admin"` role, and their account in PostgreSQL is promoted if they were previously created as a standard developer.

---

## 6. Local Offline Development Mode

For local development or air-gapped environments without an active identity provider:
1. Leave `OIDC_ISSUER`, `GITHUB_CLIENT_ID`, and `OIDC_JWKS_URL` unset.
2. Set `DEV_AUTH_BYPASS="true"` (anonymous admin, honours tenant headers) or just `ROCKET_DEV_LOGIN="true"` (login form only, server-assigned roles) in the backend environment. Neither works when `ENVIRONMENT=production`.
3. Access the Cockpit at `http://localhost:3000/login` and use the **Local Developer Access** credentials form (e.g. Username: `developer`, Org: `default_org`).

---

## Password sign-in (no identity provider)

A self-hosted install without Okta, Entra ID, Google or GitHub can use built-in accounts. Enable it with
`LOCAL_AUTH_ENABLED=true` (Helm: `auth.local.enabled`); it needs the PostgreSQL store.

1. **First administrator.** Set `INITIAL_ADMIN_EMAIL` and `INITIAL_ADMIN_PASSWORD` (at least 12 characters). On
   start the platform creates that account once; an existing account is never modified, so leaving the variable
   in place cannot reset a password that was later changed.
2. **Members.** Administrators create members (`POST /v1/admin/users`) and receive a random one-time password to
   hand over; the member must choose their own at first sign-in. `POST /v1/admin/users/{id}/reset-password`
   issues a new one and unlocks the account.
3. **Storage and policy.** Only an argon2id hash is stored. Passwords are 12-128 characters and must not contain
   the e-mail address.
4. **Guessing is bounded.** Five failed attempts lock the account for 15 minutes (the counter is in the database,
   so every replica agrees; a reset unlocks), and one source address is limited to 20 attempts a minute. A wrong
   password and an unknown e-mail give the same response and take the same time, so accounts cannot be
   enumerated. Failures and successes are written to the audit log (`login` events).
5. **Coexists with SSO.** SSO members have no password hash and cannot use the password door.


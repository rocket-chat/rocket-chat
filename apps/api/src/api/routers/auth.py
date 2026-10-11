"""Native backend authentication router handling OAuth 2.0 / OIDC flows, login, callback, and session lifecycle."""

import logging
import os
import secrets
import urllib.parse
from dataclasses import dataclass
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Query, Request, Response, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from api.auth import oauth_state
from api.auth.context import RequestContext
from api.auth.identity import dev_identity_enabled, is_production, trust_proxy_headers
from api.auth.local import (
    MAX_PASSWORD_LENGTH,
    AccountLocked,
    AddressThrottle,
    LocalAccounts,
    LoginFailed,
    WeakPassword,
)
from api.auth.oidc import OidcConfig, OidcError, begin_login, complete_login
from api.auth.session_manager import (
    clear_session_cookie,
    create_session_token,
    extract_session_context,
    set_session_cookie,
)
from api.auth.sso_policy import default_org_id, domain_permitted, map_org
from api.db.models import OrganizationModel, UserModel
from api.db.session import DatabaseSessionManager
from api.services.audit import AuditService
from api.services.credentials import store_user_credential
from api.services.git_credentials import expiring_token_metadata
from api.services.settings import load_org_config

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["authentication"])

STATE_COOKIE_NAME = "rocket_oauth_state"


class DevLoginRequest(BaseModel):
    """Payload for offline or developer sign-in."""

    username: str = "developer"
    org_id: str = "default_org"
    roles: list[str] = ["developer", "admin"]


class AuthProvidersResponse(BaseModel):
    """Configured authentication providers for the frontend cockpit."""

    google: bool
    github: bool
    oidc: bool
    devLogin: bool
    local: bool = False


class LocalLoginRequest(BaseModel):
    email: str = Field(..., max_length=255)
    password: str = Field(..., max_length=MAX_PASSWORD_LENGTH)


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(..., max_length=MAX_PASSWORD_LENGTH)
    new_password: str = Field(..., max_length=MAX_PASSWORD_LENGTH)


class UserProfileResponse(BaseModel):
    """Authenticated user profile returned by /api/auth/me."""

    id: str
    user_id: str
    email: str
    name: str | None
    org_id: str
    roles: list[str]
    groups: list[str] = []


async def _sso_allowed(db_mgr: DatabaseSessionManager | None, org_id: str, email: str) -> bool:
    """Apply the deployment and per-organization sign-in domain policy."""
    org_general, _ = await load_org_config(db_mgr, org_id, "general")
    return domain_permitted(email, org_general)


def dev_login_enabled() -> bool:
    """Developer login: explicit local opt-in only, never in production."""
    if is_production():
        return False
    return dev_identity_enabled() or os.getenv("ROCKET_DEV_LOGIN", "").strip().lower() in (
        "true",
        "1",
        "yes",
    )


def _get_origin(request: Request) -> str:
    """Determine canonical public origin (scheme + host)."""
    explicit = os.getenv("NEXTAUTH_URL") or os.getenv("APP_URL") or os.getenv("FRONTEND_URL")
    if explicit:
        return explicit.rstrip("/")

    forwarded_proto = request.headers.get("x-forwarded-proto") or request.url.scheme
    forwarded_host = request.headers.get("x-forwarded-host") or request.headers.get("host")
    if forwarded_host:
        return f"{forwarded_proto}://{forwarded_host}".rstrip("/")

    return str(request.base_url).rstrip("/")


def _with_query(path: str, key: str, value: str) -> str:
    """Append a query parameter to a relative redirect path, preserving existing params."""
    separator = "&" if "?" in path else "?"
    return f"{path}{separator}{urllib.parse.urlencode({key: value})}"


def is_admin_email(email: str) -> bool:
    """Check if an email is designated as an administrator via environment variables."""
    if not email:
        return False
    norm_email = email.strip().lower()
    initial_admin = os.getenv("INITIAL_ADMIN_EMAIL", "").strip().lower()
    if initial_admin and norm_email == initial_admin:
        return True
    admin_emails = [
        e.strip().lower() for e in os.getenv("ADMIN_EMAILS", "").split(",") if e.strip()
    ]
    return norm_email in admin_emails


async def _ensure_db_user_and_org(
    db_mgr: DatabaseSessionManager | None,
    user_id: str,
    org_id: str,
    email: str,
    name: str,
    role: str | None = None,
) -> list[str]:
    """Ensure OrganizationModel and UserModel exist in PostgreSQL and return effective roles.

    Determines roles through the following hierarchy:
    1. Direct admin configuration via INITIAL_ADMIN_EMAIL or ADMIN_EMAILS env vars.
    2. Bootstrap rule: First user in the database is automatically granted the admin role.
    3. Existing role persisted on UserModel.
    4. Default role provided (or 'developer').
    """
    admin_roles = ["admin", "owner", "developer"]
    matches_admin_email = is_admin_email(email)

    if not db_mgr or not db_mgr.session_factory:
        if matches_admin_email:
            return admin_roles
        if role:
            return admin_roles if role in ("admin", "owner") else [role]
        return ["developer"]

    try:
        async with db_mgr.session(tenant_org_id=org_id) as session:
            org = await session.get(OrganizationModel, org_id)
            if not org:
                org = OrganizationModel(
                    id=org_id,
                    name=org_id.replace("_", " ").title(),
                    slug=org_id.replace("_", "-").lower(),
                )
                session.add(org)
                await session.flush()

            user = await session.get(UserModel, user_id)
            if user:
                if matches_admin_email and user.role not in ("admin", "owner"):
                    user.role = "admin"
                    await session.flush()

                if user.role in ("admin", "owner"):
                    return admin_roles
                return [user.role]

            count_res = await session.execute(
                select(func.count(UserModel.id)).where(UserModel.id != "dev_user")
            )
            total_users = count_res.scalar_one_or_none() or 0
            is_first_user = total_users == 0

            if matches_admin_email or is_first_user or (role in ("admin", "owner")):
                assigned_role = "admin"
                effective_roles = admin_roles
            else:
                assigned_role = role or "developer"
                effective_roles = [assigned_role]

            user = UserModel(
                id=user_id,
                org_id=org_id,
                email=email,
                name=name,
                role=assigned_role,
            )
            session.add(user)
            await session.flush()
            return effective_roles
    except Exception as err:
        logger.warning("Failed ensuring user/org in database: %s", err)
        if matches_admin_email:
            return admin_roles
        if role:
            return admin_roles if role in ("admin", "owner") else [role]
        return ["developer"]


@router.get("/providers", response_model=AuthProvidersResponse)
@router.get("/configured-providers", response_model=AuthProvidersResponse)
async def get_configured_providers(request: Request) -> AuthProvidersResponse:
    """Return available SSO providers configured in environment."""
    has_google = bool(os.getenv("GOOGLE_CLIENT_ID") and os.getenv("GOOGLE_CLIENT_SECRET"))
    has_github = bool(os.getenv("GITHUB_CLIENT_ID") and os.getenv("GITHUB_CLIENT_SECRET"))
    has_oidc = bool(
        os.getenv("OIDC_ISSUER") and os.getenv("OIDC_CLIENT_ID") and os.getenv("OIDC_CLIENT_SECRET")
    )

    allow_dev = dev_login_enabled()

    return AuthProvidersResponse(
        google=has_google,
        github=has_github,
        oidc=has_oidc,
        devLogin=allow_dev,
        local=LocalAccounts(getattr(request.app.state, "db_manager", None)).available,
    )


@router.get("/me", response_model=UserProfileResponse)
async def get_current_user_profile(request: Request) -> UserProfileResponse:
    """Return identity and tenancy claims of currently authenticated session."""
    ctx = getattr(request.state, "context", None)
    if not isinstance(ctx, RequestContext):
        ctx = extract_session_context(request)

    if not ctx:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized: No active user session",
        )

    return UserProfileResponse(
        id=ctx.user_id,
        user_id=ctx.user_id,
        email=ctx.email,
        name=ctx.name or ctx.user_id,
        org_id=ctx.org_id,
        roles=ctx.roles,
        groups=ctx.groups,
    )


@router.post("/dev-login")
async def dev_login(req: DevLoginRequest, response: Response, request: Request) -> dict[str, Any]:
    """Offline development login setting session cookie directly."""
    if not dev_login_enabled():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Developer login is disabled",
        )

    user_id = (
        f"usr_{req.username.lower().replace(' ', '_')}"
        if not req.username.startswith("usr_")
        else req.username
    )
    # Developer login is a local-only convenience (DEV_AUTH_BYPASS / ROCKET_DEV_LOGIN, never
    # in production). The org stays the development org unless the explicit bypass lets a
    # developer pick one; roles are always server-assigned unless the bypass is on.
    org_id = (req.org_id or "default_org") if dev_identity_enabled() else "default_org"
    email = f"{req.username}@rocket-chat.internal"
    name = req.username.title()

    db_mgr: DatabaseSessionManager | None = getattr(request.app.state, "db_manager", None)
    initial_role = (
        "admin"
        if dev_identity_enabled() and any(r in ("admin", "owner") for r in req.roles)
        else "developer"
    )
    assigned_roles = await _ensure_db_user_and_org(
        db_mgr, user_id, org_id, email, name, role=initial_role
    )

    token = create_session_token(
        user_id=user_id,
        org_id=org_id,
        email=email,
        name=name,
        roles=(req.roles if req.roles and dev_identity_enabled() else assigned_roles),
    )
    set_session_cookie(response, token)

    return {
        "status": "authenticated",
        "user_id": user_id,
        "org_id": org_id,
        "token": token,
    }


def _client_address(request: Request) -> str:
    """The caller's address; behind a trusted proxy, the first ``X-Forwarded-For`` hop."""
    if trust_proxy_headers():
        forwarded = request.headers.get("x-forwarded-for", "")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


_login_throttle = AddressThrottle()
GENERIC_LOGIN_FAILURE = "Incorrect e-mail or password."


@router.post("/local/login")
async def local_login(
    req: LocalLoginRequest, response: Response, request: Request
) -> dict[str, Any]:
    """Sign in with an e-mail and password (self-hosted installs without an identity provider)."""
    db_mgr: DatabaseSessionManager | None = getattr(request.app.state, "db_manager", None)
    accounts = LocalAccounts(db_mgr)
    if not accounts.available:
        raise HTTPException(status_code=403, detail="Password sign-in is not enabled")
    address = _client_address(request)
    if not _login_throttle.hit(address):
        raise HTTPException(
            status_code=429,
            detail="Too many sign-in attempts; wait a minute.",
            headers={"Retry-After": "60"},
        )

    audit = AuditService(db_mgr)
    try:
        who = await accounts.authenticate(req.email, req.password)
    except AccountLocked as locked:
        await audit.record(
            "login",
            org_id=default_org_id(),
            result_summary="refused: account locked",
            metadata={"address": address, "email": req.email.strip().lower()[:255]},
        )
        raise HTTPException(
            status_code=429,
            detail="This account is temporarily locked after repeated failed attempts.",
            headers={"Retry-After": str(locked.retry_after_seconds)},
        ) from locked
    except LoginFailed as failed:
        await audit.record(
            "login",
            org_id=default_org_id(),
            result_summary="failed",
            metadata={"address": address, "email": req.email.strip().lower()[:255]},
        )
        raise HTTPException(status_code=401, detail=GENERIC_LOGIN_FAILURE) from failed

    roles = await _ensure_db_user_and_org(
        db_mgr, who.user_id, who.org_id, who.email, who.name, who.role
    )
    token = create_session_token(
        user_id=who.user_id, org_id=who.org_id, email=who.email, name=who.name, roles=roles
    )
    set_session_cookie(response, token)
    await audit.record(
        "login",
        org_id=who.org_id,
        actor_user_id=who.user_id,
        result_summary="signed in with a password",
        metadata={"address": address},
    )
    return {
        "status": "authenticated",
        "user_id": who.user_id,
        "org_id": who.org_id,
        "must_change_password": who.must_change_password,
    }


@router.post("/local/password")
async def change_local_password(req: ChangePasswordRequest, request: Request) -> dict[str, str]:
    """Change the signed-in member's password (also clears an administrator-issued one)."""
    ctx = extract_session_context(request)
    if ctx is None:
        raise HTTPException(status_code=401, detail="Sign in first")
    db_mgr: DatabaseSessionManager | None = getattr(request.app.state, "db_manager", None)
    try:
        await LocalAccounts(db_mgr).change_password(
            ctx.user_id, req.current_password, req.new_password
        )
    except LoginFailed as failed:
        raise HTTPException(
            status_code=400, detail="The current password is incorrect."
        ) from failed
    except WeakPassword as weak:
        raise HTTPException(status_code=422, detail=str(weak)) from weak
    await AuditService(db_mgr).record(
        "credential_change",
        org_id=ctx.org_id,
        actor_user_id=ctx.user_id,
        result_summary="changed their password",
    )
    return {"status": "changed"}


@router.post("/logout")
async def logout(response: Response) -> dict[str, str]:
    """Terminate current user session by clearing the cookie."""
    clear_session_cookie(response)
    return {"status": "logged_out"}


# ============================================================================
# OAUTH PROVIDERS (GOOGLE, GITHUB, OIDC)
# ============================================================================


@router.get("/{provider}/login")
async def oauth_login(
    provider: str,
    request: Request,
    redirect_uri: str | None = Query(default=None),
    link_only: bool = Query(default=False),
) -> Response:
    """Initiate OAuth 2.0 / OIDC authorization code flow."""
    origin = _get_origin(request)
    state_payload = {
        "provider": provider,
        "nonce": secrets.token_urlsafe(16),
        "link_only": link_only,
        "return_to": redirect_uri or "/",
    }

    # If linking account, preserve current user identity in state
    if link_only:
        ctx = extract_session_context(request)
        if not ctx:
            return RedirectResponse(url=f"{origin}/login?error=SessionRequired")
        state_payload["user_id"] = ctx.user_id
        state_payload["org_id"] = ctx.org_id

    state_str = oauth_state.sign(state_payload)
    flow_secrets: dict[str, str] = {}

    callback_url = f"{origin}/api/auth/{provider}/callback"

    auth_url = ""
    if provider == "google":
        client_id = os.getenv("GOOGLE_CLIENT_ID")
        if not client_id:
            raise HTTPException(status_code=500, detail="Google OAuth is not configured")
        scope = "openid email profile"
        params = {
            "client_id": client_id,
            "redirect_uri": callback_url,
            "response_type": "code",
            "scope": scope,
            "state": state_str,
            "access_type": "offline",
            "prompt": "select_account",
        }
        allowed_domain = os.getenv("GOOGLE_ALLOWED_DOMAIN")
        if allowed_domain:
            params["hd"] = allowed_domain
        auth_url = f"https://accounts.google.com/o/oauth2/v2/auth?{urllib.parse.urlencode(params)}"

    elif provider == "github":
        client_id = os.getenv("GITHUB_CLIENT_ID")
        if not client_id:
            raise HTTPException(status_code=500, detail="GitHub OAuth is not configured")
        scope = "read:user user:email repo"
        params = {
            "client_id": client_id,
            "redirect_uri": callback_url,
            "scope": scope,
            "state": state_str,
        }
        auth_url = f"https://github.com/login/oauth/authorize?{urllib.parse.urlencode(params)}"

    elif provider == "oidc":
        oidc_config = OidcConfig.from_env()
        if oidc_config is None:
            raise HTTPException(status_code=500, detail="OIDC SSO is not configured")
        try:
            authorization = await begin_login(
                oidc_config, redirect_uri=callback_url, state=state_str
            )
        except OidcError as err:
            logger.error("OIDC login could not start: %s", err)
            return RedirectResponse(url=f"{origin}/login?error=OidcUnavailable")
        auth_url = authorization.url
        flow_secrets = {"cv": authorization.code_verifier, "nonce": authorization.nonce}

    else:
        raise HTTPException(status_code=400, detail=f"Unsupported provider: {provider}")

    response = RedirectResponse(url=auth_url, status_code=status.HTTP_302_FOUND)
    is_prod = (
        os.getenv("ENVIRONMENT", "").lower() == "production"
        or os.getenv("NODE_ENV", "") == "production"
    )
    response.set_cookie(
        key=STATE_COOKIE_NAME,
        value=oauth_state.sign({"state": state_str, **flow_secrets}),
        max_age=600,
        httponly=True,
        secure=is_prod,
        samesite="lax",
    )
    return response


@dataclass(frozen=True)
class _CallbackFlow:
    """Everything a provider handler needs about one verified OAuth callback."""

    provider: str
    origin: str
    code: str
    flow: dict[str, Any]
    state_data: dict[str, Any]
    db_mgr: DatabaseSessionManager | None

    @property
    def callback_url(self) -> str:
        return f"{self.origin}/api/auth/{self.provider}/callback"

    @property
    def is_link_only(self) -> bool:
        return bool(self.state_data.get("link_only", False))

    @property
    def target_redirect(self) -> str:
        return str(self.state_data.get("return_to") or "/")

    def login_error(self, code: str) -> Response:
        return RedirectResponse(f"{self.origin}/login?error={code}")

    def signed_in(
        self, *, user_id: str, org_id: str, email: str, name: str, roles: list[str]
    ) -> Response:
        """Redirect to the requested page with a fresh session cookie."""
        session_token = create_session_token(
            user_id=user_id, org_id=org_id, email=email, name=name, roles=roles
        )
        response = RedirectResponse(f"{self.origin}{self.target_redirect}", status_code=302)
        set_session_cookie(response, session_token)
        response.delete_cookie(STATE_COOKIE_NAME)
        return response


@router.get("/{provider}/callback")
async def oauth_callback(
    provider: str,
    request: Request,
    code: str | None = Query(default=None),
    state: str | None = Query(default=None),
    error: str | None = Query(default=None),
) -> Response:
    """Handle OAuth 2.0 authorization callback, exchange token, provision user, and set session."""
    origin = _get_origin(request)
    failure_path = "/settings/user/git" if provider == "github" else "/login"

    if error or not code or not state:
        err_msg = error or "MissingAuthorizationCode"
        return RedirectResponse(f"{origin}{failure_path}?error={urllib.parse.quote(err_msg)}")

    try:
        flow_cookie = oauth_state.verify(request.cookies.get(STATE_COOKIE_NAME) or "")
        if flow_cookie.get("state") != state:
            raise ValueError("State does not belong to this browser")
        state_data: dict[str, Any] = oauth_state.verify(state)
    except ValueError:
        logger.warning("OAuth state verification failed")
        return RedirectResponse(f"{origin}{failure_path}?error=InvalidOAuthState")
    if state_data.get("provider") != provider:
        return RedirectResponse(f"{origin}{failure_path}?error=InvalidOAuthState")

    flow = _CallbackFlow(
        provider=provider,
        origin=origin,
        code=code,
        flow=flow_cookie,
        state_data=state_data,
        db_mgr=getattr(request.app.state, "db_manager", None),
    )
    try:
        return await _complete_provider_login(flow)
    except Exception as err:
        logger.error("OAuth callback processing error: %s", err, exc_info=True)
        if flow.is_link_only:
            target_path = state_data.get("return_to") or "/settings/user/git"
            return RedirectResponse(f"{origin}{_with_query(target_path, 'error', str(err))}")
        return RedirectResponse(f"{origin}/login?error={urllib.parse.quote(str(err))}")


async def _complete_provider_login(flow: _CallbackFlow) -> Response:
    if flow.provider == "google":
        async with httpx.AsyncClient(timeout=15.0) as client:
            return await _complete_google(flow, client)
    if flow.provider == "github":
        async with httpx.AsyncClient(timeout=15.0) as client:
            return await _complete_github(flow, client)
    if flow.provider == "oidc":
        return await _complete_oidc(flow)
    return flow.login_error("UnsupportedProvider")


async def _complete_google(flow: _CallbackFlow, client: httpx.AsyncClient) -> Response:
    token_res = await client.post(
        "https://oauth2.googleapis.com/token",
        data={
            "code": flow.code,
            "client_id": os.getenv("GOOGLE_CLIENT_ID"),
            "client_secret": os.getenv("GOOGLE_CLIENT_SECRET"),
            "redirect_uri": flow.callback_url,
            "grant_type": "authorization_code",
        },
    )
    if token_res.status_code != 200:
        raise ValueError(f"Google token exchange failed: {token_res.text}")
    access_token = token_res.json().get("access_token")

    user_res = await client.get(
        "https://www.googleapis.com/oauth2/v2/userinfo",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    if user_res.status_code != 200:
        raise ValueError("Failed fetching Google user profile")
    user_info = user_res.json()

    email = user_info.get("email", "").lower()
    name = user_info.get("name") or email.split("@")[0]
    user_id = f"usr_{email.replace('@', '_').replace('.', '_')}" if email else user_info.get("id")
    return await _sign_in_sso_user(flow, user_id=user_id, email=email, name=name)


async def _sign_in_sso_user(
    flow: _CallbackFlow,
    *,
    user_id: str,
    email: str,
    name: str,
    org_claim: str | None = None,
) -> Response:
    """Apply the sign-in domain policy, provision the member and start their session."""
    org_id = map_org(email, org_claim)
    if not await _sso_allowed(flow.db_mgr, org_id, email):
        return flow.login_error("AccessDenied")
    roles = await _ensure_db_user_and_org(flow.db_mgr, user_id, org_id, email, name)
    return flow.signed_in(user_id=user_id, org_id=org_id, email=email, name=name, roles=roles)


@dataclass(frozen=True)
class _GithubProfile:
    access_token: str
    token_data: dict[str, Any]
    user_id: int | None
    username: str
    email: str
    name: str
    avatar_url: str | None
    scopes: list[str]

    @property
    def credential_metadata(self) -> dict[str, Any]:
        return {
            "login": self.username,
            "name": self.name,
            "avatar_url": self.avatar_url,
            "scopes": self.scopes,
            "auth_type": "oauth",
            **expiring_token_metadata(self.token_data),
        }


async def _fetch_github_profile(flow: _CallbackFlow, client: httpx.AsyncClient) -> _GithubProfile:
    token_res = await client.post(
        "https://github.com/login/oauth/access_token",
        headers={"Accept": "application/json"},
        data={
            "client_id": os.getenv("GITHUB_CLIENT_ID"),
            "client_secret": os.getenv("GITHUB_CLIENT_SECRET"),
            "code": flow.code,
            "redirect_uri": flow.callback_url,
        },
    )
    if token_res.status_code != 200:
        raise ValueError("GitHub token exchange failed")
    token_data = token_res.json()
    access_token = token_data.get("access_token")
    if not access_token:
        raise ValueError(token_data.get("error_description", "No access token"))

    user_res = await client.get(
        "https://api.github.com/user",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "Rocket-Chat",
        },
    )
    if user_res.status_code != 200:
        raise ValueError("Failed fetching GitHub user profile")
    gh_user = user_res.json()
    username = gh_user.get("login") or "github-user"
    raw_scopes = user_res.headers.get("x-oauth-scopes", "")
    return _GithubProfile(
        access_token=access_token,
        token_data=token_data,
        user_id=gh_user.get("id"),
        username=username,
        email=(gh_user.get("email") or "").lower(),
        name=gh_user.get("name") or username,
        avatar_url=gh_user.get("avatar_url"),
        scopes=[scope.strip() for scope in raw_scopes.split(",") if scope.strip()],
    )


async def _complete_github(flow: _CallbackFlow, client: httpx.AsyncClient) -> Response:
    profile = await _fetch_github_profile(flow, client)
    if flow.is_link_only:
        return await _link_github_account(flow, profile)

    user_id = (
        f"usr_{profile.email.replace('@', '_').replace('.', '_')}"
        if profile.email
        else f"usr_gh_{profile.user_id}"
    )
    org_id = map_org(profile.email or "")
    if not await _sso_allowed(flow.db_mgr, org_id, profile.email or ""):
        return flow.login_error("AccessDenied")

    email = profile.email or f"{user_id}@rocket.internal"
    roles = await _ensure_db_user_and_org(flow.db_mgr, user_id, org_id, email, profile.name)
    await store_user_credential(
        db_mgr=flow.db_mgr,
        user_id=user_id,
        org_id=org_id,
        provider="github",
        secret=profile.access_token,
        metadata=profile.credential_metadata,
    )
    return flow.signed_in(
        user_id=user_id, org_id=org_id, email=email, name=profile.name, roles=roles
    )


async def _link_github_account(flow: _CallbackFlow, profile: _GithubProfile) -> Response:
    """An already signed-in member attaches their personal GitHub account."""
    link_user_id = flow.state_data.get("user_id")
    return_to = flow.target_redirect
    if not link_user_id:
        return RedirectResponse(
            f"{flow.origin}{_with_query(return_to, 'error', 'MissingUserIdentity')}"
        )
    await store_user_credential(
        db_mgr=flow.db_mgr,
        user_id=link_user_id,
        org_id=flow.state_data.get("org_id", "default_org"),
        provider="github",
        secret=profile.access_token,
        metadata=profile.credential_metadata,
    )
    response = RedirectResponse(
        f"{flow.origin}{_with_query(return_to, 'status', 'linked')}", status_code=302
    )
    response.delete_cookie(STATE_COOKIE_NAME)
    return response


async def _complete_oidc(flow: _CallbackFlow) -> Response:
    oidc_config = OidcConfig.from_env()
    if oidc_config is None:
        raise ValueError("OIDC SSO is not configured")
    try:
        identity = await complete_login(
            oidc_config,
            code=flow.code,
            redirect_uri=flow.callback_url,
            code_verifier=str(flow.flow.get("cv", "")),
            nonce=str(flow.flow.get("nonce", "")),
        )
    except OidcError as err:
        logger.warning("OIDC login rejected: %s", err)
        return flow.login_error("OidcLoginFailed")
    if not identity.email or identity.email_verified is False:
        # An unverified address is not proof of mailbox ownership, and the email decides
        # organization membership.
        return flow.login_error("EmailNotVerified")
    return await _sign_in_sso_user(
        flow,
        user_id=identity.subject,
        email=identity.email,
        name=identity.name,
        org_claim=identity.claims.get("org_id"),
    )

"""Service resolving and injecting Git credentials into running sandbox environments."""

import asyncio
import hashlib
import logging
import os
import shlex
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

import httpx
from git_engine.engine import GitEngine, pick_installation

from api.db.session import DatabaseSessionManager
from api.services.credentials import (
    UserCredentialData,
    decrypt_secret,
    encrypt_secret,
    get_user_credential,
    store_user_credential,
)
from api.services.github_installations import installations_for_org
from api.services.settings import effective_settings
from specifications.interfaces.sandbox import SandboxDriverProtocol

logger = logging.getLogger(__name__)


@dataclass
class GitCredentialBundle:
    """Resolved Git credential bundle ready for machine injection."""

    token: str | None
    mode: str  # "user_token", "app_readonly", "none"
    author_name: str
    author_email: str
    source_details: dict[str, Any] = field(default_factory=dict)
    # Something the member should know about their git access, e.g. an expired link.
    warning: str | None = None


TOKEN_CHECK_TTL_SECONDS = 300
TOKEN_CHECK_TIMEOUT_SECONDS = 5.0
EXPIRED_LINK_WARNING = (
    "Your linked GitHub token is no longer valid (it expired or was revoked), so it was not "
    "given to this sandbox. Reconnect GitHub in Settings > Git & version control."
)
# token digest -> (checked_at, verdict). Never keyed by the token itself.
_token_verdicts: dict[str, tuple[float, bool]] = {}


async def github_token_is_valid(token: str, api_url: str = "https://api.github.com") -> bool:
    """Whether GitHub still accepts ``token``.

    GitHub App user tokens expire after eight hours and a personal token can be revoked, so a
    token that was fine when it was linked can be dead days later. Only an explicit rejection
    (401/403 from ``/user``) counts as invalid: a network failure must not disable a good link.
    The verdict is cached for a few minutes so a burst of turns makes one call.
    """
    digest = hashlib.sha256(token.encode()).hexdigest()
    cached = _token_verdicts.get(digest)
    now = time.monotonic()
    if cached and now - cached[0] < TOKEN_CHECK_TTL_SECONDS:
        return cached[1]
    try:
        async with httpx.AsyncClient(timeout=TOKEN_CHECK_TIMEOUT_SECONDS) as client:
            response = await client.get(
                f"{api_url.rstrip('/')}/user",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
            )
    except httpx.HTTPError as err:
        logger.debug("Could not check the GitHub token (%s); assuming it is valid", err)
        return True
    valid = response.status_code not in (401, 403)
    if len(_token_verdicts) > 256:
        _token_verdicts.clear()
    _token_verdicts[digest] = (now, valid)
    return valid


REFRESH_MARGIN_SECONDS = 120
_refresh_locks: dict[str, asyncio.Lock] = {}


def expiring_token_metadata(token_response: Mapping[str, Any]) -> dict[str, Any]:
    """What to remember about a GitHub App user token so it can be renewed.

    Such tokens live eight hours and come with a single-use refresh token. The refresh token
    is a secret, so it is stored encrypted like the access token itself.
    """
    extra: dict[str, Any] = {}
    expires_in = token_response.get("expires_in")
    if isinstance(expires_in, int | float) and expires_in > 0:
        extra["expires_at"] = int(time.time() + expires_in)
    refresh_token = token_response.get("refresh_token")
    if isinstance(refresh_token, str) and refresh_token:
        extra["refresh_token_enc"] = encrypt_secret(refresh_token)
    return extra


async def refresh_github_user_token(
    db_mgr: DatabaseSessionManager | None,
    credential: UserCredentialData,
    api_url: str = "https://github.com",
) -> UserCredentialData:
    """Renew a linked GitHub App user token that is about to expire (or has).

    Refresh tokens are single use, so renewals for one member are serialised and the stored
    credential is re-read inside the lock: a concurrent turn that already renewed it wins.
    Returns the credential unchanged when it has nothing to renew or GitHub refuses; the
    validity check then reports the link as expired.
    """
    meta = credential.metadata
    expires_at = meta.get("expires_at")
    refresh_enc = meta.get("refresh_token_enc")
    if not refresh_enc or not isinstance(expires_at, int | float):
        return credential
    if time.time() < expires_at - REFRESH_MARGIN_SECONDS:
        return credential

    lock = _refresh_locks.setdefault(credential.user_id, asyncio.Lock())
    async with lock:
        latest = await get_user_credential(
            db_mgr, credential.user_id, "github", org_id=credential.org_id
        )
        if latest and latest.secret != credential.secret:
            return latest
        client_id, client_secret = os.getenv("GITHUB_CLIENT_ID"), os.getenv("GITHUB_CLIENT_SECRET")
        if not (client_id and client_secret):
            return credential
        try:
            async with httpx.AsyncClient(timeout=TOKEN_CHECK_TIMEOUT_SECONDS) as client:
                response = await client.post(
                    f"{api_url.rstrip('/')}/login/oauth/access_token",
                    headers={"Accept": "application/json"},
                    data={
                        "client_id": client_id,
                        "client_secret": client_secret,
                        "grant_type": "refresh_token",
                        "refresh_token": decrypt_secret(str(refresh_enc)),
                    },
                )
            payload = response.json()
        except (httpx.HTTPError, ValueError) as err:
            logger.warning("Could not renew the GitHub token of %s: %s", credential.user_id, err)
            return credential
        new_token = payload.get("access_token")
        if not isinstance(new_token, str) or not new_token:
            logger.warning(
                "GitHub refused to renew the token of %s: %s",
                credential.user_id,
                payload.get("error", "no access_token"),
            )
            return credential
        renewed_meta = {
            key: value
            for key, value in meta.items()
            if key not in ("expires_at", "refresh_token_enc")
        }
        renewed_meta.update(expiring_token_metadata(payload))
        return await store_user_credential(
            db_mgr,
            credential.user_id,
            credential.org_id,
            "github",
            new_token,
            renewed_meta,
        )


def _target_repositories(raw: object) -> list[str] | None:
    """Repository names an App token is limited to; ``None`` means every installed repo."""
    text = str(raw or "all").strip()
    if text.lower() in ("", "all", "*"):
        return None
    return [part.strip().rsplit("/", 1)[-1] for part in text.split(",") if part.strip()]


async def resolve_session_git_credentials(
    user_id: str,
    org_id: str,
    git_engine: GitEngine | None,
    db_mgr: DatabaseSessionManager | None = None,
    user_overrides: dict[str, Any] | None = None,
    repo: str | None = None,
) -> GitCredentialBundle:
    """
    Resolves the appropriate Git credentials for a session workspace:
    1. Priority 1: User's linked GitHub account (OAuth token or PAT).
    2. Priority 2: Ephemeral read-only GitHub App installation token.
    3. Priority 3: Unauthenticated local fallback.
    """
    overrides = user_overrides or {}
    org_github = await effective_settings(db_mgr, org_id, "github")

    # 1. Inspect User Credentials (OAuth / PAT)
    warning: str | None = None
    user_cred = await get_user_credential(db_mgr, user_id, "github", org_id=org_id)
    if user_cred and user_cred.secret:
        user_cred = await refresh_github_user_token(db_mgr, user_cred)
        api_url = git_engine.settings.github_api_url if git_engine else "https://api.github.com"
        if not await github_token_is_valid(user_cred.secret, api_url):
            # A dead token must not win over the App fallback, nor reach the sandbox.
            logger.warning("The linked GitHub token of %s is no longer valid", user_id)
            warning = EXPIRED_LINK_WARNING
            user_cred = None
    if user_cred and user_cred.secret:
        meta = user_cred.metadata
        author_name = (
            overrides.get("git_author_name")
            or overrides.get("full_name")
            or meta.get("name")
            or meta.get("login")
            or "Rocket Developer"
        )
        author_email = (
            overrides.get("git_author_email")
            or meta.get("email")
            or f"{user_id}@rocket-chat.internal"
        )
        return GitCredentialBundle(
            token=user_cred.secret,
            mode="user_token",
            author_name=author_name,
            author_email=author_email,
            source_details={
                "username": meta.get("login"),
                "auth_type": meta.get("auth_type", "oauth"),
                "scopes": meta.get("scopes", []),
            },
        )

    # 2. Inspect GitHub App installation token fallback (Read-Only)
    if git_engine:
        try:
            app_permissions = org_github.get("app_token_permissions") or {"contents": "read"}
            installation_id = pick_installation(
                [
                    (i.installation_id, i.account_login)
                    for i in await installations_for_org(db_mgr, org_id)
                ],
                repo,
            )
            app_token = await git_engine.get_github_access_token(
                repo=repo,
                permissions={str(k): str(v) for k, v in app_permissions.items()},
                repositories=_target_repositories(org_github.get("app_token_target_repositories")),
                installation_id=installation_id,
            )
            if app_token:
                return GitCredentialBundle(
                    token=app_token,
                    mode="app_readonly",
                    author_name=git_engine.settings.bot_author_name,
                    author_email=git_engine.settings.bot_author_email,
                    source_details={
                        "auth_type": "github_app",
                        "permissions": app_permissions,
                    },
                    warning=warning,
                )
        except Exception as ex:
            logger.debug("Failed minting GitHub App fallback token: %s", ex)

    # 3. Unauthenticated local fallback
    fallback_name = overrides.get("git_author_name") or "Rocket Developer"
    fallback_email = overrides.get("git_author_email") or f"{user_id}@rocket-chat.internal"
    return GitCredentialBundle(
        token=None,
        mode="none",
        author_name=fallback_name,
        author_email=fallback_email,
        source_details={"auth_type": "none"},
        warning=warning,
    )


# git asks this helper for credentials; it reads the token from the command's own environment
# so no secret is ever written to the workspace, a dotfile or the container/pod spec.
_CREDENTIAL_HELPER = (
    '!f() { test "$1" = get || exit 0; echo username=x-access-token; '
    'echo "password=${GITHUB_TOKEN:-$GH_TOKEN}"; }; f'
)

# Remove what earlier versions persisted on long-lived workspace volumes.
_LEGACY_CLEANUP = (
    'rm -f "$HOME/.git-credentials" /root/.git-credentials 2>/dev/null; '
    "sed -i '/^export GITHUB_TOKEN=/d;/^export GH_TOKEN=/d' \"$HOME/.bashrc\" 2>/dev/null; "
    "sed -i '/^GITHUB_TOKEN=/d;/^GH_TOKEN=/d' /etc/environment 2>/dev/null; "
    "git config --global --unset-all url.https://github.com/.insteadof 2>/dev/null; true"
)


async def inject_git_credentials_into_sandbox(
    driver: SandboxDriverProtocol | None,
    session_id: str,
    bundle: GitCredentialBundle,
) -> None:
    """Configure git identity and credential access for ``session_id`` without storing secrets.

    The committer identity and a token-less credential helper go into the sandbox's git
    config; the token itself is handed to the driver as per-session environment, supplied to
    each command at execution time and never persisted (see ``SessionEnvMixin``). Called at
    every turn start, so token rotation (GitHub App tokens expire hourly) needs no cleanup.
    """
    if not driver:
        return

    try:
        driver.set_session_env(
            session_id,
            (
                {
                    "GITHUB_TOKEN": bundle.token,
                    "GH_TOKEN": bundle.token,
                    "GIT_TERMINAL_PROMPT": "0",
                }
                if bundle.token
                else {"GIT_TERMINAL_PROMPT": "0"}
            ),
        )
        commands = [
            f"git config --global user.name {shlex.quote(bundle.author_name)}",
            f"git config --global user.email {shlex.quote(bundle.author_email)}",
            "git config --global init.defaultBranch main",
            (
                f"git config --global credential.helper {shlex.quote(_CREDENTIAL_HELPER)}"
                if bundle.token
                else "git config --global --unset-all credential.helper || true"
            ),
            _LEGACY_CLEANUP,
        ]
        result = await driver.exec_command(
            session_id, " && ".join(commands[:4]) + "; " + commands[4]
        )
        if result.exit_code != 0:
            logger.warning(
                "Git sandbox credential setup failed (exit %d): %s", result.exit_code, result.stderr
            )
        else:
            logger.info("Configured git access for session %s (mode=%s)", session_id, bundle.mode)
    except Exception as err:
        logger.warning("Exception configuring git access for session %s: %s", session_id, err)

"""Service resolving and injecting Git credentials into running sandbox environments."""

import asyncio
import base64
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
from api.services.git_signing import MemberSigning
from api.services.github_identity import (
    GITHUB_API,
    CommitIdentity,
    commit_identity,
    with_github_identity,
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
    identity: CommitIdentity | None = None
    # The member's own write token, kept on the platform: only ``git_push`` and pull request
    # creation use it. ``token`` is what the sandbox receives, and is read-only when it can be.
    user_token: str | None = field(default=None, repr=False)
    # The member's GPG signing key, present when they chose to commit as themselves signed.
    signing: MemberSigning | None = None
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


async def _linked_user_credential(
    db_mgr: DatabaseSessionManager | None,
    user_id: str,
    org_id: str,
    git_engine: GitEngine | None,
) -> tuple[UserCredentialData | None, str | None]:
    """The member's linked GitHub credential if GitHub still accepts it, else ``None`` and why."""
    user_cred = await get_user_credential(db_mgr, user_id, "github", org_id=org_id)
    if not (user_cred and user_cred.secret):
        return None, None
    api_url = git_engine.settings.github_api_url if git_engine else GITHUB_API
    user_cred = await refresh_github_user_token(db_mgr, user_cred)
    if not await github_token_is_valid(user_cred.secret, api_url):
        # A dead token must not win over the App fallback, nor reach the sandbox.
        logger.warning("The linked GitHub token of %s is no longer valid", user_id)
        return None, EXPIRED_LINK_WARNING
    return await with_github_identity(db_mgr, user_cred, api_url), None


def read_only_permissions(requested: Mapping[str, Any]) -> dict[str, str]:
    """Every permission asked for, lowered to ``read``.

    This token goes into the sandbox, so it must not be able to write whatever the
    organization's ``app_token_permissions`` says: an administrator may add reads (``actions``,
    ``checks``, ``statuses``) for the agent to follow CI, but a ``write`` or ``admin`` there
    would silently remove the guarantee that nothing in the sandbox can push. Writing is done
    by ``git_push`` with a token minted outside the sandbox.
    """
    return {str(name): "read" for name in requested}


async def _app_read_token(
    db_mgr: DatabaseSessionManager | None,
    org_id: str,
    git_engine: GitEngine | None,
    org_github: Mapping[str, Any],
    repo: str | None,
    allow_deployment_token: bool,
) -> tuple[str, dict[str, Any]] | None:
    """A read-only GitHub App token that can see ``repo``, with the permissions it carries.

    A deployment-wide token (``GITHUB_TOKEN``) stands in for the App only for a member who has
    linked nothing of their own, as it always has.
    """
    if not git_engine:
        return None
    try:
        permissions = read_only_permissions(
            org_github.get("app_token_permissions") or {"contents": "read"}
        )
        installation_id = pick_installation(
            [
                (i.installation_id, i.account_login)
                for i in await installations_for_org(db_mgr, org_id)
            ],
            repo,
        )
        token = None
        for prefer_app in (True, False) if allow_deployment_token else (True,):
            token = await git_engine.get_github_access_token(
                repo=repo,
                permissions={str(k): str(v) for k, v in permissions.items()},
                repositories=_target_repositories(org_github.get("app_token_target_repositories")),
                installation_id=installation_id,
                prefer_app=prefer_app,
            )
            if token:
                break
        if token and (
            repo is None or await _token_reads_repo(token, repo, git_engine.settings.github_api_url)
        ):
            return token, permissions
    except Exception as ex:
        logger.debug("Failed minting GitHub App fallback token: %s", ex)
    return None


async def _token_reads_repo(token: str, repo: str, api_url: str) -> bool:
    """Whether the token can read ``repo``: an App installed elsewhere must not shadow the member."""
    try:
        async with httpx.AsyncClient(timeout=TOKEN_CHECK_TIMEOUT_SECONDS) as client:
            response = await client.get(
                f"{api_url.rstrip('/')}/repos/{repo}",
                headers={"Authorization": f"Bearer {token}", "User-Agent": "Rocket-Chat"},
            )
    except httpx.HTTPError:
        return True  # unknown is not a reason to drop the App token
    return response.status_code != 404


async def resolve_session_git_credentials(
    user_id: str,
    org_id: str,
    git_engine: GitEngine | None,
    db_mgr: DatabaseSessionManager | None = None,
    user_overrides: dict[str, Any] | None = None,
    repo: str | None = None,
) -> GitCredentialBundle:
    """
    Resolves the Git credentials a session workspace receives, and the member's commit identity.

    The sandbox gets a **read-only** token whenever the GitHub App can read the repository, so
    nothing in it can push: writing goes through ``git_push`` with credentials that never enter
    the sandbox. Without such an App, the sandbox gets the member's own token (it cannot be
    narrowed) and the platform's own write guards are the only line.

    1. The GitHub App's read-only installation token (the member's token stays host-side).
    2. The member's linked GitHub account (OAuth token or PAT).
    3. Unauthenticated local fallback.
    """
    overrides = user_overrides or {}
    org_github = await effective_settings(db_mgr, org_id, "github")
    user_cred, warning = await _linked_user_credential(db_mgr, user_id, org_id, git_engine)
    github_meta = user_cred.metadata if user_cred else None
    identity = commit_identity(overrides, github_meta, user_id)
    member_token = user_cred.secret if user_cred else None
    member_details = {
        "username": (github_meta or {}).get("login"),
        "auth_type": (github_meta or {}).get("auth_type", "oauth"),
        "scopes": (github_meta or {}).get("scopes", []),
    }

    app = await _app_read_token(
        db_mgr, org_id, git_engine, org_github, repo, allow_deployment_token=user_cred is None
    )
    if app is not None and git_engine is not None:
        token, permissions = app
        commits_as_bot = user_cred is None
        return GitCredentialBundle(
            token=token,
            mode="app_readonly",
            author_name=git_engine.settings.bot_author_name if commits_as_bot else identity.name,
            author_email=git_engine.settings.bot_author_email if commits_as_bot else identity.email,
            source_details={"auth_type": "github_app", "permissions": permissions},
            identity=None if commits_as_bot else identity,
            user_token=member_token,
            warning=warning,
        )
    if user_cred and member_token:
        return GitCredentialBundle(
            token=member_token,
            mode="user_token",
            author_name=identity.name,
            author_email=identity.email,
            source_details=member_details,
            identity=identity,
            user_token=member_token,
        )
    return GitCredentialBundle(
        token=None,
        mode="none",
        author_name=identity.name,
        author_email=identity.email,
        identity=identity,
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


# A raw ``git push`` is refused with the way forward: commits leave the sandbox through the
# ``git_push`` tool, which holds the write credentials and applies the signing policy. The
# publisher sets ROCKET_PUBLISH for its own push. This explains the refusal; the credentials
# the sandbox holds (read-only where the GitHub App can read the repository) enforce it.
_PUSH_GUARD_HOOK = (
    "#!/bin/sh\n"
    '[ -n "$ROCKET_PUBLISH" ] && exit 0\n'
    "echo \"Direct 'git push' is not available in this workspace. Commit, then call the "
    "git_push tool (or git_open_pr to also open the pull request): it publishes with the "
    "organization's signing policy.\" >&2\n"
    "exit 1\n"
)
_PUSH_GUARD_SETUP = (
    'mkdir -p "$HOME/.rocket/hooks" && '
    f'printf %b {shlex.quote(_PUSH_GUARD_HOOK)} > "$HOME/.rocket/hooks/pre-push" && '
    'chmod +x "$HOME/.rocket/hooks/pre-push" && '
    'git config --global core.hooksPath "$HOME/.rocket/hooks"'
)


# The member's signing key lives in a memory-backed keyring while a turn runs: GnuPG needs it
# there to sign ``git commit`` for the agent. Never on the workspace volume, and removed when
# the turn ends. The wrapper adds what git cannot: the keyring and the passphrase, loopback.
SIGNING_HOME = "/dev/shm/rocket-gnupg"


def _signing_script(home: str, fingerprint: str) -> str:
    wrapper = (
        "#!/bin/sh\n"
        f"exec gpg --homedir {shlex.quote(home)} --batch --no-tty --pinentry-mode loopback "
        f'--passphrase-file {shlex.quote(home + "/pass")} "$@"\n'
    )
    return " && ".join(
        [
            "umask 077",
            f"rm -rf {shlex.quote(home)}",
            f"mkdir -p {shlex.quote(home)}",
            f"chmod 700 {shlex.quote(home)}",
            f'printf %s "$ROCKET_SIGNING_PASSPHRASE" > {shlex.quote(home + "/pass")}',
            f"printf %b {shlex.quote(wrapper)} > {shlex.quote(home + '/gpg-sign')}",
            f"chmod 700 {shlex.quote(home + '/gpg-sign')}",
            'printf %s "$ROCKET_SIGNING_KEY" | base64 -d | '
            f"{shlex.quote(home + '/gpg-sign')} --import 2>/dev/null",
            f"git config --global user.signingkey {shlex.quote(fingerprint)}",
            "git config --global commit.gpgsign true",
            f"git config --global gpg.program {shlex.quote(home + '/gpg-sign')}",
        ]
    )


def _signing_cleanup_script(home: str) -> str:
    return (
        f"rm -rf {shlex.quote(home)}; git config --global --unset-all user.signingkey; "
        "git config --global --unset-all commit.gpgsign; "
        "git config --global --unset-all gpg.program; true"
    )


async def apply_signing_to_sandbox(
    driver: SandboxDriverProtocol, session_id: str, signing: MemberSigning | None
) -> None:
    """Set the sandbox up to sign commits as the member, or remove any key left behind."""
    if signing is None:
        await driver.exec_command(session_id, _signing_cleanup_script(SIGNING_HOME))
        return
    encoded = base64.b64encode(signing.private_key.encode()).decode()
    result = await driver.exec_command(
        session_id,
        _signing_script(SIGNING_HOME, signing.fingerprint),
        env={"ROCKET_SIGNING_KEY": encoded, "ROCKET_SIGNING_PASSPHRASE": signing.passphrase},
    )
    if result.exit_code != 0:
        # Commits then stay unsigned rather than failing; the member learns from the warning.
        logger.warning(
            "Could not set up commit signing in %s (exit %d)", session_id, result.exit_code
        )
        await driver.exec_command(session_id, _signing_cleanup_script(SIGNING_HOME))


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
        setup = " && ".join(
            [
                f"git config --global user.name {shlex.quote(bundle.author_name)}",
                f"git config --global user.email {shlex.quote(bundle.author_email)}",
                "git config --global init.defaultBranch main",
                (
                    f"git config --global credential.helper {shlex.quote(_CREDENTIAL_HELPER)}"
                    if bundle.token
                    else "git config --global --unset-all credential.helper || true"
                ),
                _PUSH_GUARD_SETUP,
            ]
        )
        # A turn without a signing key must not inherit one a previous turn (another member of a
        # shared session) left configured, so the removal rides along with the setup.
        leftovers = "" if bundle.signing else f"; {_signing_cleanup_script(SIGNING_HOME)}"
        result = await driver.exec_command(session_id, f"{setup}; {_LEGACY_CLEANUP}{leftovers}")
        if bundle.signing:
            await apply_signing_to_sandbox(driver, session_id, bundle.signing)
        if result.exit_code != 0:
            logger.warning(
                "Git sandbox credential setup failed (exit %d): %s", result.exit_code, result.stderr
            )
        else:
            logger.info("Configured git access for session %s (mode=%s)", session_id, bundle.mode)
    except Exception as err:
        logger.warning("Exception configuring git access for session %s: %s", session_id, err)

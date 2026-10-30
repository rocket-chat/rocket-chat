"""Service resolving and injecting Git credentials into running sandbox environments."""

import asyncio
import hashlib
import logging
import os
import shlex
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from typing import Any

import httpx
from git_engine.engine import GitEngine, pick_installation
from git_engine.gitlab import GitLabSettings

from api.auth import git_gateway_token
from api.db.session import DatabaseSessionManager
from api.services.credentials import (
    UserCredentialData,
    decrypt_secret,
    encrypt_secret,
    get_user_credential,
    store_user_credential,
)
from api.services.git_gateway_env import sandbox_git_environment
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
    # How the platform reads GitHub for this member: "app" (the GitHub App's read-only
    # installation token), "member" (their own linked token) or "public" (no credential).
    mode: str
    author_name: str
    author_email: str
    source_details: dict[str, Any] = field(default_factory=dict)
    identity: CommitIdentity | None = None
    # The member's own write token, kept on the platform: only ``git_push`` and pull request
    # creation use it. ``token`` is the read credential the git gateway and the GitHub read tools
    # use on the platform's side; the sandbox receives neither (see ``services/git_gateway.py``).
    user_token: str | None = field(default=None, repr=False)
    # The member's GPG signing key, present when they chose to commit as themselves signed.
    signing: MemberSigning | None = None
    # Something the member should know about their git access, e.g. an expired link.
    warning: str | None = None
    # The member's GitLab token (``gitlab`` user credential), for repositories on GitLab: used
    # by the git gateway and publishing only, like ``user_token``.
    gitlab_token: str | None = field(default=None, repr=False)


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


def _allowed_repositories(raw: object) -> list[str] | None:
    """The organization's allowlist as lower-case ``owner/name`` (or legacy bare ``name``)
    entries; ``None`` means every installed repository."""
    text = str(raw or "all").strip()
    if text.lower() in ("", "all", "*"):
        return None
    return [part.strip().lower() for part in text.split(",") if part.strip()]


_warned_bare_entries: set[str] = set()


def _allows(entry: str, repository: str) -> bool:
    if "/" in entry:
        return entry == repository.lower()
    # Deprecated: a bare name matches that repository under every owner the App is installed on.
    if entry not in _warned_bare_entries:
        _warned_bare_entries.add(entry)
        logger.warning(
            "github.app_token_target_repositories entry %r has no owner and matches it under "
            "every owner; write it as owner/%s",
            entry,
            entry,
        )
    return entry == repository.rsplit("/", 1)[-1].lower()


def _target_repositories(raw: object, account_login: str | None = None) -> list[str] | None:
    """The repository names an App token of the installation on ``account_login`` is limited to.

    GitHub scopes an installation token by bare names, so the allowlist is narrowed to the
    installation's owner first: ``acme/api`` must not admit ``evil/api``. ``None`` means every
    installed repository; an empty list means none of this installation's.
    """
    allowed = _allowed_repositories(raw)
    if allowed is None:
        return None
    owner = (account_login or "").lower()
    return [
        entry.rsplit("/", 1)[-1]
        for entry in allowed
        if "/" not in entry or not owner or entry.split("/", 1)[0] == owner
    ]


def app_may_act_on(org_github: Mapping[str, Any], repository: str) -> bool:
    """Whether the organization lets its GitHub App's tokens reach ``owner/name``."""
    allowed = _allowed_repositories(org_github.get("app_token_target_repositories"))
    return allowed is None or any(_allows(entry, repository) for entry in allowed)


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
) -> tuple[str, dict[str, Any]] | None:
    """A read-only GitHub App token that can see ``repo``, with the permissions it carries."""
    if not git_engine:
        return None
    try:
        permissions = read_only_permissions(
            org_github.get("app_token_permissions")
            or {
                "contents": "read",
                "pull_requests": "read",
                "checks": "read",
                "actions": "read",
                "statuses": "read",
                "issues": "read",
            }
        )
        installations = [
            (i.installation_id, i.account_login)
            for i in await installations_for_org(db_mgr, org_id)
        ]
        installation_id = pick_installation(installations, repo)
        account = next((login for iid, login in installations if iid == installation_id), None)
        repositories = _target_repositories(
            org_github.get("app_token_target_repositories"), account
        )
        if repositories == [] or (repo is not None and not app_may_act_on(org_github, repo)):
            return None
        token = await git_engine.get_github_access_token(
            repo=repo,
            permissions={str(k): str(v) for k, v in permissions.items()},
            repositories=repositories,
            installation_id=installation_id,
        )
        if token and (
            repo is None or await _token_reads_repo(token, repo, git_engine.settings.github_api_url)
        ):
            return token, permissions
    except Exception as ex:
        logger.debug("Failed minting GitHub App read token: %s", ex)
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
    """The member's git credentials: GitHub (below), and their GitLab token if they added one."""
    bundle = await _github_credentials(user_id, org_id, git_engine, db_mgr, user_overrides, repo)
    gitlab = await get_user_credential(db_mgr, user_id, "gitlab", org_id=org_id)
    return replace(bundle, gitlab_token=gitlab.secret if gitlab else None)


async def _github_credentials(
    user_id: str,
    org_id: str,
    git_engine: GitEngine | None,
    db_mgr: DatabaseSessionManager | None = None,
    user_overrides: dict[str, Any] | None = None,
    repo: str | None = None,
) -> GitCredentialBundle:
    """How the platform reads GitHub for this member, and the member's commit identity.

    Every credential here is used on the platform's side only (the git gateway, the GitHub read
    tools, publishing); the sandbox receives none.

    1. ``app``: the organization's GitHub App, read-only, when it can see the repository.
    2. ``member``: the member's linked GitHub account (OAuth token or PAT).
    3. ``public``: no credential.
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

    app = await _app_read_token(db_mgr, org_id, git_engine, org_github, repo)
    if app is not None and git_engine is not None:
        token, permissions = app
        commits_as_bot = user_cred is None
        return GitCredentialBundle(
            token=token,
            mode="app",
            # The bot identity is the organization's setting (the deployment's GIT_BOT_NAME /
            # GIT_BOT_EMAIL are its instance default), the same one the turn's git policy uses.
            author_name=str(org_github["bot_author_name"]) if commits_as_bot else identity.name,
            author_email=str(org_github["bot_author_email"]) if commits_as_bot else identity.email,
            source_details={"auth_type": "github_app", "permissions": permissions},
            identity=None if commits_as_bot else identity,
            user_token=member_token,
            warning=warning,
        )
    if user_cred and member_token:
        return GitCredentialBundle(
            token=member_token,
            mode="member",
            author_name=identity.name,
            author_email=identity.email,
            source_details=member_details,
            identity=identity,
            user_token=member_token,
        )
    return GitCredentialBundle(
        token=None,
        mode="public",
        author_name=identity.name,
        author_email=identity.email,
        identity=identity,
        source_details={"auth_type": "none"},
        warning=warning,
    )


# Remove what earlier versions persisted on long-lived workspace volumes.
_LEGACY_CLEANUP = (
    'rm -f "$HOME/.git-credentials" /root/.git-credentials 2>/dev/null; '
    "sed -i '/^export GITHUB_TOKEN=/d;/^export GH_TOKEN=/d' \"$HOME/.bashrc\" 2>/dev/null; "
    "sed -i '/^GITHUB_TOKEN=/d;/^GH_TOKEN=/d' /etc/environment 2>/dev/null; "
    "git config --global --unset-all url.https://github.com/.insteadof 2>/dev/null; "
    # Earlier versions installed a helper reading GITHUB_TOKEN; the sandbox holds no token now.
    "git config --global --unset-all credential.helper 2>/dev/null; true"
)


# A raw ``git push`` is refused with the way forward: commits leave the sandbox through the
# ``git_push`` tool, which publishes from the platform with the organization's signing policy.
# The sandbox holds no credential that could push anyway (the git gateway serves reads only);
# the hook only makes the refusal say what to do.
_PUSH_GUARD_HOOK = (
    "#!/bin/sh\n"
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


# Earlier versions put the member's signing key in a memory-backed keyring in the sandbox for
# the length of a turn. Signing now happens on the platform at publication; this removes what a
# sandbox started by such a version may still hold.
_LEGACY_SIGNING_HOME = "/dev/shm/rocket-gnupg"
_LEGACY_SIGNING_CLEANUP = (
    f"rm -rf {_LEGACY_SIGNING_HOME}; git config --global --unset-all user.signingkey; "
    "git config --global --unset-all commit.gpgsign; "
    "git config --global --unset-all gpg.program; true"
)


def _gateway_environment(
    session_id: str, org_id: str, user_id: str, api_url: str
) -> dict[str, str]:
    capability = git_gateway_token.mint(session_id, org_id, user_id)
    return sandbox_git_environment(capability, api_url, GitLabSettings.from_env().url)


def _capability_refresher(
    session_id: str, org_id: str, user_id: str, api_url: str
) -> Callable[[], dict[str, str] | None]:
    """A callback for the driver: a new gateway environment once the capability is nearly spent.

    Called before every command of the session. Minting is a signature, not a network call, so
    asking is cheap; the capability lasts an hour and a turn can outlast it.
    """
    valid_until = time.time() + git_gateway_token.GIT_GATEWAY_TTL_SECONDS

    def refresh() -> dict[str, str] | None:
        nonlocal valid_until
        if valid_until - time.time() > git_gateway_token.RENEW_BEFORE_SECONDS:
            return None
        valid_until = time.time() + git_gateway_token.GIT_GATEWAY_TTL_SECONDS
        return _gateway_environment(session_id, org_id, user_id, api_url)

    return refresh


async def inject_git_credentials_into_sandbox(
    driver: SandboxDriverProtocol | None,
    session_id: str,
    bundle: GitCredentialBundle,
    *,
    org_id: str,
    user_id: str,
    api_url: str,
) -> None:
    """Configure git in ``session_id``'s sandbox: the committer identity and the git gateway.

    The sandbox receives no GitHub credential. Its git reaches GitHub through the gateway, which
    adds the credential on the platform; the sandbox only holds a session-scoped, read-only
    gateway capability, handed to the driver as per-session environment (supplied to each command,
    never written to the workspace). Called at every turn start; the driver then asks a refresher
    before every command, so the capability is renewed whenever less than twenty minutes of its
    hour are left, however long the turn runs.
    """
    if not driver:
        return

    try:
        driver.set_session_env(
            session_id, _gateway_environment(session_id, org_id, user_id, api_url)
        )
        driver.set_session_env_refresher(
            session_id, _capability_refresher(session_id, org_id, user_id, api_url)
        )
        setup = " && ".join(
            [
                f"git config --global user.name {shlex.quote(bundle.author_name)}",
                f"git config --global user.email {shlex.quote(bundle.author_email)}",
                "git config --global init.defaultBranch main",
                _PUSH_GUARD_SETUP,
            ]
        )
        result = await driver.exec_command(
            session_id, f"{setup}; {_LEGACY_CLEANUP}; {_LEGACY_SIGNING_CLEANUP}"
        )
        if result.exit_code != 0:
            logger.warning(
                "Git sandbox setup failed (exit %d): %s", result.exit_code, result.stderr
            )
        else:
            logger.info("Configured git for session %s (mode=%s)", session_id, bundle.mode)
    except Exception as err:
        logger.warning("Exception configuring git for session %s: %s", session_id, err)

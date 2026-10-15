"""The read-only git gateway: how a sandbox with no GitHub credential clones and fetches.

The sandbox's git is configured (through ``GIT_CONFIG_*`` environment variables, so nothing is
written to the workspace) to rewrite ``https://github.com/…`` and ``git@github.com:…`` to the
gateway and to send its gateway capability with every request. The gateway checks the capability,
adds the GitHub App's read token for that repository or the member's own token, and passes git's
smart-HTTP protocol through to GitHub as a stream. Only ``git-upload-pack`` (clone, fetch) is
served: commits leave the sandbox through ``git_push``, which publishes from the platform.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import re
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

import httpx
from git_engine.config import github_web_url
from git_engine.engine import GitEngine

from api.auth.git_gateway_token import GitGatewayGrant
from api.db.session import DatabaseSessionManager, current_tenant_org_id
from api.services.git_credentials import resolve_session_git_credentials
from api.services.settings import effective_settings
from specifications.interfaces.session import SessionStoreProtocol

logger = logging.getLogger(__name__)

UPLOAD_PACK = "git-upload-pack"

CONNECT_TIMEOUT_SECONDS = 10.0
IDLE_TIMEOUT_SECONDS = 300.0
# The negotiation a fetch sends (wants and haves) is small; a pack never travels this way.
MAX_REQUEST_BYTES = 64 * 1024 * 1024
# A credential is resolved (and checked against GitHub) once per member and repository, then
# reused until shortly before an App installation token would expire.
CREDENTIAL_CACHE_SECONDS = 50 * 60
SESSION_CONCURRENCY = 4
PROCESS_CONCURRENCY = 32

_OWNER = r"[A-Za-z0-9][A-Za-z0-9_.-]{0,99}"
_NAME = r"[A-Za-z0-9_.-]{1,100}"
REPOSITORY_PATH = re.compile(rf"^({_OWNER})/({_NAME}?)(?:\.git)?$")
# What git sends that GitHub needs; everything else (cookies, the capability) stays here.
_FORWARDED_REQUEST_HEADERS = (
    "accept",
    "accept-encoding",
    "content-encoding",
    "content-type",
    "git-protocol",
    "user-agent",
)
_FORWARDED_RESPONSE_HEADERS = ("cache-control", "content-encoding", "content-type", "expires")


class GatewayRefusal(Exception):
    """The request is refused; ``status`` and the message are returned to git."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


def parse_repository_path(path: str) -> str:
    """``owner/name`` from the gateway path, or a refusal."""
    match = REPOSITORY_PATH.match(path.strip("/"))
    if match is None or ".." in path:
        raise GatewayRefusal(404, "Not a GitHub repository path.")
    return f"{match.group(1)}/{match.group(2).removesuffix('.git')}"


@dataclass
class _CachedCredential:
    token: str | None
    expires_at: float


@dataclass
class GitGateway:
    """Checks a gateway request and streams it to GitHub with the right credential."""

    db_mgr: DatabaseSessionManager | None
    store: SessionStoreProtocol
    git_engine: GitEngine | None
    client: httpx.AsyncClient = field(
        default_factory=lambda: httpx.AsyncClient(
            timeout=httpx.Timeout(IDLE_TIMEOUT_SECONDS, connect=CONNECT_TIMEOUT_SECONDS),
            follow_redirects=False,
        )
    )
    _credentials: dict[tuple[str, str, str], _CachedCredential] = field(default_factory=dict)
    _process_limit: asyncio.Semaphore = field(
        default_factory=lambda: asyncio.Semaphore(PROCESS_CONCURRENCY)
    )
    _session_limits: dict[str, asyncio.Semaphore] = field(default_factory=dict)

    @property
    def api_url(self) -> str:
        return (
            self.git_engine.settings.github_api_url if self.git_engine else "https://api.github.com"
        )

    async def authorize(self, grant: GitGatewayGrant) -> None:
        """The session the capability names must still exist in the organization it names."""
        tenant = current_tenant_org_id.set(grant.org_id)
        try:
            record = await self.store.get_session(grant.session_id)
        finally:
            current_tenant_org_id.reset(tenant)
        if record is None or record.tenant_org_id != grant.org_id:
            raise GatewayRefusal(403, "The session this credential belongs to is gone.")

    async def credential(self, grant: GitGatewayGrant, repository: str) -> str | None:
        """The App's read token for ``repository``, else the member's token, else none (public)."""
        key = (grant.org_id, grant.user_id, repository.lower())
        cached = self._credentials.get(key)
        if cached is not None and cached.expires_at > time.monotonic():
            return cached.token
        tenant = current_tenant_org_id.set(grant.org_id)
        try:
            preferences = await effective_settings(
                self.db_mgr, grant.org_id, "user_preferences", grant.user_id
            )
            bundle = await resolve_session_git_credentials(
                user_id=grant.user_id,
                org_id=grant.org_id,
                git_engine=self.git_engine,
                db_mgr=self.db_mgr,
                user_overrides=preferences,
                repo=repository,
            )
        finally:
            current_tenant_org_id.reset(tenant)
        token = bundle.token or bundle.user_token
        self._credentials[key] = _CachedCredential(
            token, time.monotonic() + CREDENTIAL_CACHE_SECONDS
        )
        return token

    def upstream_url(self, repository: str, suffix: str) -> str:
        return f"{github_web_url(self.api_url)}/{repository}.git/{suffix}"

    def _limit(self, session_id: str) -> asyncio.Semaphore:
        limit = self._session_limits.get(session_id)
        if limit is None:
            limit = asyncio.Semaphore(SESSION_CONCURRENCY)
            self._session_limits[session_id] = limit
        return limit

    async def forward(
        self,
        grant: GitGatewayGrant,
        repository: str,
        method: str,
        suffix: str,
        query: str,
        headers: dict[str, str],
        body: AsyncIterator[bytes] | None,
    ) -> tuple[httpx.Response, AsyncIterator[bytes]]:
        """Send the request upstream; the response and a body stream that releases its slot."""
        token = await self.credential(grant, repository)
        upstream_headers = {
            name: value for name, value in headers.items() if name in _FORWARDED_REQUEST_HEADERS
        }
        if token:
            basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
            upstream_headers["authorization"] = f"Basic {basic}"
        url = self.upstream_url(repository, suffix) + (f"?{query}" if query else "")
        session_limit = self._limit(grant.session_id)
        await session_limit.acquire()
        await self._process_limit.acquire()
        try:
            request = self.client.build_request(
                method, url, headers=upstream_headers, content=_capped(body) if body else None
            )
            response = await self.client.send(request, stream=True)
        except BaseException:
            self._process_limit.release()
            session_limit.release()
            raise

        async def stream() -> AsyncIterator[bytes]:
            try:
                async for chunk in response.aiter_raw():
                    yield chunk
            finally:
                await response.aclose()
                self._process_limit.release()
                session_limit.release()

        return response, stream()


async def _capped(body: AsyncIterator[bytes]) -> AsyncIterator[bytes]:
    received = 0
    async for chunk in body:
        received += len(chunk)
        if received > MAX_REQUEST_BYTES:
            raise GatewayRefusal(413, "The git request is too large.")
        yield chunk


def response_headers(response: httpx.Response) -> dict[str, Any]:
    return {
        name: response.headers[name]
        for name in _FORWARDED_RESPONSE_HEADERS
        if name in response.headers
    }

"""A server that refuses a token mid-life is retried with a refreshed one before giving up."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any

import httpx2
from api.services import mcp_runtime
from api.services.mcp_runtime import (
    McpServerTarget,
    McpToolProvider,
    auth_headers,
    is_unauthorized,
    probe_target,
)
from config_engine.crypto import CredentialCipher


def _refused() -> httpx2.HTTPStatusError:
    request = httpx2.Request("POST", "https://kb.example.test/mcp")
    return httpx2.HTTPStatusError(
        "401", request=request, response=httpx2.Response(401, request=request)
    )


def test_a_refused_token_is_recognised_however_it_is_wrapped() -> None:
    assert is_unauthorized(_refused())
    assert is_unauthorized(BaseExceptionGroup("connect", [RuntimeError("x"), _refused()]))
    try:
        try:
            raise _refused()
        except httpx2.HTTPStatusError as inner:
            raise RuntimeError("session failed") from inner
    except RuntimeError as outer:
        assert is_unauthorized(outer)
    assert not is_unauthorized(RuntimeError("timed out"))


async def test_a_server_listed_again_with_a_fresh_token_offers_its_tools() -> None:
    @asynccontextmanager
    async def connect(target: McpServerTarget) -> AsyncIterator[Any]:
        if target.headers.get("Authorization") != "Bearer fresh":
            raise _refused()
        tool = SimpleNamespace(name="search", description="Search", input_schema={})
        yield SimpleNamespace(list_tools=lambda: _tools(tool))

    async def _tools(tool: Any) -> Any:
        return SimpleNamespace(tools=[tool])

    async def reachable(_target: McpServerTarget) -> None:
        return None

    target = McpServerTarget(
        "kb-refused", "Knowledge base", "http", "https://kb.example.test/mcp",
        headers={"Authorization": "Bearer stale"},
    )  # fmt: skip
    provider = McpToolProvider([target], connector=connect, guard=reachable)

    await provider.load()
    assert provider.unauthorized == {"kb-refused"} and not provider.definitions()

    assert await provider.retry("kb-refused", {"Authorization": "Bearer fresh"})
    assert [d.name for d in provider.definitions()] == ["mcp__knowledge-base__search"]
    await provider.aclose()


SENTRY_URL = "https://mcp.sentry.example.test/mcp"


def _serve_like_workers_oauth_provider(
    monkeypatch: Any, seen: list[str], accepted: str = "Bearer at-good"
) -> None:
    """An MCP server behind Cloudflare's workers-oauth-provider (Sentry's): it issues
    ``token_type: "bearer"`` but reads only an Authorization header starting with ``"Bearer "``."""

    def answer(request: httpx2.Request) -> httpx2.Response:
        authorization = request.headers.get("Authorization", "")
        seen.append(authorization)
        if authorization != accepted:
            return httpx2.Response(401, headers={"WWW-Authenticate": 'Bearer realm="OAuth"'})
        return httpx2.Response(500)

    class MockedClient(httpx2.AsyncClient):
        def __init__(self, **kwargs: Any) -> None:
            super().__init__(transport=httpx2.MockTransport(answer), **kwargs)

    async def public(_url: str) -> None:
        return None

    monkeypatch.setattr(mcp_runtime.httpx2, "AsyncClient", MockedClient)
    monkeypatch.setattr(mcp_runtime, "resolve_public_target", public)


async def test_a_lowercase_bearer_token_type_is_sent_as_the_bearer_scheme(
    monkeypatch: Any,
) -> None:
    seen: list[str] = []
    _serve_like_workers_oauth_provider(monkeypatch, seen)
    cipher = CredentialCipher()
    stored = {"access_token_enc": cipher.encrypt("at-good"), "token_type": "bearer"}

    target = McpServerTarget(
        "s", "Sentry", "http", SENTRY_URL, headers=auth_headers(stored, cipher)
    )
    status, _tools, error = await probe_target(target, 5)

    assert seen and set(seen) == {"Bearer at-good"}
    assert error != "The server refused the credentials (HTTP 401)."
    assert status == "error"


def test_other_token_types_keep_their_own_scheme() -> None:
    cipher = CredentialCipher()
    token = cipher.encrypt("t")
    assert auth_headers({"access_token_enc": token}, cipher) == {"Authorization": "Bearer t"}
    assert auth_headers({"access_token_enc": token, "token_type": "DPoP"}, cipher) == {
        "Authorization": "DPoP t"
    }


async def test_a_refusal_through_the_real_sdk_is_reported_as_a_401(monkeypatch: Any) -> None:
    """mcp 2.2 hides HTTP errors behind a JSON-RPC -32603 inside an exception group."""
    seen: list[str] = []
    _serve_like_workers_oauth_provider(monkeypatch, seen)
    target = McpServerTarget(
        "s", "Sentry", "http", SENTRY_URL, headers={"Authorization": "Bearer at-revoked"}
    )

    status, tools, error = await probe_target(target, 5)
    provider = McpToolProvider([target])
    await provider.load()

    assert (status, tools, error) == ("error", [], "The server refused the credentials (HTTP 401).")
    assert provider.unauthorized == {"s"}, "a turn refreshes the token instead of giving up"
    await provider.aclose()

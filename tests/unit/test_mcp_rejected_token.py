"""A server that refuses a token mid-life is retried with a refreshed one before giving up."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any

import httpx2
from api.services.mcp_runtime import McpServerTarget, McpToolProvider, is_unauthorized


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

"""Pluggable web search and the optional egress proxy for fetches."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from agent_core.tools import safe_http, tier1
from agent_core.tools.search_providers import (
    BraveProvider,
    DuckDuckGoHtmlProvider,
    SearchProviderError,
    SearxngProvider,
    provider_from_env,
)

Handler = Callable[[httpx.Request], httpx.Response]


@pytest.fixture
def serve(monkeypatch: pytest.MonkeyPatch) -> Callable[[Handler], list[httpx.Request]]:
    real_client = httpx.AsyncClient

    def install(handler: Handler) -> list[httpx.Request]:
        seen: list[httpx.Request] = []

        def recording(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return handler(request)

        def factory(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
            kwargs["transport"] = httpx.MockTransport(recording)
            return real_client(*args, **kwargs)

        monkeypatch.setattr("agent_core.tools.search_providers.httpx.AsyncClient", factory)
        return seen

    return install


def test_searxng_results_are_mapped_and_capped(
    serve: Callable[[Handler], list[httpx.Request]],
) -> None:
    seen = serve(
        lambda request: httpx.Response(
            200,
            json={
                "results": [
                    {"title": f"T{i}", "url": f"https://e.example/{i}", "content": f"S{i}"}
                    for i in range(10)
                ]
            },
        )
    )
    results = asyncio.run(SearxngProvider("http://searx.internal:8080/").search("rocket", 3))

    assert [r.title for r in results] == ["T0", "T1", "T2"]
    assert seen[0].url.path == "/search"
    assert dict(seen[0].url.params) == {"q": "rocket", "format": "json"}


def test_searxng_with_json_disabled_explains_the_fix(
    serve: Callable[[Handler], list[httpx.Request]],
) -> None:
    serve(lambda request: httpx.Response(403))
    with pytest.raises(SearchProviderError, match="json"):
        asyncio.run(SearxngProvider("http://searx").search("q", 3))


def test_brave_sends_the_key_as_a_header_never_in_the_url(
    serve: Callable[[Handler], list[httpx.Request]],
) -> None:
    seen = serve(
        lambda request: httpx.Response(
            200, json={"web": {"results": [{"title": "A", "url": "https://a", "description": "d"}]}}
        )
    )
    results = asyncio.run(BraveProvider("secret-key").search("q", 5))
    assert results[0].snippet == "d"
    assert seen[0].headers["X-Subscription-Token"] == "secret-key"
    assert "secret-key" not in str(seen[0].url)


def test_duckduckgo_redirect_wrappers_are_unwrapped(
    serve: Callable[[Handler], list[httpx.Request]],
) -> None:
    html = (
        '<div class="result"><a class="result__a" '
        'href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.org%2Fpage">Example</a>'
        '<a class="result__snippet">A snippet</a></div>'
    )
    serve(lambda request: httpx.Response(200, text=html))
    (result,) = asyncio.run(DuckDuckGoHtmlProvider().search("q", 5))
    assert result.url == "https://example.org/page"


@pytest.mark.parametrize(
    "env, message",
    [
        ({"ROCKET_SEARCH_PROVIDER": "searxng"}, "ROCKET_SEARXNG_URL"),
        ({"ROCKET_SEARCH_PROVIDER": "brave"}, "ROCKET_BRAVE_API_KEY"),
        ({"ROCKET_SEARCH_PROVIDER": "bing"}, "Unknown"),
    ],
)
def test_misconfiguration_is_reported_not_guessed(
    monkeypatch: pytest.MonkeyPatch, env: dict[str, str], message: str
) -> None:
    for key in ("ROCKET_SEARXNG_URL", "ROCKET_BRAVE_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    with pytest.raises(SearchProviderError, match=message):
        provider_from_env()
    assert message in asyncio.run(tier1.web_search("anything", 3))


def test_the_default_provider_is_duckduckgo(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ROCKET_SEARCH_PROVIDER", raising=False)
    assert isinstance(provider_from_env(), DuckDuckGoHtmlProvider)


def test_search_output_format_is_stable(
    monkeypatch: pytest.MonkeyPatch, serve: Callable[[Handler], list[httpx.Request]]
) -> None:
    monkeypatch.setenv("ROCKET_SEARCH_PROVIDER", "searxng")
    monkeypatch.setenv("ROCKET_SEARXNG_URL", "http://searx")
    serve(
        lambda r: httpx.Response(
            200, json={"results": [{"title": "T", "url": "https://u", "content": "S"}]}
        )
    )
    assert asyncio.run(tier1.web_search("rocket", 3)) == (
        "Search results for: 'rocket'\n\n\n1. [T](https://u)\n   S"
    )


def test_fetches_go_through_the_configured_egress_proxy_after_vetting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ROCKET_WEB_FETCH_PROXY", "http://egress-proxy:3128")
    monkeypatch.setenv("ROCKET_WEB_FETCH_ALLOW_PRIVATE", "1")
    captured: dict[str, Any] = {}
    real_client = httpx.AsyncClient

    def factory(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        captured["proxy"] = kwargs.get("proxy")

        def handler(request: httpx.Request) -> httpx.Response:
            captured["url"] = str(request.url)
            return httpx.Response(200, text="ok")

        kwargs.pop("proxy", None)
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(safe_http.httpx, "AsyncClient", factory)
    monkeypatch.setattr(
        safe_http,
        "resolve_public_target",
        _resolver,
    )
    _, body = asyncio.run(safe_http.safe_get("https://docs.example.org/page"))

    assert body == b"ok"
    assert captured["proxy"] == "http://egress-proxy:3128"
    assert captured["url"] == "https://docs.example.org/page"


async def _resolver(url: str) -> safe_http.ResolvedTarget:
    return safe_http.ResolvedTarget(host="docs.example.org", address="93.184.216.34")

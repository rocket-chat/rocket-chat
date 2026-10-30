"""Pluggable web-search backends.

Scraping a public HTML endpoint is fragile and rate-limited, so the default is only a
zero-configuration fallback. Self-hosters point ``ROCKET_SEARCH_PROVIDER`` at a SearXNG
instance they run (no API key, no third party), the Brave Search API or Tavily.

The SearXNG, Brave and Tavily endpoints are operator configuration, not model-chosen URLs, so they
are called directly rather than through the SSRF guard (a self-hosted SearXNG is normally on
a private network).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import parse_qs, unquote, urlparse

import httpx
from bs4 import BeautifulSoup

_BROWSER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
_TIMEOUT_SECONDS = 10.0


@dataclass(frozen=True)
class SearchResult:
    title: str
    url: str
    snippet: str


class SearchProviderError(RuntimeError):
    """The provider is misconfigured or could not answer."""


class SearchProvider(Protocol):
    async def search(self, query: str, max_results: int) -> list[SearchResult]: ...


class DuckDuckGoHtmlProvider:
    """Zero-configuration fallback that parses DuckDuckGo's HTML endpoint."""

    async def search(self, query: str, max_results: int) -> list[SearchResult]:
        async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS, follow_redirects=True) as client:
            response = await client.post(
                "https://html.duckduckgo.com/html/",
                data={"q": query},
                headers={"User-Agent": _BROWSER_AGENT},
            )
            response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        results: list[SearchResult] = []
        for block in soup.find_all("div", class_="result", limit=max_results):
            title_tag = block.find("a", class_="result__a")
            if not title_tag:
                continue
            snippet_tag = block.find("a", class_="result__snippet")
            results.append(
                SearchResult(
                    title=title_tag.get_text(strip=True),
                    url=_unwrap_duckduckgo_redirect(str(title_tag.get("href") or "")),
                    snippet=snippet_tag.get_text(strip=True) if snippet_tag else "",
                )
            )
        return results


def _unwrap_duckduckgo_redirect(href: str) -> str:
    parsed = urlparse(href)
    if "duckduckgo.com" in parsed.netloc and parsed.path == "/l/":
        target = parse_qs(parsed.query).get("uddg")
        if target:
            return unquote(target[0])
    return href


class SearxngProvider:
    """A SearXNG instance's JSON API (enable ``json`` under ``search.formats``)."""

    def __init__(self, base_url: str) -> None:
        self._endpoint = base_url.rstrip("/") + "/search"

    async def search(self, query: str, max_results: int) -> list[SearchResult]:
        async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
            response = await client.get(
                self._endpoint,
                params={"q": query, "format": "json"},
                headers={"Accept": "application/json"},
            )
            if response.status_code == 403:
                raise SearchProviderError(
                    "SearXNG refused the request; enable the 'json' format in its settings.yml."
                )
            response.raise_for_status()
        return [
            SearchResult(
                title=str(item.get("title") or ""),
                url=str(item.get("url") or ""),
                snippet=str(item.get("content") or ""),
            )
            for item in response.json().get("results", [])[:max_results]
        ]


class BraveProvider:
    """The Brave Search API (requires ``ROCKET_BRAVE_API_KEY``)."""

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key

    async def search(self, query: str, max_results: int) -> list[SearchResult]:
        async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
            response = await client.get(
                "https://api.search.brave.com/res/v1/web/search",
                params={"q": query, "count": max_results},
                headers={"Accept": "application/json", "X-Subscription-Token": self._api_key},
            )
            response.raise_for_status()
        items = response.json().get("web", {}).get("results", [])
        return [
            SearchResult(
                title=str(item.get("title") or ""),
                url=str(item.get("url") or ""),
                snippet=str(item.get("description") or ""),
            )
            for item in items[:max_results]
        ]


class TavilyProvider:
    """Tavily's search API (requires ``ROCKET_TAVILY_API_KEY``)."""

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key

    async def search(self, query: str, max_results: int) -> list[SearchResult]:
        async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
            response = await client.post(
                "https://api.tavily.com/search",
                json={"query": query, "max_results": max_results},
                headers={"Authorization": f"Bearer {self._api_key}"},
            )
            response.raise_for_status()
        return [
            SearchResult(
                title=str(item.get("title") or ""),
                url=str(item.get("url") or ""),
                snippet=str(item.get("content") or ""),
            )
            for item in response.json().get("results", [])[:max_results]
        ]


# Provider -> the variable holding its key or address (empty for none needed).
PROVIDER_SETTINGS = {
    "duckduckgo": "",
    "searxng": "ROCKET_SEARXNG_URL",
    "brave": "ROCKET_BRAVE_API_KEY",
    "tavily": "ROCKET_TAVILY_API_KEY",
}


def configured_provider_name() -> str:
    return os.getenv("ROCKET_SEARCH_PROVIDER", "duckduckgo").strip().lower() or "duckduckgo"


def provider_from_env() -> SearchProvider:
    """Select the backend from ``ROCKET_SEARCH_PROVIDER`` (default ``duckduckgo``)."""
    name = configured_provider_name()
    if name == "duckduckgo":
        return DuckDuckGoHtmlProvider()
    if name == "searxng":
        url = os.getenv("ROCKET_SEARXNG_URL", "").strip()
        if not url:
            raise SearchProviderError("ROCKET_SEARCH_PROVIDER=searxng needs ROCKET_SEARXNG_URL.")
        return SearxngProvider(url)
    if name in ("brave", "tavily"):
        variable = PROVIDER_SETTINGS[name]
        key = os.getenv(variable, "").strip()
        if not key:
            raise SearchProviderError(f"ROCKET_SEARCH_PROVIDER={name} needs {variable}.")
        return BraveProvider(key) if name == "brave" else TavilyProvider(key)
    raise SearchProviderError(
        f"Unknown ROCKET_SEARCH_PROVIDER '{name}' (use duckduckgo, searxng, brave or tavily)."
    )

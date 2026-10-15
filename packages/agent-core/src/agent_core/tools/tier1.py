"""Tier 1 in-process tools for web retrieval and search."""

import json

import httpx
from bs4 import BeautifulSoup
from markdownify import markdownify

from agent_core.tools.safe_http import UnsafeURLError, safe_get
from agent_core.tools.search_providers import SearchProviderError, provider_from_env

WEB_PAGE_CHARS = 20_000


def _page(text: str, url: str, start: int, max_chars: int) -> str:
    """One window of a page's text, saying where it is and how to read on."""
    size = max(1_000, min(max_chars, WEB_PAGE_CHARS))
    begin = max(0, start)
    window = text[begin : begin + size]
    end = begin + len(window)
    if begin == 0 and end >= len(text):
        return window
    more = f"call web_fetch with start={end} to continue" if end < len(text) else "end of page"
    return f"{window}\n\n[{url}: characters {begin:,}-{end:,} of {len(text):,}; {more}]"


async def web_fetch(
    url: str, timeout_seconds: float = 15.0, start: int = 0, max_chars: int = WEB_PAGE_CHARS
) -> str:
    """Fetch content from a URL and convert HTML pages into clean Markdown."""
    clean_url = url.strip()
    if not clean_url.startswith(("http://", "https://")):
        return f"Error: Invalid URL scheme for '{url}'. Only http and https are supported."

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
    }

    try:
        response, body = await safe_get(clean_url, headers=headers, timeout_seconds=timeout_seconds)
        response.raise_for_status()
    except UnsafeURLError as err:
        return f"Error: refusing to fetch {url}: {err}"
    except httpx.HTTPError as err:
        return f"Error fetching {url}: {err}"

    content_type = response.headers.get("content-type", "").lower()
    text = body.decode(response.encoding or "utf-8", errors="replace")

    if "application/json" in content_type:
        try:
            return _page(json.dumps(json.loads(text), indent=2), clean_url, start, max_chars)
        except json.JSONDecodeError:
            return _page(text, clean_url, start, max_chars)

    # Strip script and styling noise before markdown transformation
    soup = BeautifulSoup(text, "html.parser")
    for tag in soup(["script", "style", "noscript", "svg"]):
        tag.decompose()

    html_str = str(soup)
    markdown_text = markdownify(html_str, heading_style="ATX").strip()

    return _page(markdown_text or text, clean_url, start, max_chars)


async def web_search(query: str, max_results: int = 5) -> str:
    """Perform a web search and return structured title, URL, and snippet results."""
    clean_query = query.strip()
    if not clean_query:
        return "Error: Search query cannot be empty."

    try:
        results = await provider_from_env().search(clean_query, max_results)
    except SearchProviderError as err:
        return f"Search provider misconfigured: {err}"
    except httpx.HTTPError as err:
        return f"Search provider unavailable: {err}"

    if not results:
        return f"No search results found for query: '{clean_query}'"

    lines: list[str] = [f"Search results for: '{clean_query}'\n"]
    for i, res in enumerate(results, start=1):
        lines.append(f"{i}. [{res.title}]({res.url})\n   {res.snippet}")

    return "\n\n".join(lines)

"""Tier 1 in-process tools for web retrieval and search."""

import json

import httpx
from bs4 import BeautifulSoup
from markdownify import markdownify

from agent_core.tools.safe_http import UnsafeURLError, safe_get
from agent_core.tools.search_providers import SearchProviderError, provider_from_env


async def web_fetch(url: str, timeout_seconds: float = 15.0) -> str:
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
            parsed = json.loads(text)
            return json.dumps(parsed, indent=2)
        except json.JSONDecodeError:
            return text

    # Strip script and styling noise before markdown transformation
    soup = BeautifulSoup(text, "html.parser")
    for tag in soup(["script", "style", "noscript", "svg"]):
        tag.decompose()

    html_str = str(soup)
    markdown_text = markdownify(html_str, heading_style="ATX").strip()

    # Truncate oversized page content to protect downstream model context limits
    max_chars = 100_000
    if len(markdown_text) > max_chars:
        return markdown_text[:max_chars] + f"\n\n[Content truncated at {max_chars} characters]"

    return markdown_text or text


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

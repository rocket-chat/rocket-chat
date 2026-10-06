"""Tier 1 in-process tools for web retrieval and search."""

import json
from urllib.parse import parse_qs, unquote, urlparse

import httpx
from bs4 import BeautifulSoup
from markdownify import markdownify


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
        async with httpx.AsyncClient(timeout=timeout_seconds, follow_redirects=True) as client:
            response = await client.get(clean_url, headers=headers)
            response.raise_for_status()
    except httpx.HTTPError as err:
        return f"Error fetching {url}: {err}"

    content_type = response.headers.get("content-type", "").lower()
    text = response.text

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

    search_url = "https://html.duckduckgo.com/html/"
    data = {"q": clean_query}
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
    }

    try:
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
            response = await client.post(search_url, data=data, headers=headers)
            response.raise_for_status()
    except httpx.HTTPError as err:
        return f"Search provider unavailable: {err}"

    soup = BeautifulSoup(response.text, "html.parser")
    results: list[dict[str, str]] = []

    for result in soup.find_all("div", class_="result", limit=max_results):
        title_tag = result.find("a", class_="result__a")
        snippet_tag = result.find("a", class_="result__snippet")
        if not title_tag:
            continue

        href_val = title_tag.get("href")
        raw_href = str(href_val) if href_val is not None else ""
        # DuckDuckGo redirect wrapper extraction
        parsed_href = urlparse(raw_href)
        actual_url = raw_href
        if "duckduckgo.com" in parsed_href.netloc and parsed_href.path == "/l/":
            query_params = parse_qs(parsed_href.query)
            if "uddg" in query_params:
                actual_url = unquote(query_params["uddg"][0])

        title = title_tag.get_text(strip=True)
        snippet = snippet_tag.get_text(strip=True) if snippet_tag else ""

        results.append({"title": title, "url": actual_url, "snippet": snippet})

    if not results:
        return f"No search results found for query: '{clean_query}'"

    lines: list[str] = [f"Search results for: '{clean_query}'\n"]
    for i, res in enumerate(results, start=1):
        lines.append(f"{i}. [{res['title']}]({res['url']})\n   {res['snippet']}")

    return "\n\n".join(lines)

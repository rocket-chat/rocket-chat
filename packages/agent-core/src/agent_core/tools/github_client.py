"""HTTP access to GitHub for the agent's GitHub tools, with failures phrased for the agent.

Every call runs on the platform with a credential the tools hold (``GithubAccess``): the token
never appears in an argument, a result or an event. Results are compact text the model reads.
"""

from __future__ import annotations

from typing import Any

import httpx

from specifications.interfaces.agent import GithubAccess

TIMEOUT_SECONDS = 20.0
MAX_PAGES = 5


class GithubError(Exception):
    """A failure already phrased for the agent."""


def headers(access: GithubAccess, accept: str = "application/vnd.github+json") -> dict[str, str]:
    result = {
        "Accept": accept,
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "rocket-chat-agent",
    }
    if access.token:
        result["Authorization"] = f"Bearer {access.token}"
    return result


def _explain(response: httpx.Response, access: GithubAccess) -> str:
    status = response.status_code
    if status == 404:
        return (
            "Not found, or not visible to the credential this session has"
            + ("" if access.token else " (no GitHub credential: public data only)")
            + "."
        )
    if status in (401, 403):
        if response.headers.get("x-ratelimit-remaining") == "0":
            reset = response.headers.get("x-ratelimit-reset", "")
            return f"GitHub's rate limit is exhausted (resets at epoch {reset}); wait or narrow the work."
        if "retry-after" in response.headers:
            return f"GitHub asked to slow down (secondary rate limit); retry after {response.headers['retry-after']}s."
        return (
            "GitHub refused: the credential is missing or not allowed to do this "
            f"({access.actor or 'no credential'}). Do not retry or hunt for tokens; say what you "
            "could not do."
        )
    detail = response.text.strip()[:400]
    return f"GitHub answered HTTP {status}: {detail}"


async def request(
    access: GithubAccess,
    method: str,
    path: str,
    *,
    params: dict[str, Any] | None = None,
    body: Any = None,
    accept: str = "application/vnd.github+json",
) -> Any:
    """One REST call; the decoded JSON (or text for non-JSON media types), or ``GithubError``."""
    url = f"{access.api_url.rstrip('/')}{path}"
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS, follow_redirects=True) as client:
            response = await client.request(
                method, url, headers=headers(access, accept), params=params, json=body
            )
    except httpx.HTTPError as err:
        raise GithubError(f"Could not reach GitHub ({type(err).__name__}).") from err
    if response.status_code >= 400:
        raise GithubError(_explain(response, access))
    if not response.content:
        return None
    if "json" in response.headers.get("content-type", ""):
        return response.json()
    return response.text


async def paginate(
    access: GithubAccess, path: str, params: dict[str, Any] | None = None, pages: int = MAX_PAGES
) -> list[Any]:
    """Up to ``pages`` pages of a list endpoint (100 items each)."""
    items: list[Any] = []
    for page in range(1, max(1, min(pages, MAX_PAGES)) + 1):
        batch = await request(
            access, "GET", path, params={**(params or {}), "per_page": 100, "page": page}
        )
        if not isinstance(batch, list):
            return [batch]
        items.extend(batch)
        if len(batch) < 100:
            break
    return items


def graphql_url(api_url: str) -> str:
    base = api_url.rstrip("/")
    return base[: -len("/v3")] + "/graphql" if base.endswith("/api/v3") else f"{base}/graphql"


async def graphql(access: GithubAccess, query: str, variables: dict[str, Any] | None = None) -> Any:
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
            response = await client.post(
                graphql_url(access.api_url),
                headers=headers(access),
                json={"query": query, "variables": variables or {}},
            )
    except httpx.HTTPError as err:
        raise GithubError(f"Could not reach GitHub ({type(err).__name__}).") from err
    if response.status_code >= 400:
        raise GithubError(_explain(response, access))
    payload = response.json()
    if payload.get("errors"):
        raise GithubError(
            "GitHub refused: " + "; ".join(e.get("message", "") for e in payload["errors"])
        )
    return payload.get("data")


def acted_as(access: GithubAccess) -> str:
    return f" (as {access.actor})" if access.actor else ""

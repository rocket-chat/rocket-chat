"""Fetching and searching the web."""

import logging
from typing import TYPE_CHECKING, Annotated

from pydantic import Field

from agent_core.tools import tier1

if TYPE_CHECKING:
    from agent_core.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)


def register(registry: "ToolRegistry") -> None:
    @registry._tool(
        description="Fetch content from a URL and convert HTML pages into clean Markdown."
    )
    async def web_fetch(
        url: Annotated[str, Field(description="The HTTP or HTTPS URL to fetch.")],
        timeout_seconds: Annotated[
            float, Field(description="Request timeout in seconds (default: 15.0).")
        ] = 15.0,
        start: Annotated[
            int, Field(description="Character offset to read from, to page through (default 0).")
        ] = 0,
        max_chars: Annotated[
            int, Field(description="Characters to return, at most 20000 (default 20000).")
        ] = 20_000,
    ) -> str:
        return await tier1.web_fetch(url, timeout_seconds, start, max_chars)

    @registry._tool(
        description=("Perform a web search and return structured title, URL, and snippet results.")
    )
    async def web_search(
        query: Annotated[str, Field(description="The search query terms.")],
        max_results: Annotated[
            int, Field(description="Maximum number of search results to return (default: 5).")
        ] = 5,
    ) -> str:
        return await tier1.web_search(query, max_results)

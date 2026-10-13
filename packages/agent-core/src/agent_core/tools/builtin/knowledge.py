"""Knowledge bases, long-term memory and skills."""

import logging
from typing import TYPE_CHECKING, Annotated, Any, Literal

from pydantic import Field

from agent_core.tools import memory_tools

if TYPE_CHECKING:
    from agent_core.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)


def register(registry: "ToolRegistry") -> None:
    @registry._tool(
        description=(
            "Search PostgreSQL pgvector knowledge bases for semantic documentation, "
            "architecture guides, and API specs."
        )
    )
    async def kb_search(
        query: Annotated[str, Field(description="Natural language question or search query.")],
        kb_id: Annotated[
            str | None,
            Field(description="Optional specific knowledge base ID to scope search."),
        ] = None,
        limit: Annotated[
            int, Field(description="Max result snippets to retrieve (default: 5).")
        ] = 5,
    ) -> str:
        search = registry._turn_callback("kb_search")
        if search:
            return str(await search(query, kb_id, limit))
        return f"Knowledge search requested for '{query}' (kb_id={kb_id})"

    @registry._tool(
        description=(
            "Persist a durable fact, preference, decision or convention for future "
            "sessions. Use it deliberately for knowledge worth keeping across sessions;"
            " never store secrets or credentials (they are refused)."
        )
    )
    async def remember(
        content: Annotated[
            str, Field(description="The durable statement to remember, in one clear sentence.")
        ],
        kind: Annotated[
            Literal["fact", "preference", "decision", "convention"],
            Field(description="Category of the memory (default: fact)."),
        ] = "fact",
        scope: Annotated[
            Literal["user", "org", "session"],
            Field(description="Who the memory applies to (default: user)."),
        ] = "user",
    ) -> str:
        return await memory_tools.remember(
            registry._on_memory_remember_callback, content, kind, scope
        )

    @registry._tool(
        description=(
            "Search durable long-term memories relevant to a query and return the best "
            "matches. Use it before answering questions about past decisions, "
            "conventions or preferences."
        )
    )
    async def recall(
        query: Annotated[
            str, Field(description="Natural-language description of what to remember.")
        ],
        scope: Annotated[
            Literal["user", "org", "session"] | None,
            Field(description="Optional scope to restrict the search to."),
        ] = None,
        limit: Annotated[int, Field(description="Maximum memories to return (default: 5).")] = 5,
    ) -> str:
        return await memory_tools.recall(registry._on_memory_recall_callback, query, scope, limit)

    @registry._tool(
        description=(
            "Load or run a skill from the organization's library (the available names are listed "
            "under [Skills] in your instructions). An instruction skill returns its full "
            "instructions for you to follow; a code skill runs with the given arguments. Call it "
            "before starting work the skill covers."
        )
    )
    async def execute_skill(
        skill_name: Annotated[
            str, Field(description="Name of the skill, exactly as listed under [Skills].")
        ],
        arguments: Annotated[
            dict[str, Any],
            Field(description="Arguments for a code skill; pass {} for an instruction skill."),
        ],
    ) -> str:
        run_skill = registry._turn_callback("execute_skill")
        if run_skill:
            return str(await run_skill(skill_name, arguments))
        return f"Skill '{skill_name}' execution requested with arguments {arguments}"

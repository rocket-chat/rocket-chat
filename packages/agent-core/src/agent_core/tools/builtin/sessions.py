"""Reading the agent's other sessions (only for an agent whose ``reads_agent_sessions`` is on)."""

from __future__ import annotations

import datetime as dt
from typing import TYPE_CHECKING, Annotated

from pydantic import Field

from agent_core.context import current_execution_ctx
from specifications.interfaces.agent import AgentSessionsProtocol

if TYPE_CHECKING:
    from agent_core.tools.registry import ToolRegistry

NOT_ENABLED = (
    "Error: this agent may not read its other sessions (an administrator turns this on in the "
    "agent's settings)."
)


def _sessions() -> AgentSessionsProtocol | None:
    ctx = current_execution_ctx.get()
    if ctx is None or not ctx.policy.reads_agent_sessions:
        return None
    return ctx.callbacks.agent_sessions


def _when(epoch: int) -> str:
    return dt.datetime.fromtimestamp(epoch, tz=dt.UTC).strftime("%Y-%m-%d %H:%M UTC")


def register(registry: ToolRegistry) -> None:
    @registry._tool(
        description=(
            "List the other sessions of your organization you answered in, most recent first "
            "(never direct messages or private threads): id, title, where, when, how many "
            "messages. Use it to find an earlier request, its answer or the feedback it got; "
            "then read one with agent_session_read."
        )
    )
    async def agent_sessions_list(
        query: Annotated[
            str, Field(description="Words the session's title must contain (empty: all).")
        ] = "",
        limit: Annotated[int, Field(description="How many sessions, at most 30.")] = 10,
    ) -> str:
        sessions = _sessions()
        if sessions is None:
            return NOT_ENABLED
        found = await sessions.list_sessions(query, limit)
        if not found:
            return "No other session of yours matches." if query else "No other session yet."
        lines = [
            f"- {s.session_id} · {s.title or '(untitled)'} · {s.place or s.source} · "
            f"{_when(s.updated_at)} · {s.messages} messages"
            for s in found
        ]
        return "\n".join(lines)

    @registry._tool(
        description=(
            "Read a window of one of your other sessions (an id from agent_sessions_list): the "
            "person's and your messages, numbered, without tool calls. Long messages and long "
            "windows are cut and say where to read on. Every read is audited."
        )
    )
    async def agent_session_read(
        session_id: Annotated[str, Field(description="The session to read.")],
        first_index: Annotated[
            int, Field(description="Number of the first message to read (default 0).")
        ] = 0,
        limit: Annotated[int, Field(description="How many messages, at most 50.")] = 20,
    ) -> str:
        sessions = _sessions()
        if sessions is None:
            return NOT_ENABLED
        excerpt = await sessions.read_session(session_id, first_index, limit)
        if excerpt is None:
            return (
                f"Error: '{session_id}' is not one of your other sessions you may read (it does "
                "not exist, you never answered there, or it is private)."
            )
        last = excerpt.first_index + max(excerpt.shown, 1) - 1
        header = (
            f"Session {excerpt.session_id} · {excerpt.title or '(untitled)'} · messages "
            f"{excerpt.first_index}-{last} of {excerpt.total_messages}"
        )
        return f"{header}\n\n{excerpt.text or '(no messages in this window)'}"

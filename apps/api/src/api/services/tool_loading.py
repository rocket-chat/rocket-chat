"""Which of an agent's tools a turn sends with every step, and which it loads on demand.

Every agent gets the core tools (``CORE_TOOLS``) and the ones it keeps active; the rest are
loaded when needed. What a session already loaded is remembered on it, so the definitions stay
in the cached prompt for its later turns.
"""

from __future__ import annotations

import logging
from typing import Any

from specifications.interfaces.agent import ACTIVE_TOOLS_WARNING, ToolLoading
from specifications.interfaces.session import SessionRecord

logger = logging.getLogger(__name__)

LOADED_TOOLS_KEY = "loaded_tools"
GUIDANCE_READ_KEY = "guidance_read"
SKILLS_READ_KEY = "skills_read"


def tool_loading_for(agent: Any) -> ToolLoading:
    """The agent's tool loading: the core set plus what it keeps active."""
    return ToolLoading(active=tuple(getattr(agent, "active_tools", None) or ()), send_all=False)


def loaded_tools_of(session: SessionRecord) -> tuple[str, ...]:
    metadata = session.metadata if isinstance(session.metadata, dict) else {}
    return tuple(str(name) for name in metadata.get(LOADED_TOOLS_KEY) or ())


def remember_loaded(names: list[str]) -> Any:
    """A ``mutate_session`` step adding ``names`` to the session's loaded tools."""

    def add(record: SessionRecord) -> None:
        metadata = dict(record.metadata) if isinstance(record.metadata, dict) else {}
        known = list(metadata.get(LOADED_TOOLS_KEY) or [])
        metadata[LOADED_TOOLS_KEY] = known + [n for n in names if n not in known]
        record.metadata = metadata

    return add


def guidance_read_of(session: SessionRecord) -> dict[str, str]:
    """The MCP servers whose usage guidance the agent put in effect, with the version it read."""
    metadata = session.metadata if isinstance(session.metadata, dict) else {}
    read = metadata.get(GUIDANCE_READ_KEY)
    return {str(k): str(v) for k, v in read.items()} if isinstance(read, dict) else {}


def remember_guidance_read(read: dict[str, str]) -> Any:
    """A ``mutate_session`` step recording the guidance in effect, in the order it was read."""

    def record(target: SessionRecord) -> None:
        metadata = dict(target.metadata) if isinstance(target.metadata, dict) else {}
        metadata[GUIDANCE_READ_KEY] = dict(read)
        target.metadata = metadata

    return record


def describe_loading(sent: int, deferred: int) -> str:
    """In a sentence, what the tool loading does for an agent."""
    if not deferred:
        return f"All {sent} allowed tools are sent with every step."
    warning = (
        f" That is more than {ACTIVE_TOOLS_WARNING}: every step pays for them, and models choose "
        "worse among many tools; keep only the ones this agent uses on most tasks."
        if sent > ACTIVE_TOOLS_WARNING
        else ""
    )
    return (
        f"{sent} tools are sent with every step (the core set and the ones kept active); the "
        f"other {deferred} are listed by name and loaded when the agent needs them.{warning}"
    )


def skills_read_of(session: SessionRecord) -> dict[str, str]:
    """The skills whose instructions the agent put in effect, with the version it read."""
    metadata = session.metadata if isinstance(session.metadata, dict) else {}
    read = metadata.get(SKILLS_READ_KEY)
    return {str(k): str(v) for k, v in read.items()} if isinstance(read, dict) else {}


def remember_skills_read(read: dict[str, str]) -> Any:
    """A ``mutate_session`` step recording the skills in effect, in the order they were read."""

    def record(target: SessionRecord) -> None:
        metadata = dict(target.metadata) if isinstance(target.metadata, dict) else {}
        metadata[SKILLS_READ_KEY] = dict(read)
        target.metadata = metadata

    return record

"""Which of an agent's tools a turn sends with every step, and which it loads on demand.

The organization picks the default (``general.tool_loading``, ``general.tool_loading_threshold``);
an agent can choose its own mode and the tools it always keeps loaded. What a session already
loaded is remembered on it, so the definitions stay in the cached prompt for its later turns.
"""

from __future__ import annotations

import logging
from typing import Any

from api.services.settings import effective_settings
from specifications.interfaces.agent import TOOL_LOADING_MODES, ToolLoading
from specifications.interfaces.session import SessionRecord

logger = logging.getLogger(__name__)

LOADED_TOOLS_KEY = "loaded_tools"


async def tool_loading_for(db_mgr: Any, org_id: str, agent: Any) -> ToolLoading:
    """The organization's tool loading, with the agent's own mode and always-loaded tools."""
    general = await effective_settings(db_mgr, org_id, "general")
    mode = str(getattr(agent, "tool_loading", None) or general["tool_loading"])
    if mode not in TOOL_LOADING_MODES:
        logger.warning("Ignoring unknown tool_loading %r for org %s", mode, org_id)
        mode = "all"
    return ToolLoading(
        mode=mode,
        active=tuple(getattr(agent, "active_tools", None) or ()),
        threshold=int(general["tool_loading_threshold"]),
    )


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

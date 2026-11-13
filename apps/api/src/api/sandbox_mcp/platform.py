"""The platform collaborators a sandbox MCP call uses, read from the API application."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agent_core.dispatcher import ToolDispatcher

from specifications.interfaces.events import EventBusProtocol
from specifications.interfaces.sandbox import SandboxDriverProtocol
from specifications.interfaces.session import SessionStoreProtocol


@dataclass(frozen=True)
class Platform:
    store: SessionStoreProtocol
    driver: SandboxDriverProtocol
    event_bus: EventBusProtocol
    # The orchestrator's own dispatcher: the same gates, redaction, offloading and concurrency
    # bounds as a tool call of an agent turn.
    dispatcher: ToolDispatcher
    db_mgr: Any = None
    git_engine: Any = None

    @classmethod
    def of(cls, state: Any) -> Platform:
        """The collaborators of a running API application (``app.state``)."""
        return cls(
            store=state.session_store,
            driver=state.sandbox_driver,
            event_bus=state.event_bus,
            dispatcher=state.orchestrator.dispatcher,
            db_mgr=getattr(state, "db_manager", None),
            git_engine=getattr(state, "git_engine", None),
        )

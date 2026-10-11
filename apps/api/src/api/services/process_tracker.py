"""Tracking the background processes a session started in its sandbox.

The sandbox holds the truth about a process (see ``agent_core.tools.processes``); this module
mirrors it into ``sandbox_processes`` so the history outlives the sandbox, announces starts and
exits on the session's event stream, and tells the agent what happened while it was away.
A refresh is cheap and idempotent; it runs after every process tool call, when the Processes
tab polls, and at the start of each turn.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Sequence
from dataclasses import dataclass, fields
from typing import Any, ClassVar

from agent_core.tools.processes import ProcessInfo, list_processes
from sqlalchemy import select

from api.db.models import SandboxProcessModel
from api.db.session import DatabaseSessionManager
from specifications.interfaces.events import EventBusProtocol, EventType, SessionEvent
from specifications.interfaces.sandbox import SandboxDriverProtocol, SandboxStatus

logger = logging.getLogger(__name__)

RUNNING = "running"
LOST = "lost"
REPORTED_METADATA_KEY = "processes_reported"


@dataclass
class TrackedProcess:
    session_id: str
    process_id: str
    org_id: str
    name: str
    command: str
    state: str
    port: int | None = None
    exit_code: int | None = None
    started_at: int = 0
    ended_at: int | None = None
    updated_at: int = 0

    def as_payload(self) -> dict[str, Any]:
        return {
            "process_id": self.process_id,
            "name": self.name,
            "command": self.command,
            "state": self.state,
            "port": self.port,
            "exit_code": self.exit_code,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
        }


_COLUMNS = [f.name for f in fields(TrackedProcess)]


class ProcessStore:
    """``sandbox_processes`` rows (tenant-scoped); a process-local dict without a database.

    The fallback is shared by every instance: the tracker is built wherever it is needed
    (turns, routes) and they must all see the same processes.
    """

    _MEMORY: ClassVar[dict[tuple[str, str], TrackedProcess]] = {}

    def __init__(self, db_manager: DatabaseSessionManager | None) -> None:
        self._db = (
            db_manager
            if db_manager is not None and getattr(db_manager, "session_factory", None)
            else None
        )

    async def list(self, session_id: str, org_id: str) -> list[TrackedProcess]:
        if self._db is None:
            found = [p for p in self._MEMORY.values() if p.session_id == session_id]
            return sorted(found, key=lambda p: p.started_at)
        async with self._db.session(tenant_org_id=org_id) as session:
            stmt = (
                select(SandboxProcessModel)
                .where(SandboxProcessModel.session_id == session_id)
                .order_by(SandboxProcessModel.started_at)
            )
            return [
                TrackedProcess(**{c: getattr(row, c) for c in _COLUMNS})
                for row in (await session.execute(stmt)).scalars()
            ]

    async def save(self, process: TrackedProcess) -> None:
        if self._db is None:
            self._MEMORY[(process.session_id, process.process_id)] = process
            return
        async with self._db.session(tenant_org_id=process.org_id) as session:
            row = await session.get(SandboxProcessModel, (process.session_id, process.process_id))
            if row is None:
                session.add(SandboxProcessModel(**{c: getattr(process, c) for c in _COLUMNS}))
                return
            for column in _COLUMNS:
                setattr(row, column, getattr(process, column))


class ProcessTracker:
    """Reconciles the sandbox's processes with the stored rows and announces what changed."""

    def __init__(self, store: ProcessStore, event_bus: EventBusProtocol) -> None:
        self._store = store
        self._bus = event_bus

    @classmethod
    def for_app(
        cls, db_manager: DatabaseSessionManager | None, event_bus: EventBusProtocol
    ) -> ProcessTracker:
        return cls(ProcessStore(db_manager), event_bus)

    async def history(self, session_id: str, org_id: str) -> list[TrackedProcess]:
        return await self._store.list(session_id, org_id)

    async def refresh(
        self,
        session_id: str,
        org_id: str,
        driver: SandboxDriverProtocol | None,
        *,
        keepalive_seconds: float = 0,
    ) -> list[TrackedProcess]:
        """Bring the stored rows up to date with the sandbox and return all of them."""
        stored = {p.process_id: p for p in await self._store.list(session_id, org_id)}
        live = await self._read_live(driver, session_id)
        now = int(time.time())
        if live is not None:
            seen = {info.id for info in live}
            for info in live:
                await self._apply(stored, info, session_id, org_id, now)
            for process_id, process in stored.items():
                if process_id not in seen and process.state == RUNNING:
                    await self._finish(process, LOST, None, now)
        self._keep_awake(driver, session_id, stored.values(), keepalive_seconds)
        return sorted(stored.values(), key=lambda p: p.started_at)

    async def _read_live(
        self, driver: SandboxDriverProtocol | None, session_id: str
    ) -> list[ProcessInfo] | None:
        if driver is None:
            return None
        try:
            if await driver.get_status(session_id) != SandboxStatus.RUNNING:
                return None
            return await list_processes(driver, session_id)
        except Exception as err:
            logger.warning("Could not read the processes of %s: %s", session_id, err)
            return None

    async def _apply(
        self,
        stored: dict[str, TrackedProcess],
        info: ProcessInfo,
        session_id: str,
        org_id: str,
        now: int,
    ) -> None:
        process = stored.get(info.id)
        if process is None:
            process = TrackedProcess(
                session_id=session_id,
                process_id=info.id,
                org_id=org_id,
                name=info.name,
                command=info.command,
                state=info.state,
                port=info.port,
                exit_code=info.exit_code,
                started_at=now,
                updated_at=now,
            )
            stored[info.id] = process
            await self._store.save(process)
            await self._announce(EventType.PROCESS_STARTED, process)
            if info.state != RUNNING:
                await self._finish(process, info.state, info.exit_code, now)
            return
        if process.state == RUNNING and info.state != RUNNING:
            await self._finish(process, info.state, info.exit_code, now)

    async def _finish(
        self, process: TrackedProcess, state: str, exit_code: int | None, now: int
    ) -> None:
        process.state, process.exit_code = state, exit_code
        process.ended_at = process.updated_at = now
        await self._store.save(process)
        await self._announce(EventType.PROCESS_EXITED, process)

    async def _announce(self, kind: EventType, process: TrackedProcess) -> None:
        await self._bus.publish(
            SessionEvent(
                session_id=process.session_id, event_type=kind, payload=process.as_payload()
            )
        )

    @staticmethod
    def _keep_awake(
        driver: SandboxDriverProtocol | None,
        session_id: str,
        processes: Any,
        keepalive_seconds: float,
    ) -> None:
        """Hold the idle reaper off while something is running; release it afterwards."""
        hold = getattr(driver, "hold_awake", None)
        if hold is None or keepalive_seconds <= 0:
            return
        running = any(p.state == RUNNING for p in processes)
        hold(session_id, keepalive_seconds if running else 0)


def describe_for_agent(processes: Sequence[TrackedProcess], reported: set[str]) -> str | None:
    """What changed with the session's background processes since the agent last heard.

    ``reported`` holds the ids whose exit the agent has already been told about.
    """
    lines = []
    for process in processes:
        if process.state == RUNNING:
            port = f", port {process.port}" if process.port else ""
            lines.append(f"- {process.process_id} running{port}: {process.name}")
        elif process.process_id not in reported:
            outcome = (
                f"exited with code {process.exit_code}"
                if process.exit_code is not None
                else process.state
            )
            lines.append(
                f"- {process.process_id} {outcome} while you were away: {process.name} "
                "(read its output with process_output)"
            )
    if not lines:
        return None
    return "Background processes in this sandbox:\n" + "\n".join(lines)


def unreported_exits(processes: Sequence[TrackedProcess], reported: set[str]) -> set[str]:
    return {p.process_id for p in processes if p.state != RUNNING and p.process_id not in reported}

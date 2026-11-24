"""What a mission run leaves behind: its record (``mission_runs``) and the session metadata the
run, its ``deliver`` tool and its deliveries share."""

from __future__ import annotations

import time
from dataclasses import replace
from typing import Any, ClassVar

from sqlalchemy import select, update

from api.db.models import MissionRunModel
from specifications.interfaces.automation import MissionRun

# The session metadata a mission run reads and writes.
MISSION_ID_KEY = "mission_id"
RUN_ID_KEY = "mission_run_id"
SCHEMA_KEY = "mission_output_schema"
OUTPUT_KEY = "mission_output"
REVIEWED_SHA_KEY = "review_last_sha"
# ``False`` on the session of a mission without GitHub writes: its turns read GitHub only.
GITHUB_WRITE_KEY = "mission_github_write"
# The pull requests the run opened: {"run_id": ..., "urls": [...]}, checked by its delivery.
PULL_REQUESTS_KEY = "mission_pull_requests"
# The toolset that carries the ``deliver`` tool into a run that has something to deliver.
MISSION_TOOLSET = "mission"


def forge_writes_of(metadata: Any) -> bool:
    """Whether a session's turns may change the forge: always, unless a mission said no."""
    return not (isinstance(metadata, dict) and metadata.get(GITHUB_WRITE_KEY) is False)


def note_pull_request(url: str) -> Any:
    """A session mutation that records a pull request opened by the session's current run."""

    def apply(record: Any) -> None:
        known = dict(record.metadata) if isinstance(record.metadata, dict) else {}
        run_id = str(known.get(RUN_ID_KEY) or "")
        if not run_id:
            return
        kept = known.get(PULL_REQUESTS_KEY)
        urls = (
            list(kept.get("urls") or [])
            if isinstance(kept, dict) and kept.get("run_id") == run_id
            else []
        )
        if url not in urls:
            urls.append(url)
        known[PULL_REQUESTS_KEY] = {"run_id": run_id, "urls": urls}
        record.metadata = known

    return apply


def pull_requests_of(metadata: Any, run_id: str) -> list[str]:
    """The pull requests run ``run_id`` opened, as recorded on its session."""
    kept = metadata.get(PULL_REQUESTS_KEY) if isinstance(metadata, dict) else None
    if not isinstance(kept, dict) or kept.get("run_id") != run_id:
        return []
    return [str(url) for url in kept.get("urls") or []]


# -- runs ------------------------------------------------------------------------------------


def _run(row: MissionRunModel) -> MissionRun:
    return MissionRun(
        id=row.id,
        org_id=row.org_id,
        mission_id=row.mission_id,
        session_id=row.session_id,
        source=row.source,
        title=row.title,
        status=row.status,
        deliveries=tuple(row.deliveries or ()),
        started_at=row.started_at,
        finished_at=row.finished_at,
    )


class RunStore:
    """Mission runs in PostgreSQL (tenant-scoped); a process-local dict without a database."""

    _MEMORY: ClassVar[dict[str, tuple[MissionRun, dict[str, Any]]]] = {}

    def __init__(self, db_mgr: Any) -> None:
        self._db = (
            db_mgr if db_mgr is not None and getattr(db_mgr, "session_factory", None) else None
        )

    async def create(self, run: MissionRun, item: dict[str, Any]) -> MissionRun:
        if self._db is None:
            self._MEMORY[run.id] = (run, dict(item))
            return run
        async with self._db.session(tenant_org_id=run.org_id) as session:
            session.add(
                MissionRunModel(
                    id=run.id,
                    org_id=run.org_id,
                    mission_id=run.mission_id,
                    session_id=run.session_id,
                    source=run.source,
                    title=run.title[:255],
                    item=dict(item),
                    status=run.status,
                    deliveries=[],
                    started_at=run.started_at,
                )
            )
        return run

    async def get(self, org_id: str, run_id: str) -> tuple[MissionRun, dict[str, Any]] | None:
        if self._db is None:
            held = self._MEMORY.get(run_id)
            return held if held and held[0].org_id == org_id else None
        async with self._db.session(tenant_org_id=org_id) as session:
            row = await session.get(MissionRunModel, run_id)
            return (_run(row), dict(row.item or {})) if row and row.org_id == org_id else None

    async def of_mission(self, org_id: str, mission_id: str, limit: int = 50) -> list[MissionRun]:
        if self._db is None:
            runs = [r for r, _ in self._MEMORY.values() if r.mission_id == mission_id]
            return sorted(runs, key=lambda r: r.started_at, reverse=True)[:limit]
        async with self._db.session(tenant_org_id=org_id) as session:
            rows = await session.execute(
                select(MissionRunModel)
                .where(MissionRunModel.org_id == org_id, MissionRunModel.mission_id == mission_id)
                .order_by(MissionRunModel.started_at.desc())
                .limit(limit)
            )
            return [_run(row) for row in rows.scalars()]

    async def finish(
        self, org_id: str, run_id: str, status: str, deliveries: list[dict[str, str]]
    ) -> None:
        fields: dict[str, Any] = {
            "status": status,
            "deliveries": deliveries,
            "finished_at": int(time.time()),
        }
        if self._db is None:
            held = self._MEMORY.get(run_id)
            if held is not None:
                run = replace(held[0], **{**fields, "deliveries": tuple(deliveries)})
                self._MEMORY[run_id] = (run, held[1])
            return
        async with self._db.session(tenant_org_id=org_id) as session:
            await session.execute(
                update(MissionRunModel).where(MissionRunModel.id == run_id).values(**fields)
            )

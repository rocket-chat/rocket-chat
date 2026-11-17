"""Durable session files: offloaded tool outputs and the scratch files of a sandbox-free agent.

A file lives in PostgreSQL (``session_files``, forced row-level security) so it survives a
restart, is visible from every replica and does not need a sandbox. Each file is capped
(``max_file_bytes``, a longer one keeps its head and tail) and each session is capped
(``max_session_bytes``): a write past it evicts the session's least recently written files,
never the one being written. When a sandbox starts, ``materialise_session_files`` copies the
files into the workspace so shell tools find them where the agent was told they are.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Any

from sqlalchemy import delete, func, select, text

from api.db.models import SessionFileModel
from api.db.session import DatabaseSessionManager
from api.services.settings import effective_settings
from specifications.interfaces.session import (
    SessionFile,
    SessionFilesProtocol,
    SessionFileStoreProtocol,
    SessionFileWrite,
)

logger = logging.getLogger(__name__)

DEFAULT_MAX_SESSION_BYTES = 64 * 1024 * 1024
DEFAULT_MAX_FILE_BYTES = 4 * 1024 * 1024
SESSION_FILE_KINDS = ("output", "scratch")
MAX_PATH_CHARS = 512
# Files copied into a sandbox when it starts; more than this many is a listing nobody reads.
MAX_MATERIALISED_FILES = 200


class SessionFilePathError(ValueError):
    """A path that does not name a file inside the workspace."""


def normalise_session_path(path: str) -> str:
    """The workspace-relative form of ``path``; refuses escapes and empty names."""
    cleaned = (path or "").strip().replace("\\", "/")
    for prefix in ("/workspace/", "workspace/", "/"):
        if cleaned.startswith(prefix):
            cleaned = cleaned[len(prefix) :]
    while cleaned.startswith("./"):
        cleaned = cleaned[2:]
    parts = [p for p in cleaned.split("/") if p not in ("", ".")]
    if not parts or any(p == ".." for p in parts):
        raise SessionFilePathError(f"'{path}' is not a file path inside the workspace")
    joined = "/".join(parts)
    if len(joined) > MAX_PATH_CHARS:
        raise SessionFilePathError(f"path longer than {MAX_PATH_CHARS} characters")
    return joined


def _byte_size(content: str) -> int:
    return len(content.encode("utf-8"))


def cap_content(content: str, max_bytes: int) -> tuple[str, bool]:
    """``content`` within ``max_bytes``: whole, or its head and tail around a marker."""
    if _byte_size(content) <= max_bytes:
        return content, False
    # Characters, not bytes, are cut; a quarter of slack covers multi-byte text.
    budget = max(1, (max_bytes * 3) // 8)
    omitted = len(content) - 2 * budget
    marker = (
        f"\n\n... [{omitted:,} characters cut: the file exceeded the session file limit] ...\n\n"
    )
    return content[:budget] + marker + content[-budget:], True


@dataclass(frozen=True)
class SessionFileLimits:
    max_session_bytes: int = DEFAULT_MAX_SESSION_BYTES
    max_file_bytes: int = DEFAULT_MAX_FILE_BYTES

    @classmethod
    def from_settings(cls, general: dict[str, Any] | None) -> SessionFileLimits:
        """``general.session_files_max_mb`` bounds a session; a file is at most a sixteenth."""
        megabytes = int((general or {}).get("session_files_max_mb") or 0)
        if megabytes <= 0:
            return cls()
        session_bytes = megabytes * 1024 * 1024
        return cls(
            max_session_bytes=session_bytes,
            max_file_bytes=min(DEFAULT_MAX_FILE_BYTES, max(64 * 1024, session_bytes // 16)),
        )


class InMemorySessionFileStore(SessionFileStoreProtocol):
    """Process-local store for tests and runs without a database, with the same ceilings."""

    def __init__(self, limits: SessionFileLimits | None = None) -> None:
        self.limits = limits or SessionFileLimits()
        self._files: dict[tuple[str, str], tuple[str, SessionFile]] = {}

    async def put(
        self, session_id: str, tenant_org_id: str, path: str, content: str, kind: str = "output"
    ) -> SessionFileWrite:
        key_path = normalise_session_path(path)
        stored, truncated = cap_content(content, self.limits.max_file_bytes)
        now = int(time.time())
        previous = self._files.pop((session_id, key_path), None)
        created = previous[1].created_at if previous else now
        # Re-inserted at the end: the dictionary's order is the write order.
        self._files[(session_id, key_path)] = (
            tenant_org_id,
            SessionFile(key_path, _byte_size(stored), kind, stored, created, now),
        )
        evicted = self._evict(session_id, keep=key_path)
        return SessionFileWrite(key_path, _byte_size(stored), truncated, tuple(evicted))

    def _evict(self, session_id: str, keep: str) -> list[str]:
        evicted: list[str] = []
        own = [(p, f) for (s, p), (_, f) in self._files.items() if s == session_id]
        total = sum(f.size for _, f in own)
        for path, file in own:
            if total <= self.limits.max_session_bytes:
                break
            if path == keep:
                continue
            self._files.pop((session_id, path), None)
            total -= file.size
            evicted.append(path)
        return evicted

    async def get(self, session_id: str, tenant_org_id: str, path: str) -> SessionFile | None:
        entry = self._files.get((session_id, normalise_session_path(path)))
        if entry is None or entry[0] != tenant_org_id:
            return None
        return entry[1]

    async def list_files(self, session_id: str, tenant_org_id: str) -> list[SessionFile]:
        rows = [
            replace(f, content="")
            for (s, _), (org, f) in self._files.items()
            if s == session_id and org == tenant_org_id
        ]
        return list(reversed(rows))

    async def delete(self, session_id: str, tenant_org_id: str, path: str) -> bool:
        key = (session_id, normalise_session_path(path))
        entry = self._files.get(key)
        if entry is None or entry[0] != tenant_org_id:
            return False
        del self._files[key]
        return True


class PostgresSessionFileStore(SessionFileStoreProtocol):
    """``session_files`` under the tenant's row-level security context."""

    def __init__(self, db: DatabaseSessionManager, limits: SessionFileLimits | None = None) -> None:
        self._db = db
        self.limits = limits or SessionFileLimits()

    async def put(
        self, session_id: str, tenant_org_id: str, path: str, content: str, kind: str = "output"
    ) -> SessionFileWrite:
        key_path = normalise_session_path(path)
        stored, truncated = cap_content(content, self.limits.max_file_bytes)
        size = _byte_size(stored)
        now_ms = time.time_ns() // 1_000_000
        now = now_ms // 1000
        async with self._db.session(tenant_org_id=tenant_org_id) as db:
            # Serialise writers of one session: two concurrent writes would each see room for
            # themselves and together pass the session ceiling.
            await db.execute(
                text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
                {"key": f"session_files:{session_id}"},
            )
            row = await db.get(SessionFileModel, (session_id, key_path))
            if row is None:
                db.add(
                    SessionFileModel(
                        session_id=session_id,
                        path=key_path,
                        org_id=tenant_org_id,
                        kind=kind,
                        content=stored,
                        size=size,
                        created_at=now,
                        updated_at=now,
                        written_ms=now_ms,
                    )
                )
            else:
                row.kind, row.content, row.size = kind, stored, size
                row.updated_at, row.written_ms = now, now_ms
            await db.flush()
            evicted = await self._evict(db, session_id, keep=key_path)
        return SessionFileWrite(key_path, size, truncated, tuple(evicted))

    async def _evict(self, db: Any, session_id: str, keep: str) -> list[str]:
        total = (
            await db.execute(
                select(func.coalesce(func.sum(SessionFileModel.size), 0)).where(
                    SessionFileModel.session_id == session_id
                )
            )
        ).scalar_one()
        if int(total) <= self.limits.max_session_bytes:
            return []
        oldest = (
            await db.execute(
                select(SessionFileModel.path, SessionFileModel.size)
                .where(SessionFileModel.session_id == session_id, SessionFileModel.path != keep)
                .order_by(SessionFileModel.written_ms.asc())
            )
        ).all()
        evicted: list[str] = []
        remaining = int(total)
        for path, size in oldest:
            if remaining <= self.limits.max_session_bytes:
                break
            evicted.append(path)
            remaining -= int(size)
        if evicted:
            await db.execute(
                delete(SessionFileModel).where(
                    SessionFileModel.session_id == session_id, SessionFileModel.path.in_(evicted)
                )
            )
        return evicted

    async def get(self, session_id: str, tenant_org_id: str, path: str) -> SessionFile | None:
        key_path = normalise_session_path(path)
        async with self._db.session(tenant_org_id=tenant_org_id) as db:
            row = await db.get(SessionFileModel, (session_id, key_path))
            return _to_file(row, with_content=True) if row is not None else None

    async def list_files(self, session_id: str, tenant_org_id: str) -> list[SessionFile]:
        async with self._db.session(tenant_org_id=tenant_org_id) as db:
            rows = (
                await db.execute(
                    select(
                        SessionFileModel.path,
                        SessionFileModel.size,
                        SessionFileModel.kind,
                        SessionFileModel.created_at,
                        SessionFileModel.updated_at,
                    )
                    .where(SessionFileModel.session_id == session_id)
                    .order_by(SessionFileModel.written_ms.desc())
                )
            ).all()
        return [
            SessionFile(r.path, int(r.size), r.kind, "", r.created_at, r.updated_at) for r in rows
        ]

    async def delete(self, session_id: str, tenant_org_id: str, path: str) -> bool:
        key_path = normalise_session_path(path)
        async with self._db.session(tenant_org_id=tenant_org_id) as db:
            result = await db.execute(
                delete(SessionFileModel).where(
                    SessionFileModel.session_id == session_id, SessionFileModel.path == key_path
                )
            )
            return bool(getattr(result, "rowcount", 0))


def _to_file(row: SessionFileModel, *, with_content: bool) -> SessionFile:
    return SessionFile(
        path=row.path,
        size=int(row.size),
        kind=row.kind,
        content=row.content if with_content else "",
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


_in_memory_store = InMemorySessionFileStore()


def get_session_file_store(
    db_mgr: DatabaseSessionManager | None, limits: SessionFileLimits | None = None
) -> SessionFileStoreProtocol:
    """The deployment's store: PostgreSQL when configured, else the process-wide fallback."""
    if db_mgr is not None:
        return PostgresSessionFileStore(db_mgr, limits)
    if limits is not None:
        _in_memory_store.limits = limits
    return _in_memory_store


class BoundSessionFiles(SessionFilesProtocol):
    """A store bound to one session and organization, handed to a turn's tools."""

    def __init__(self, store: SessionFileStoreProtocol, session_id: str, tenant_org_id: str):
        self._store, self._session_id, self._org = store, session_id, tenant_org_id

    async def put(self, path: str, content: str, kind: str = "output") -> SessionFileWrite:
        return await self._store.put(self._session_id, self._org, path, content, kind)

    async def get(self, path: str) -> SessionFile | None:
        return await self._store.get(self._session_id, self._org, path)

    async def list_files(self) -> list[SessionFile]:
        return await self._store.list_files(self._session_id, self._org)

    async def delete(self, path: str) -> bool:
        return await self._store.delete(self._session_id, self._org, path)


async def materialise_session_files(
    store: SessionFileStoreProtocol,
    driver: Any,
    session_id: str,
    tenant_org_id: str,
) -> Sequence[str]:
    """Copy the session's files into a sandbox that just started; returns the paths written.

    A file the agent was told is at ``/workspace/.rocket/outputs/…`` must be there for
    ``rg``, ``jq`` or ``bash_exec`` once a sandbox exists, not only for ``file_read``.
    A file already in the workspace is left alone: the sandbox's copy may be newer.
    """
    if not hasattr(driver, "write_file"):
        return []
    written: list[str] = []
    for listed in (await store.list_files(session_id, tenant_org_id))[:MAX_MATERIALISED_FILES]:
        file = await store.get(session_id, tenant_org_id, listed.path)
        if file is None:
            continue
        try:
            await driver.write_file(session_id, file.path, file.content, overwrite=False)
            written.append(file.path)
        except FileExistsError:
            continue
        except Exception as err:
            logger.warning("Could not copy session file %s into the sandbox: %s", file.path, err)
    return written


async def turn_session_file_store(
    db_mgr: DatabaseSessionManager | None, org_id: str
) -> SessionFileStoreProtocol:
    """The store with the organization's limits (``general.session_files_max_mb``)."""
    general = await effective_settings(db_mgr, org_id, "general")
    return get_session_file_store(db_mgr, SessionFileLimits.from_settings(general))

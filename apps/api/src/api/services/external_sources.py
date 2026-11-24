"""Durable external sources and the links of the objects imported from them.

PostgreSQL in deployments (forced row-level security on both tables), process memory in
development without a database. The periodic synchronisation claims due sources across every
organization, so ``claim_due`` runs on the schema-owner connection like the mission scheduler.
"""

from __future__ import annotations

import time
from dataclasses import asdict, replace
from typing import Any

from sqlalchemy import delete, func, select, update

from api.db.models import ExternalSourceLinkModel, ExternalSourceModel
from api.db.session import DatabaseSessionManager
from specifications.interfaces.external_sources import (
    Catalogue,
    CatalogueEntry,
    CataloguePlugin,
    ExternalSource,
    ExternalSourceStoreProtocol,
    LinkedFile,
    SourceAuth,
    SourceLink,
)

# -- catalogue as stored ---------------------------------------------------------------------


def catalogue_to_json(catalogue: Catalogue | None) -> dict[str, Any] | None:
    return asdict(catalogue) if catalogue is not None else None


def catalogue_from_json(raw: Any) -> Catalogue | None:
    if not isinstance(raw, dict):
        return None
    return Catalogue(
        commit=str(raw.get("commit") or ""),
        has_manifest=bool(raw.get("has_manifest")),
        plugins=tuple(
            CataloguePlugin(
                name=str(p.get("name") or ""),
                description=str(p.get("description") or ""),
                entries=tuple(str(e) for e in p.get("entries") or ()),
            )
            for p in raw.get("plugins") or ()
        ),
        entries=tuple(_entry_from_json(e) for e in raw.get("entries") or ()),
        notes=tuple(str(n) for n in raw.get("notes") or ()),
    )


def _entry_from_json(raw: dict[str, Any]) -> CatalogueEntry:
    return CatalogueEntry(
        kind=str(raw.get("kind") or "skill"),
        path=str(raw.get("path") or ""),
        name=str(raw.get("name") or ""),
        description=str(raw.get("description") or ""),
        plugins=tuple(str(p) for p in raw.get("plugins") or ()),
        files=tuple(str(f) for f in raw.get("files") or ()),
        document_id=str(raw.get("document_id") or ""),
        digest=str(raw.get("digest") or ""),
        problem=str(raw.get("problem") or ""),
    )


# What reading a source changes; never its schedule, name, branch or owner.
_READ_STATE = ("status", "last_commit", "last_synced_at", "last_error", "catalogue", "updated_at")


def _auth(value: str) -> SourceAuth:
    return "member" if value == "member" else "app"


# -- rows ------------------------------------------------------------------------------------


def _source_columns(source: ExternalSource) -> dict[str, Any]:
    return {
        "id": source.id,
        "org_id": source.org_id,
        "scope": source.scope,
        "user_id": source.user_id,
        "team_id": source.team_id,
        "name": source.name,
        "repository": source.repository,
        "ref": source.ref,
        "auth": source.auth,
        "status": source.status,
        "last_commit": source.last_commit,
        "last_synced_at": source.last_synced_at,
        "last_error": source.last_error,
        "catalogue": catalogue_to_json(source.catalogue),
        "next_sync_at": source.next_sync_at,
        "created_by": source.created_by,
        "created_at": source.created_at,
        "updated_at": source.updated_at,
    }


def _source_from_row(row: ExternalSourceModel) -> ExternalSource:
    return ExternalSource(
        id=row.id,
        org_id=row.org_id,
        name=row.name,
        repository=row.repository,
        ref=row.ref,
        auth=_auth(row.auth),
        scope=row.scope,
        user_id=row.user_id,
        team_id=row.team_id,
        status=row.status,
        last_commit=row.last_commit,
        last_synced_at=row.last_synced_at,
        last_error=row.last_error,
        catalogue=catalogue_from_json(row.catalogue),
        next_sync_at=row.next_sync_at,
        created_by=row.created_by,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _link_columns(link: SourceLink) -> dict[str, Any]:
    return {
        "id": link.id,
        "org_id": link.org_id,
        "source_id": link.source_id,
        "kind": link.kind,
        "object_id": link.object_id,
        "path": link.path,
        "plugin": link.plugin,
        "commit": link.commit,
        "document_id": link.document_id,
        "digest": link.digest,
        "status": link.status,
        "problem": link.problem,
        "files": [asdict(f) for f in link.files],
        "created_at": link.created_at,
        "updated_at": link.updated_at,
    }


def _link_from_row(row: ExternalSourceLinkModel) -> SourceLink:
    return SourceLink(
        id=row.id,
        org_id=row.org_id,
        source_id=row.source_id,
        kind=row.kind,
        object_id=row.object_id,
        path=row.path,
        plugin=row.plugin,
        commit=row.commit,
        document_id=row.document_id,
        digest=row.digest,
        status=row.status,
        problem=row.problem,
        files=[
            LinkedFile(
                str(f.get("path") or ""), str(f.get("content") or ""), bool(f.get("executable"))
            )
            for f in row.files or []
        ],
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


# -- stores ----------------------------------------------------------------------------------


class InMemoryExternalSourceStore(ExternalSourceStoreProtocol):
    """Development without a database: one process, so no claim can race."""

    def __init__(self) -> None:
        self._sources: dict[str, ExternalSource] = {}
        self._links: dict[tuple[str, str, str], SourceLink] = {}

    def clear(self) -> None:
        self._sources.clear()
        self._links.clear()

    async def save_source(self, source: ExternalSource) -> ExternalSource:
        self._sources[source.id] = replace(source)
        return source

    async def record_read(self, source: ExternalSource) -> None:
        found = self._sources.get(source.id)
        if found is None or found.org_id != source.org_id:
            return
        for name in _READ_STATE:
            setattr(found, name, getattr(source, name))

    async def get_source(self, org_id: str, source_id: str) -> ExternalSource | None:
        found = self._sources.get(source_id)
        return replace(found) if found is not None and found.org_id == org_id else None

    async def list_sources(self, org_id: str) -> list[ExternalSource]:
        rows = [replace(s) for s in self._sources.values() if s.org_id == org_id]
        return sorted(rows, key=lambda s: (s.name.lower(), s.id))

    async def delete_source(self, org_id: str, source_id: str) -> bool:
        if await self.get_source(org_id, source_id) is None:
            return False
        del self._sources[source_id]
        for key in [k for k, link in self._links.items() if link.source_id == source_id]:
            del self._links[key]
        return True

    async def sources_following(
        self, org_id: str, repository: str, ref: str
    ) -> list[ExternalSource]:
        wanted = repository.lower()
        return [
            s
            for s in await self.list_sources(org_id)
            if s.repository.lower() == wanted and s.ref == ref
        ]

    async def schedule(self, org_id: str, source_id: str, next_sync_at: int) -> None:
        found = self._sources.get(source_id)
        if found is not None and found.org_id == org_id:
            found.next_sync_at = next_sync_at

    async def claim_due(self, now: int, interval_seconds: int) -> list[ExternalSource]:
        claimed: list[ExternalSource] = []
        for source in self._sources.values():
            if source.next_sync_at <= now:
                source.next_sync_at = now + interval_seconds
                claimed.append(replace(source))
        return claimed

    async def save_link(self, link: SourceLink) -> SourceLink:
        self._links[(link.org_id, link.kind, link.object_id)] = replace(
            link, files=list(link.files)
        )
        return link

    async def links_of_source(self, org_id: str, source_id: str) -> list[SourceLink]:
        return [
            replace(link)
            for link in self._links.values()
            if link.org_id == org_id and link.source_id == source_id
        ]

    async def links_of_objects(
        self, org_id: str, kind: str, object_ids: list[str]
    ) -> dict[str, SourceLink]:
        found = (self._links.get((org_id, kind, object_id)) for object_id in object_ids)
        return {link.object_id: replace(link) for link in found if link is not None}

    async def delete_link(self, org_id: str, kind: str, object_id: str) -> None:
        self._links.pop((org_id, kind, object_id), None)


class PostgresExternalSourceStore(ExternalSourceStoreProtocol):
    """Rows under forced RLS; ``admin_db`` (the schema owner) only for ``claim_due``."""

    def __init__(
        self, db: DatabaseSessionManager, admin_db: DatabaseSessionManager | None = None
    ) -> None:
        self._db, self._admin = db, admin_db

    async def save_source(self, source: ExternalSource) -> ExternalSource:
        async with self._db.session(tenant_org_id=source.org_id) as db:
            await db.merge(ExternalSourceModel(**_source_columns(source)))
        return source

    async def record_read(self, source: ExternalSource) -> None:
        columns = _source_columns(source)
        async with self._db.session(tenant_org_id=source.org_id) as db:
            await db.execute(
                update(ExternalSourceModel)
                .where(
                    ExternalSourceModel.id == source.id,
                    ExternalSourceModel.org_id == source.org_id,
                )
                .values({name: columns[name] for name in _READ_STATE})
            )

    async def get_source(self, org_id: str, source_id: str) -> ExternalSource | None:
        async with self._db.session(tenant_org_id=org_id) as db:
            row = await db.get(ExternalSourceModel, source_id)
            return _source_from_row(row) if row is not None and row.org_id == org_id else None

    async def list_sources(self, org_id: str) -> list[ExternalSource]:
        statement = (
            select(ExternalSourceModel)
            .where(ExternalSourceModel.org_id == org_id)
            .order_by(func.lower(ExternalSourceModel.name), ExternalSourceModel.id)
        )
        async with self._db.session(tenant_org_id=org_id) as db:
            return [_source_from_row(r) for r in (await db.execute(statement)).scalars().all()]

    async def delete_source(self, org_id: str, source_id: str) -> bool:
        async with self._db.session(tenant_org_id=org_id) as db:
            result = await db.execute(
                delete(ExternalSourceModel).where(
                    ExternalSourceModel.id == source_id, ExternalSourceModel.org_id == org_id
                )
            )
            return bool(getattr(result, "rowcount", 0))

    async def sources_following(
        self, org_id: str, repository: str, ref: str
    ) -> list[ExternalSource]:
        statement = select(ExternalSourceModel).where(
            ExternalSourceModel.org_id == org_id,
            func.lower(ExternalSourceModel.repository) == repository.lower(),
            ExternalSourceModel.ref == ref,
        )
        async with self._db.session(tenant_org_id=org_id) as db:
            return [_source_from_row(r) for r in (await db.execute(statement)).scalars().all()]

    async def schedule(self, org_id: str, source_id: str, next_sync_at: int) -> None:
        async with self._db.session(tenant_org_id=org_id) as db:
            await db.execute(
                update(ExternalSourceModel)
                .where(ExternalSourceModel.id == source_id, ExternalSourceModel.org_id == org_id)
                .values(next_sync_at=next_sync_at)
            )

    async def claim_due(self, now: int, interval_seconds: int) -> list[ExternalSource]:
        if self._admin is None:
            return []
        claimed: list[ExternalSource] = []
        async with self._admin.session() as db:
            due = await db.execute(
                select(ExternalSourceModel).where(ExternalSourceModel.next_sync_at <= now)
            )
            for row in due.scalars().all():
                # Compare-and-set on the token: a replica that lost the race updates no row.
                result = await db.execute(
                    update(ExternalSourceModel)
                    .where(
                        ExternalSourceModel.id == row.id,
                        ExternalSourceModel.next_sync_at == row.next_sync_at,
                    )
                    .values(next_sync_at=now + interval_seconds)
                )
                if getattr(result, "rowcount", 0):
                    claimed.append(
                        replace(_source_from_row(row), next_sync_at=now + interval_seconds)
                    )
        return claimed

    async def save_link(self, link: SourceLink) -> SourceLink:
        link.updated_at = int(time.time())
        async with self._db.session(tenant_org_id=link.org_id) as db:
            existing = (
                await db.execute(
                    select(ExternalSourceLinkModel).where(
                        ExternalSourceLinkModel.org_id == link.org_id,
                        ExternalSourceLinkModel.kind == link.kind,
                        ExternalSourceLinkModel.object_id == link.object_id,
                    )
                )
            ).scalar_one_or_none()
            columns = _link_columns(link)
            if existing is None:
                db.add(ExternalSourceLinkModel(**columns))
            else:
                columns.pop("id")
                for name, value in columns.items():
                    setattr(existing, name, value)
                link.id = existing.id
        return link

    async def links_of_source(self, org_id: str, source_id: str) -> list[SourceLink]:
        statement = select(ExternalSourceLinkModel).where(
            ExternalSourceLinkModel.org_id == org_id, ExternalSourceLinkModel.source_id == source_id
        )
        async with self._db.session(tenant_org_id=org_id) as db:
            return [_link_from_row(r) for r in (await db.execute(statement)).scalars().all()]

    async def links_of_objects(
        self, org_id: str, kind: str, object_ids: list[str]
    ) -> dict[str, SourceLink]:
        if not object_ids:
            return {}
        statement = select(ExternalSourceLinkModel).where(
            ExternalSourceLinkModel.org_id == org_id,
            ExternalSourceLinkModel.kind == kind,
            ExternalSourceLinkModel.object_id.in_(object_ids),
        )
        async with self._db.session(tenant_org_id=org_id) as db:
            rows = (await db.execute(statement)).scalars().all()
            return {row.object_id: _link_from_row(row) for row in rows}

    async def delete_link(self, org_id: str, kind: str, object_id: str) -> None:
        async with self._db.session(tenant_org_id=org_id) as db:
            await db.execute(
                delete(ExternalSourceLinkModel).where(
                    ExternalSourceLinkModel.org_id == org_id,
                    ExternalSourceLinkModel.kind == kind,
                    ExternalSourceLinkModel.object_id == object_id,
                )
            )


_in_memory_store = InMemoryExternalSourceStore()


def external_source_store(
    db_mgr: DatabaseSessionManager | None, admin_db: DatabaseSessionManager | None = None
) -> ExternalSourceStoreProtocol:
    """The store of this deployment: PostgreSQL when there is a database, else memory."""
    if db_mgr is not None and getattr(db_mgr, "session_factory", None) is not None:
        return PostgresExternalSourceStore(db_mgr, admin_db)
    return _in_memory_store


def reset_memory_store() -> None:
    """Tests without a database start from no source."""
    _in_memory_store.clear()

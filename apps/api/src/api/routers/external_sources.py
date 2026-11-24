"""External sources: connect a repository, browse what it offers, import skills from it."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from api.models import CustomSkillResponse
from api.routers.skills import skill_responses
from api.services.external_sources import external_source_store
from api.services.ownership import request_viewer
from api.services.skill_sources import SKILL_KIND, repository_url
from api.services.source_management import (
    SourceError,
    create_source,
    delete_source,
    import_owner,
    managed_source,
    may_manage,
    update_source,
    visible_source,
    visible_sources,
)
from api.services.source_sync import SourceImportError, import_entries, synchronise
from specifications.interfaces.external_sources import ExternalSource, SourceReadError
from specifications.interfaces.ownership import Viewer

router = APIRouter(prefix="/v1", tags=["External sources"])


class ExternalSourceRequest(BaseModel):
    name: str = Field(default="", description="Display name; the repository when empty")
    repository: str = Field(..., description="GitHub repository: owner/name or its URL")
    ref: str = Field(default="main", description="The branch to follow")
    auth: str = Field(
        default="app",
        description="'app' (the organization's GitHub App) or 'member' (your linked GitHub "
        "account; personal sources only)",
    )
    scope: str = Field(default="org", description="Owner: 'org', 'team' or 'user'")
    team_id: str | None = Field(default=None, description="Owning team for team sources")


class ExternalSourceUpdate(BaseModel):
    name: str | None = Field(default=None, description="New display name")
    ref: str | None = Field(default=None, description="New branch to follow")


class ExternalSourceResponse(BaseModel):
    id: str
    name: str
    repository: str
    ref: str
    auth: str
    scope: str
    user_id: str | None = None
    team_id: str | None = None
    status: str
    last_commit: str = ""
    last_synced_at: int | None = None
    last_error: str = ""
    url: str
    has_manifest: bool = False
    plugin_count: int = 0
    skill_count: int = 0
    imported_count: int = 0
    can_manage: bool = False
    notes: list[str] = Field(default_factory=list)


class CatalogueImport(BaseModel):
    skill_id: str
    scope: str
    team_id: str | None = None
    user_id: str | None = None
    status: str


class CatalogueEntryResponse(BaseModel):
    path: str
    name: str
    description: str = ""
    plugins: list[str] = Field(default_factory=list)
    files: list[str] = Field(default_factory=list)
    problem: str = ""
    url: str
    imported: list[CatalogueImport] = Field(default_factory=list)


class CataloguePluginResponse(BaseModel):
    name: str
    description: str = ""
    entries: list[str] = Field(default_factory=list)


class CatalogueResponse(BaseModel):
    source: ExternalSourceResponse
    commit: str = ""
    has_manifest: bool = False
    plugins: list[CataloguePluginResponse] = Field(default_factory=list)
    entries: list[CatalogueEntryResponse] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class ImportRequest(BaseModel):
    paths: list[str] = Field(..., description="The folders of the skills to import")
    scope: str = Field(default="user", description="Owner of the skills: 'org', 'team' or 'user'")
    team_id: str | None = Field(default=None, description="Owning team for team skills")
    merge: list[str] = Field(
        default_factory=list,
        description="Folders whose skill may replace a skill of the same name (the source wins)",
    )


class ImportConflictResponse(BaseModel):
    path: str
    name: str
    skill_id: str


class ImportResponse(BaseModel):
    imported: list[CustomSkillResponse] = Field(default_factory=list)
    conflicts: list[ImportConflictResponse] = Field(default_factory=list)


class SyncResponse(BaseModel):
    source: ExternalSourceResponse
    updated: int = 0
    missing: int = 0
    invalid: int = 0
    unchanged: int = 0
    error: str = ""


def _http(err: SourceError) -> HTTPException:
    return HTTPException(status_code=err.status_code, detail=err.detail)


async def _response(state: Any, source: ExternalSource, viewer: Viewer) -> ExternalSourceResponse:
    store = external_source_store(getattr(state, "db_manager", None))
    catalogue = source.catalogue
    return ExternalSourceResponse(
        id=source.id,
        name=source.name,
        repository=source.repository,
        ref=source.ref,
        auth=source.auth,
        scope=source.scope,
        user_id=source.user_id,
        team_id=source.team_id,
        status=source.status,
        last_commit=source.last_commit,
        last_synced_at=source.last_synced_at,
        last_error=source.last_error,
        url=repository_url(state, source.repository),
        has_manifest=bool(catalogue and catalogue.has_manifest),
        plugin_count=len(catalogue.plugins) if catalogue else 0,
        skill_count=len(catalogue.entries) if catalogue else 0,
        imported_count=len(await store.links_of_source(source.org_id, source.id)),
        can_manage=may_manage(viewer, source),
        notes=list(catalogue.notes) if catalogue else [],
    )


@router.get("/external-sources", response_model=list[ExternalSourceResponse])
async def list_external_sources(request: Request) -> list[ExternalSourceResponse]:
    """The repositories connected as sources that the caller may see and import from."""
    org_id, viewer = await request_viewer(request)
    state = request.app.state
    sources = await visible_sources(getattr(state, "db_manager", None), org_id, viewer)
    return [await _response(state, s, viewer) for s in sources]


@router.post("/external-sources", response_model=ExternalSourceResponse)
async def connect_external_source(
    req: ExternalSourceRequest, request: Request
) -> ExternalSourceResponse:
    """Connect a repository as a source and read it at once; a repository that cannot be read
    is still connected, with the reason in ``last_error``."""
    org_id, viewer = await request_viewer(request)
    try:
        source, _ = await create_source(
            request.app.state,
            org_id,
            viewer,
            name=req.name,
            repository=req.repository,
            ref=req.ref,
            auth=req.auth,
            scope=req.scope,
            team_id=req.team_id,
        )
    except SourceError as err:
        raise _http(err) from err
    return await _response(request.app.state, source, viewer)


@router.put("/external-sources/{source_id}", response_model=ExternalSourceResponse)
async def update_external_source(
    source_id: str, req: ExternalSourceUpdate, request: Request
) -> ExternalSourceResponse:
    """Rename a source or change the branch it follows."""
    org_id, viewer = await request_viewer(request)
    try:
        source = await update_source(
            request.app.state, org_id, viewer, source_id, name=req.name, ref=req.ref
        )
    except SourceError as err:
        raise _http(err) from err
    return await _response(request.app.state, source, viewer)


@router.delete("/external-sources/{source_id}")
async def delete_external_source(source_id: str, request: Request) -> dict[str, bool]:
    """Disconnect a source. Its skills stay, no longer linked: they can then be edited here."""
    org_id, viewer = await request_viewer(request)
    try:
        await delete_source(request.app.state, org_id, viewer, source_id)
    except SourceError as err:
        raise _http(err) from err
    return {"deleted": True}


@router.post("/external-sources/{source_id}/sync", response_model=SyncResponse)
async def synchronise_external_source(source_id: str, request: Request) -> SyncResponse:
    """Read the source again now and bring its imported skills in step with it."""
    org_id, viewer = await request_viewer(request)
    state = request.app.state
    db_mgr = getattr(state, "db_manager", None)
    try:
        source = await managed_source(db_mgr, org_id, viewer, source_id)
    except SourceError as err:
        raise _http(err) from err
    outcome = await synchronise(state, source, force=True)
    after = await external_source_store(db_mgr).get_source(org_id, source_id) or source
    return SyncResponse(
        source=await _response(state, after, viewer),
        updated=outcome.updated,
        missing=outcome.missing,
        invalid=outcome.invalid,
        unchanged=outcome.unchanged,
        error=outcome.error,
    )


async def _imports_by_path(
    state: Any, source: ExternalSource, viewer: Viewer
) -> dict[str, list[CatalogueImport]]:
    db_mgr = getattr(state, "db_manager", None)
    links = await external_source_store(db_mgr).links_of_source(source.org_id, source.id)
    skills = {
        s.id: s
        for s in await skill_responses(
            state, source.org_id, viewer, [link.object_id for link in links]
        )
    }
    found: dict[str, list[CatalogueImport]] = {}
    for link in links:
        skill = skills.get(link.object_id)
        if skill is not None and link.kind == SKILL_KIND:
            found.setdefault(link.path, []).append(
                CatalogueImport(
                    skill_id=skill.id,
                    scope=skill.scope,
                    team_id=skill.team_id,
                    user_id=skill.user_id,
                    status=link.status,
                )
            )
    return found


@router.get("/external-sources/{source_id}/catalogue", response_model=CatalogueResponse)
async def external_source_catalogue(source_id: str, request: Request) -> CatalogueResponse:
    """What the source offers as of its last read: plugins (when it has a manifest), every
    skill, and where the caller can see each one already imported."""
    org_id, viewer = await request_viewer(request)
    state = request.app.state
    try:
        source = await visible_source(getattr(state, "db_manager", None), org_id, viewer, source_id)
    except SourceError as err:
        raise _http(err) from err
    catalogue = source.catalogue
    imports = await _imports_by_path(state, source, viewer)
    return CatalogueResponse(
        source=await _response(state, source, viewer),
        commit=catalogue.commit if catalogue else "",
        has_manifest=bool(catalogue and catalogue.has_manifest),
        plugins=[
            CataloguePluginResponse(name=p.name, description=p.description, entries=list(p.entries))
            for p in (catalogue.plugins if catalogue else ())
        ],
        entries=[
            CatalogueEntryResponse(
                path=e.path,
                name=e.name,
                description=e.description,
                plugins=list(e.plugins),
                files=list(e.files),
                problem=e.problem,
                url=repository_url(state, source.repository, source.ref, e.path),
                imported=imports.get(e.path, []),
            )
            for e in (catalogue.entries if catalogue else ())
        ],
        notes=list(catalogue.notes) if catalogue else [],
    )


@router.post("/external-sources/{source_id}/import", response_model=ImportResponse)
async def import_from_external_source(
    source_id: str, req: ImportRequest, request: Request
) -> ImportResponse:
    """Import skills of the source for one owner. A skill of that owner with the same name is
    reported as a conflict and nothing is imported, unless its folder is in ``merge``: the
    existing skill then takes the source's content and follows it."""
    org_id, viewer = await request_viewer(request)
    state = request.app.state
    db_mgr = getattr(state, "db_manager", None)
    try:
        source = await visible_source(db_mgr, org_id, viewer, source_id)
        owner = await import_owner(db_mgr, org_id, viewer, source, req.scope, req.team_id)
        outcome = await import_entries(
            state, source, req.paths, owner, merge=req.merge, actor_user_id=viewer.user_id
        )
    except SourceError as err:
        raise _http(err) from err
    except SourceImportError as err:
        raise HTTPException(status_code=422, detail=str(err)) from err
    except SourceReadError as err:
        raise HTTPException(status_code=502, detail=str(err)) from err
    imported = await skill_responses(state, org_id, viewer, [s.id for s in outcome.imported])
    return ImportResponse(
        imported=imported,
        conflicts=[
            ImportConflictResponse(path=c.path, name=c.name, skill_id=c.skill_id)
            for c in outcome.conflicts
        ],
    )

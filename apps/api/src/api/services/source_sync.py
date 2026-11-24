"""Importing skills from an external source, and keeping them in step with its repository.

Reading a source refreshes its catalogue (only when its branch moved, unless forced). Importing
copies the chosen entries into skills of one owner, each with a link to its folder; a skill of
that owner with the same name is a conflict, unless the person chose to merge it, in which case
the repository wins. Synchronising updates every linked skill whose folder changed, deactivates
and flags the ones whose folder is gone, and leaves the others alone.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field, replace
from typing import Any

from api.services.audit import AuditService
from api.services.external_sources import external_source_store
from api.services.skill_import import SkillImportError, parse_skill_markdown
from api.services.skill_store import (
    SkillContent,
    StoredSkill,
    get_skill,
    save_skill,
    set_active,
    skills_named,
)
from api.services.source_access import source_access
from api.services.source_catalogue import (
    READ_CONCURRENCY,
    SkillFolder,
    build_catalogue,
    read_text,
    skill_folders,
)
from specifications.interfaces.external_sources import (
    CatalogueEntry,
    ExternalSource,
    ExternalSourceStoreProtocol,
    LinkedFile,
    SourceLink,
    SourceReaderProtocol,
    SourceReadError,
)
from specifications.interfaces.ownership import Owner

SKILL_KIND = "skill"


class SourceImportError(ValueError):
    """A selection that cannot be imported; the message says why (HTTP 422)."""


@dataclass(frozen=True)
class EntryContent:
    """A skill as its folder holds it at one commit."""

    skill: SkillContent
    files: list[LinkedFile]
    # Files of the folder that were not carried (not text, too large), and why.
    skipped: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ImportConflict:
    path: str
    name: str
    skill_id: str


@dataclass
class ImportOutcome:
    imported: list[StoredSkill] = field(default_factory=list)
    conflicts: list[ImportConflict] = field(default_factory=list)


@dataclass
class SyncOutcome:
    commit: str = ""
    updated: int = 0
    missing: int = 0
    invalid: int = 0
    unchanged: int = 0
    error: str = ""


# -- reading -----------------------------------------------------------------------------------


async def _carried_file(
    reader: SourceReaderProtocol, folder: SkillFolder, index: int
) -> LinkedFile:
    file = folder.files[index]
    return LinkedFile(folder.relative(file), await read_text(reader, file), file.executable)


async def read_entry(reader: SourceReaderProtocol, folder: SkillFolder) -> EntryContent:
    """The skill a folder holds: ``SKILL.md`` parsed, its text files carried along."""
    try:
        parsed = parse_skill_markdown(await read_text(reader, folder.document))
    except SkillImportError as err:
        raise SourceReadError(f"{folder.path}/SKILL.md: {err}") from err
    limit = asyncio.Semaphore(READ_CONCURRENCY)

    async def carry(index: int) -> LinkedFile | str:
        async with limit:
            try:
                return await _carried_file(reader, folder, index)
            except SourceReadError as err:
                return f"{folder.relative(folder.files[index])}: {err}"

    results = await asyncio.gather(*(carry(i) for i in range(len(folder.files))))
    return EntryContent(
        skill=SkillContent(parsed.name, parsed.description, parsed.body),
        files=[r for r in results if isinstance(r, LinkedFile)],
        skipped=[r for r in results if isinstance(r, str)],
    )


def _skipped_note(content: EntryContent) -> str:
    return ("Not carried: " + "; ".join(content.skipped)) if content.skipped else ""


async def refresh_catalogue(
    state: Any, source: ExternalSource, *, force: bool = False
) -> tuple[ExternalSource, SourceReaderProtocol, dict[str, SkillFolder] | None]:
    """Read the source's branch; rebuild the catalogue when it moved (or ``force``).

    Returns the updated source, the reader and the folders of the commit when they were listed.
    ``SourceReadError`` propagates; the caller records it on the source."""
    reader = await source_access(state).reader(source)
    commit = await reader.resolve(source.ref)
    folders: dict[str, SkillFolder] | None = None
    catalogue = source.catalogue
    if force or catalogue is None or catalogue.commit != commit:
        catalogue, folders = await build_catalogue(reader, commit, source.catalogue)
    now = int(time.time())
    updated = replace(
        source,
        catalogue=catalogue,
        last_commit=commit,
        last_synced_at=now,
        status="ok",
        last_error="",
        updated_at=now,
    )
    return updated, reader, folders


async def _folders_of(
    reader: SourceReaderProtocol, commit: str, folders: dict[str, SkillFolder] | None
) -> dict[str, SkillFolder]:
    return folders if folders is not None else skill_folders(await reader.list_files(commit))


# -- importing -------------------------------------------------------------------------------


def _entries(source: ExternalSource, paths: list[str]) -> list[CatalogueEntry]:
    catalogue = source.catalogue
    if catalogue is None:
        raise SourceImportError("This source has not been read yet; synchronise it first.")
    if not paths:
        raise SourceImportError("Choose at least one skill to import.")
    entries: list[CatalogueEntry] = []
    for path in dict.fromkeys(paths):
        entry = catalogue.entry(path)
        if entry is None:
            raise SourceImportError(f"'{path}' is not a skill of this source.")
        if entry.problem:
            raise SourceImportError(f"'{entry.name}' cannot be imported: {entry.problem}")
        entries.append(entry)
    names = [e.name for e in entries]
    if duplicate := next((n for n in names if names.count(n) > 1), None):
        raise SourceImportError(f"Two of the chosen skills are named '{duplicate}'; choose one.")
    return entries


async def _conflicts(
    store: ExternalSourceStoreProtocol,
    source: ExternalSource,
    entries: list[CatalogueEntry],
    existing: dict[str, StoredSkill],
    merge: set[str],
) -> list[ImportConflict]:
    links = await store.links_of_objects(
        source.org_id, SKILL_KIND, [s.id for s in existing.values()]
    )
    conflicts: list[ImportConflict] = []
    for entry in entries:
        skill = existing.get(entry.name)
        if skill is None or entry.path in merge:
            continue
        link = links.get(skill.id)
        if link is not None and link.source_id == source.id and link.path == entry.path:
            continue  # importing it again refreshes it
        conflicts.append(ImportConflict(entry.path, entry.name, skill.id))
    return conflicts


def _link_for(
    source: ExternalSource, entry: CatalogueEntry, skill_id: str, content: EntryContent
) -> SourceLink:
    return SourceLink(
        id=f"lnk_{uuid.uuid4().hex[:12]}",
        org_id=source.org_id,
        source_id=source.id,
        kind=SKILL_KIND,
        object_id=skill_id,
        path=entry.path,
        plugin=entry.plugins[0] if entry.plugins else None,
        commit=source.last_commit,
        document_id=entry.document_id,
        digest=entry.digest,
        status="synced",
        problem=_skipped_note(content),
        files=content.files,
    )


async def import_entries(
    state: Any,
    source: ExternalSource,
    paths: list[str],
    owner: Owner,
    *,
    merge: list[str],
    actor_user_id: str,
) -> ImportOutcome:
    """Copy the entries at ``paths`` into skills of ``owner`` (nothing when any conflicts)."""
    db_mgr = getattr(state, "db_manager", None)
    store = external_source_store(db_mgr)
    entries = _entries(source, paths)
    existing = await skills_named(db_mgr, source.org_id, owner, [e.name for e in entries])
    conflicts = await _conflicts(store, source, entries, existing, set(merge))
    if conflicts:
        return ImportOutcome(conflicts=conflicts)
    reader = await source_access(state).reader(source)
    folders = await _folders_of(reader, source.last_commit, None)
    outcome = ImportOutcome()
    for entry in entries:
        folder = folders.get(entry.path)
        if folder is None:
            raise SourceReadError(f"'{entry.path}' left the repository; synchronise the source.")
        content = await read_entry(reader, folder)
        current = existing.get(entry.name)
        skill = await save_skill(
            db_mgr, source.org_id, owner, content.skill, skill_id=current.id if current else None
        )
        await store.save_link(_link_for(source, entry, skill.id, content))
        outcome.imported.append(skill)
    await AuditService(db_mgr).record(
        "external_source.imported",
        org_id=source.org_id,
        actor_user_id=actor_user_id,
        result_summary=f"{len(outcome.imported)} skill(s) from {source.repository}",
        metadata={"source_id": source.id, "paths": [e.path for e in entries]},
    )
    return outcome


# -- synchronising ---------------------------------------------------------------------------


async def _apply_entry(
    db_mgr: Any,
    reader: SourceReaderProtocol,
    link: SourceLink,
    skill: StoredSkill,
    entry: CatalogueEntry,
    folder: SkillFolder,
    commit: str,
) -> SourceLink:
    content = await read_entry(reader, folder)
    name, rename_problem = await _rename(db_mgr, link.org_id, skill, content.skill.name)
    await save_skill(
        db_mgr,
        link.org_id,
        skill.owner,
        replace(content.skill, name=name),
        skill_id=skill.id,
        is_active=True if link.status == "missing" else skill.is_active,
    )
    problems = [p for p in (rename_problem, _skipped_note(content)) if p]
    return replace(
        link,
        commit=commit,
        document_id=entry.document_id,
        digest=entry.digest,
        plugin=entry.plugins[0] if entry.plugins else link.plugin,
        status="synced",
        problem=" ".join(problems),
        files=content.files,
    )


async def _rename(db_mgr: Any, org_id: str, skill: StoredSkill, wanted: str) -> tuple[str, str]:
    if wanted == skill.name:
        return wanted, ""
    taken = await skills_named(db_mgr, org_id, skill.owner, [wanted])
    if wanted in taken:
        return (
            skill.name,
            f"Renamed '{wanted}' in the repository, but another skill here has that name.",
        )
    return wanted, ""


async def _sync_link(
    state: Any,
    reader: SourceReaderProtocol,
    source: ExternalSource,
    link: SourceLink,
    folders: dict[str, SkillFolder],
    outcome: SyncOutcome,
) -> SourceLink | None:
    """The link after this commit; None when its skill no longer exists."""
    db_mgr = getattr(state, "db_manager", None)
    skill = await get_skill(db_mgr, source.org_id, link.object_id)
    if skill is None:
        return None
    assert source.catalogue is not None
    entry = source.catalogue.entry(link.path)
    folder = folders.get(link.path)
    if entry is None or folder is None:
        outcome.missing += 1
        await set_active(db_mgr, source.org_id, skill.id, False)
        return replace(
            link,
            status="missing",
            commit=source.last_commit,
            problem=f"'{link.path}' is no longer in {source.repository} ({source.ref}).",
        )
    if entry.problem:
        outcome.invalid += 1
        return replace(link, status="invalid", commit=source.last_commit, problem=entry.problem)
    if entry.digest == link.digest and link.status == "synced":
        outcome.unchanged += 1
        return replace(link, commit=source.last_commit)
    outcome.updated += 1
    return await _apply_entry(db_mgr, reader, link, skill, entry, folder, source.last_commit)


async def synchronise(state: Any, source: ExternalSource, *, force: bool = False) -> SyncOutcome:
    """Read the source and bring every skill imported from it in step (see the module)."""
    db_mgr = getattr(state, "db_manager", None)
    store = external_source_store(db_mgr)
    try:
        refreshed, reader, folders = await refresh_catalogue(state, source, force=force)
    except SourceReadError as err:
        now = int(time.time())
        await store.record_read(
            replace(source, status="error", last_error=str(err), updated_at=now)
        )
        return SyncOutcome(error=str(err))
    await store.record_read(refreshed)
    outcome = SyncOutcome(commit=refreshed.last_commit)
    links = await store.links_of_source(source.org_id, source.id)
    if not links or (
        folders is None and all(link.commit == refreshed.last_commit for link in links)
    ):
        outcome.unchanged = len(links)
        return outcome
    every_folder = await _folders_of(reader, refreshed.last_commit, folders)
    for link in links:
        try:
            after = await _sync_link(state, reader, refreshed, link, every_folder, outcome)
        except SourceReadError as err:
            after = replace(link, status="invalid", problem=str(err))
            outcome.invalid += 1
        if after is None:
            await store.delete_link(source.org_id, SKILL_KIND, link.object_id)
        else:
            await store.save_link(after)
    await _audit_sync(db_mgr, refreshed, outcome)
    return outcome


async def _audit_sync(db_mgr: Any, source: ExternalSource, outcome: SyncOutcome) -> None:
    if not (outcome.updated or outcome.missing or outcome.invalid):
        return
    await AuditService(db_mgr).record(
        "external_source.synced",
        org_id=source.org_id,
        result_summary=(
            f"{source.repository}@{outcome.commit[:7]}: {outcome.updated} updated, "
            f"{outcome.missing} missing, {outcome.invalid} invalid"
        ),
        metadata={"source_id": source.id, "commit": outcome.commit},
    )

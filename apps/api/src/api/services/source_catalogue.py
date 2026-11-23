"""What a repository offers: its skills, grouped by the plugins its manifest declares.

A skill is a folder holding a ``SKILL.md`` (the Agent Skills format), anywhere in the
repository; its other files (scripts, references) travel with it. A folder inside another
skill's folder belongs to that skill. Without ``.claude-plugin/marketplace.json`` every skill is
offered on its own; with one, each plugin it declares groups the skills under its folders, and
the skills no plugin claims are still offered. No layout is assumed beyond that.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import posixpath
from collections.abc import Iterable
from dataclasses import dataclass, replace
from typing import Any

from api.services.skill_import import SkillImportError, parse_skill_markdown
from specifications.interfaces.external_sources import (
    Catalogue,
    CatalogueEntry,
    CataloguePlugin,
    SourceFile,
    SourceReaderProtocol,
    SourceReadError,
)

MANIFEST_PATH = ".claude-plugin/marketplace.json"
SKILL_DOCUMENT = "SKILL.md"
# Plugins without a ``skills`` list keep their skills here (the Claude Code plugin layout).
DEFAULT_PLUGIN_SKILLS = ("skills",)
READ_CONCURRENCY = 8


@dataclass(frozen=True)
class SkillFolder:
    """A skill's folder at one commit: its ``SKILL.md`` and its other files."""

    path: str
    document: SourceFile
    files: tuple[SourceFile, ...]

    def relative(self, file: SourceFile) -> str:
        return posixpath.relpath(file.path, self.path)


@dataclass(frozen=True)
class _PluginSpec:
    name: str
    description: str
    folders: tuple[str, ...]


def skill_folders(files: Iterable[SourceFile]) -> dict[str, SkillFolder]:
    """Every skill folder of a tree, by path; a nested ``SKILL.md`` stays its parent's file."""
    every = list(files)
    documents = {
        posixpath.dirname(f.path): f
        for f in every
        if posixpath.basename(f.path) == SKILL_DOCUMENT and posixpath.dirname(f.path)
    }
    outermost = sorted(
        folder
        for folder in documents
        if not any(folder.startswith(other + "/") for other in documents if other != folder)
    )
    contents: dict[str, list[SourceFile]] = {folder: [] for folder in outermost}
    for file in every:
        owner = next((f for f in outermost if file.path.startswith(f + "/")), None)
        if owner is not None and file.path != f"{owner}/{SKILL_DOCUMENT}":
            contents[owner].append(file)
    return {
        folder: SkillFolder(folder, documents[folder], tuple(sorted(found, key=lambda f: f.path)))
        for folder, found in contents.items()
    }


def digest_of(folder: SkillFolder) -> str:
    """Changes whenever ``SKILL.md`` or any other file of the folder changes (blob ids are
    content hashes), so an unchanged skill is recognised without reading it."""
    parts = [f"{SKILL_DOCUMENT}:{folder.document.blob_id}"]
    parts += [f"{folder.relative(f)}:{f.blob_id}:{int(f.executable)}" for f in folder.files]
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()


async def read_text(reader: SourceReaderProtocol, file: SourceFile) -> str:
    """A file's text; ``SourceReadError`` when it is not UTF-8 text."""
    raw = await reader.read(file)
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as err:
        raise SourceReadError(f"{file.path} is not UTF-8 text.") from err


async def _describe(
    reader: SourceReaderProtocol, folder: SkillFolder, known: dict[str, CatalogueEntry]
) -> CatalogueEntry:
    entry = CatalogueEntry(
        kind="skill",
        path=folder.path,
        name=posixpath.basename(folder.path),
        files=tuple(folder.relative(f) for f in folder.files),
        document_id=folder.document.blob_id,
        digest=digest_of(folder),
    )
    cached = known.get(folder.document.blob_id)
    if cached is not None:
        # Same SKILL.md as the last read: its name, description and validity are the same.
        return replace(
            entry, name=cached.name, description=cached.description, problem=cached.problem
        )
    try:
        parsed = parse_skill_markdown(await read_text(reader, folder.document))
    except (SkillImportError, SourceReadError) as err:
        return replace(entry, problem=str(err))
    return replace(entry, name=parsed.name, description=parsed.description)


def _local_folder(base: str, relative: str) -> str | None:
    joined = posixpath.normpath(posixpath.join(base, relative))
    if joined.startswith("..") or joined.startswith("/"):
        return None
    return "" if joined == "." else joined


def _plugin_spec(raw: Any) -> tuple[_PluginSpec | None, str]:
    if not isinstance(raw, dict) or not str(raw.get("name") or "").strip():
        return None, "A plugin of the manifest has no name; it was left out."
    name = str(raw["name"]).strip()
    source = raw.get("source", ".")
    if not isinstance(source, str):
        return None, (
            f"Plugin '{name}' lives in another repository; connect that repository to offer it."
        )
    outside = f"Plugin '{name}' points outside the repository; it was left out."
    base = _local_folder(".", source)
    if base is None:
        return None, outside
    skills = raw.get("skills")
    listed = [str(s) for s in skills] if isinstance(skills, list) else list(DEFAULT_PLUGIN_SKILLS)
    folders = tuple(f for f in (_local_folder(base or ".", s) for s in listed) if f is not None)
    if not folders:
        return None, outside
    return _PluginSpec(name, str(raw.get("description") or ""), folders), ""


def parse_manifest(text: str) -> tuple[list[_PluginSpec], list[str]]:
    """The plugins a ``marketplace.json`` declares, and what was left out."""
    try:
        manifest = json.loads(text)
    except ValueError as err:
        return [], [f"{MANIFEST_PATH} is not valid JSON ({err}); skills are offered on their own."]
    raw_plugins = manifest.get("plugins") if isinstance(manifest, dict) else None
    if not isinstance(raw_plugins, list):
        return [], [f"{MANIFEST_PATH} lists no plugins; skills are offered on their own."]
    specs: list[_PluginSpec] = []
    notes: list[str] = []
    for raw in raw_plugins:
        spec, note = _plugin_spec(raw)
        if spec is not None:
            specs.append(spec)
        if note:
            notes.append(note)
    return specs, notes


def _within(path: str, folder: str) -> bool:
    return folder == "" or path == folder or path.startswith(folder + "/")


def group_by_plugin(
    entries: list[CatalogueEntry], specs: list[_PluginSpec]
) -> tuple[list[CatalogueEntry], list[CataloguePlugin]]:
    """Each entry with the plugins whose folders hold it, and each plugin with its entries."""
    plugins = [
        CataloguePlugin(
            name=spec.name,
            description=spec.description,
            entries=tuple(e.path for e in entries if any(_within(e.path, f) for f in spec.folders)),
        )
        for spec in specs
    ]
    grouped = [
        replace(e, plugins=tuple(p.name for p in plugins if e.path in p.entries)) for e in entries
    ]
    return grouped, plugins


async def build_catalogue(
    reader: SourceReaderProtocol, commit: str, previous: Catalogue | None = None
) -> tuple[Catalogue, dict[str, SkillFolder]]:
    """The catalogue of ``commit`` and its skill folders (for importing from it).

    ``SKILL.md`` files already read in ``previous`` (same blob id) are not read again."""
    files = await reader.list_files(commit)
    folders = skill_folders(files)
    known = {e.document_id: e for e in (previous.entries if previous else ()) if e.document_id}
    limit = asyncio.Semaphore(READ_CONCURRENCY)

    async def describe(folder: SkillFolder) -> CatalogueEntry:
        async with limit:
            return await _describe(reader, folder, known)

    entries = list(await asyncio.gather(*(describe(f) for f in folders.values())))
    manifest = next((f for f in files if f.path == MANIFEST_PATH), None)
    notes: list[str] = []
    plugins: list[CataloguePlugin] = []
    if manifest is not None:
        specs, notes = parse_manifest(await read_text(reader, manifest))
        entries, plugins = group_by_plugin(entries, specs)
    catalogue = Catalogue(
        commit=commit,
        has_manifest=manifest is not None,
        plugins=tuple(plugins),
        entries=tuple(sorted(entries, key=lambda e: e.path)),
        notes=tuple(notes),
    )
    return catalogue, folders

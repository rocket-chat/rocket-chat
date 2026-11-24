"""External sources: repositories the platform takes objects from, the repository staying their
source of truth.

A source names a repository and a branch, an owner (see ``ownership.py``) and how the platform
reads it: ``app`` (the organization's GitHub App installation) or ``member`` (the linked GitHub
account of the member who owns a personal source; its objects only go to that member's personal
ones). Reading the repository gives a ``Catalogue``, what the repository offers; a person then
imports the entries they choose, and each imported object keeps a ``SourceLink``.

Synchronisation follows the repository: an entry that changed is updated, one that disappeared
is deactivated and flagged ``missing``. An object linked to a source is never edited on the
platform; a change to it becomes a pull request on the repository, opened with the linked
GitHub account of the member who asks for it.

Skills are the only kind of entry today. The source, the reader, the catalogue and the link
know nothing specific to skills: another kind (agents, knowledge bases) adds a value to
``ENTRY_KINDS``, a way to find its entries in a tree, and the importer of its objects.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Literal, Protocol

from specifications.interfaces.git_host import ChangeRequest

SourceAuth = Literal["app", "member"]
SOURCE_AUTHS: tuple[SourceAuth, ...] = ("app", "member")
# ``pending``: never read yet; ``ok``: the last read succeeded; ``error``: it failed (``last_error``).
SOURCE_STATUSES = ("pending", "ok", "error")
ENTRY_KINDS = ("skill",)
# ``synced``: the object matches the repository; ``missing``: the entry left the repository (the
# object is deactivated); ``invalid``: the entry is there but cannot be read (the object keeps its
# last good content).
LINK_STATUSES = ("synced", "missing", "invalid")


@dataclass(frozen=True)
class SourceFile:
    """A file of a repository at one commit, as its tree lists it."""

    path: str
    # The forge's content id of the file (git's blob id): equal ids mean equal contents.
    blob_id: str
    size: int = 0
    executable: bool = False


@dataclass(frozen=True)
class CatalogueEntry:
    """Something the repository offers: a folder holding one object of ``kind``."""

    kind: str
    # The folder of the entry in the repository (``teams/data/bq-envs``); its identity.
    path: str
    name: str
    description: str = ""
    # The plugins of the repository's manifest that list the entry (none without a manifest).
    plugins: tuple[str, ...] = ()
    # The entry's other files, relative to ``path`` (scripts, references).
    files: tuple[str, ...] = ()
    # The content id of the entry's main document (a skill's ``SKILL.md``).
    document_id: str = ""
    # Changes whenever the main document or any of the files change.
    digest: str = ""
    # Why the entry cannot be imported (an invalid ``SKILL.md``); empty when it can.
    problem: str = ""


@dataclass(frozen=True)
class CataloguePlugin:
    """A collection the repository's manifest (``.claude-plugin/marketplace.json``) declares."""

    name: str
    description: str = ""
    # The ``path`` of each entry the plugin holds.
    entries: tuple[str, ...] = ()


@dataclass(frozen=True)
class Catalogue:
    """What a repository offers at one commit."""

    commit: str
    has_manifest: bool = False
    plugins: tuple[CataloguePlugin, ...] = ()
    entries: tuple[CatalogueEntry, ...] = ()
    # What was left out and why (a plugin hosted in another repository, an unreadable manifest).
    notes: tuple[str, ...] = ()

    def entry(self, path: str) -> CatalogueEntry | None:
        return next((e for e in self.entries if e.path == path), None)


@dataclass
class ExternalSource:
    """A repository connected to the organization, owned like any shared object."""

    id: str
    org_id: str
    name: str
    # ``owner/name`` on the forge.
    repository: str
    # The branch followed.
    ref: str
    auth: SourceAuth = "app"
    scope: str = "org"
    user_id: str | None = None
    team_id: str | None = None
    status: str = "pending"
    last_commit: str = ""
    last_synced_at: int | None = None
    last_error: str = ""
    catalogue: Catalogue | None = None
    # When the periodic synchronisation reads it next (a push webhook brings it forward).
    next_sync_at: int = 0
    created_by: str = ""
    created_at: int = field(default_factory=lambda: int(time.time()))
    updated_at: int = field(default_factory=lambda: int(time.time()))


@dataclass(frozen=True)
class LinkedFile:
    """A file an imported object carries from its entry, relative to the entry's folder."""

    path: str
    content: str
    executable: bool = False


@dataclass
class SourceLink:
    """Where an imported object comes from, and how it stands against the repository."""

    id: str
    org_id: str
    source_id: str
    kind: str
    # The imported object (a skill's id).
    object_id: str
    # The entry's folder in the repository.
    path: str
    plugin: str | None = None
    commit: str = ""
    document_id: str = ""
    digest: str = ""
    status: str = "synced"
    # Why the link is not ``synced``, or what the last synchronisation could not apply.
    problem: str = ""
    files: list[LinkedFile] = field(default_factory=list)
    created_at: int = field(default_factory=lambda: int(time.time()))
    updated_at: int = field(default_factory=lambda: int(time.time()))


class SourceReadError(RuntimeError):
    """The repository could not be read; the message says why, for the person."""


class SourceReaderProtocol(Protocol):
    """Reads one repository on its forge with one credential, never through a sandbox."""

    repository: str

    async def resolve(self, ref: str) -> str:
        """The commit ``ref`` (a branch) points to now."""
        ...

    async def list_files(self, commit: str) -> list[SourceFile]:
        """Every file of the repository at ``commit``."""
        ...

    async def read(self, file: SourceFile) -> bytes:
        """The contents of one file of a ``list_files`` result."""
        ...

    def web_url(self, ref: str, path: str) -> str:
        """Where a person sees ``path`` at ``ref`` on the forge."""
        ...


class SourceWriterProtocol(Protocol):
    """Proposes a change to a repository as a person: a branch, a commit, a pull request."""

    async def propose_file_change(
        self,
        *,
        base: str,
        path: str,
        content: str,
        expected_blob_id: str,
        branch: str,
        title: str,
        body: str,
    ) -> ChangeRequest:
        """Replace ``path`` on a new ``branch`` from ``base`` and open a pull request into it.

        Raises ``SourceReadError`` when ``path`` no longer has ``expected_blob_id`` on ``base``
        (the repository changed since the platform read it) or the forge refuses."""
        ...


class ExternalSourceStoreProtocol(ABC):
    """Durable sources and links of an organization."""

    @abstractmethod
    async def save_source(self, source: ExternalSource) -> ExternalSource:
        """Create or replace a source."""

    @abstractmethod
    async def record_read(self, source: ExternalSource) -> None:
        """Store what reading a source found (status, commit, catalogue, error) and nothing
        else: a push that moved ``next_sync_at`` during the read must not be undone by it."""

    @abstractmethod
    async def get_source(self, org_id: str, source_id: str) -> ExternalSource | None:
        """One source, or None."""

    @abstractmethod
    async def list_sources(self, org_id: str) -> list[ExternalSource]:
        """Every source of the organization (callers filter by ownership)."""

    @abstractmethod
    async def delete_source(self, org_id: str, source_id: str) -> bool:
        """Delete a source and its links (the objects stay, no longer linked)."""

    @abstractmethod
    async def sources_following(
        self, org_id: str, repository: str, ref: str
    ) -> list[ExternalSource]:
        """The organization's sources on ``repository`` (any case) following branch ``ref``."""

    @abstractmethod
    async def schedule(self, org_id: str, source_id: str, next_sync_at: int) -> None:
        """Move a source's next synchronisation."""

    @abstractmethod
    async def claim_due(self, now: int, interval_seconds: int) -> list[ExternalSource]:
        """Sources of every organization due by ``now``, each claimed once across replicas by
        moving its next synchronisation ``interval_seconds`` later."""

    @abstractmethod
    async def save_link(self, link: SourceLink) -> SourceLink:
        """Create or replace a link (one per object)."""

    @abstractmethod
    async def links_of_source(self, org_id: str, source_id: str) -> list[SourceLink]:
        """Every object imported from a source."""

    @abstractmethod
    async def links_of_objects(
        self, org_id: str, kind: str, object_ids: list[str]
    ) -> dict[str, SourceLink]:
        """The links of these objects, by object id (an object without one is absent)."""

    @abstractmethod
    async def delete_link(self, org_id: str, kind: str, object_id: str) -> None:
        """Forget where an object comes from (it was deleted)."""

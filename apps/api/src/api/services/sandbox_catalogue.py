"""The sandboxes an organization offers.

Each entry is an image with a name, a description and capability tags. Sessions pick an entry;
the entry's capabilities decide which optional tools the agent has (``browser`` enables
``browser_inspect``). The catalogue is seeded once per organization from the deployment's
default image (``ROCKET_DEFAULT_SANDBOX_IMAGE``) and any ``sandboxes.default_image`` /
``allowed_images`` the organization had already set, so existing installs keep working.
"""

from __future__ import annotations

import fnmatch
import logging
import re
import time
import uuid
from dataclasses import dataclass, field, fields
from typing import Any, ClassVar

from sandbox_driver.profiles import configured_profiles
from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError

from api.db.models import SandboxCatalogueModel
from api.db.session import DatabaseSessionManager
from api.services.settings import effective_settings

logger = logging.getLogger(__name__)

# What an image can declare, and what a person must know to turn it on.
CAPABILITIES: dict[str, str] = {
    "browser": (
        "Headless browser: lets the agent open a page it built and read its text, console "
        "errors and a screenshot (browser_inspect). The image must include Chromium and "
        "Playwright, for example the rocket-chat-sandbox-browser variant."
    ),
}
MAX_ENTRIES_PER_ORG = 50
MAX_RELEASE_DAYS = 365
_IMAGE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._\-/:@+]{0,254}$")
# A Kubernetes quantity such as 4Gi or 512Mi (also what the Docker driver accepts).
_QUANTITY = re.compile(r"^[0-9]+(\.[0-9]+)?(Ki|Mi|Gi|Ti|K|M|G|T)?$")
# Fields an entry may leave empty to inherit (the organization's setting, or no profile).
_INHERITING = frozenset({"release_after_days"})


class CatalogueError(ValueError):
    """The change is not acceptable as submitted."""


@dataclass
class SandboxEntry:
    id: str
    org_id: str
    name: str
    image: str
    description: str = ""
    capabilities: list[str] = field(default_factory=list)
    is_default: bool = False
    created_at: int = 0
    # An operator-defined placement profile (node pool, tolerations, sizing); empty: none.
    profile: str = ""
    # Overrides of the organization's sandbox resources for this type (an agent's own still
    # wins); empty inherits.
    cpu_limit: str = ""
    memory_limit: str = ""
    disk_size: str = ""
    # Days an idle session keeps its workspace volume; None inherits the organization's.
    release_after_days: int | None = None

    @property
    def capability_set(self) -> frozenset[str]:
        return frozenset(self.capabilities)


_COLUMNS = [f.name for f in fields(SandboxEntry)]
_RUNTIME_FIELDS = frozenset(
    {"profile", "cpu_limit", "memory_limit", "disk_size", "release_after_days"}
)


def validate_entry(name: str, image: str, capabilities: list[str]) -> None:
    if not name.strip():
        raise CatalogueError("A sandbox needs a name.")
    if not _IMAGE.match(image.strip()):
        raise CatalogueError("The image must look like registry/name:tag (no spaces).")
    unknown = sorted(set(capabilities) - set(CAPABILITIES))
    if unknown:
        raise CatalogueError(f"Unknown capability: {', '.join(unknown)}.")


def validate_runtime(entry: SandboxEntry) -> None:
    """The profile exists in this deployment, and the overrides are valid quantities."""
    if entry.profile and entry.profile not in configured_profiles():
        raise CatalogueError(
            f"Unknown sandbox profile '{entry.profile}': profiles are defined by the "
            "deployment's operator (Helm sandbox.profiles)."
        )
    if entry.cpu_limit:
        try:
            if float(entry.cpu_limit) <= 0:
                raise ValueError
        except ValueError as err:
            raise CatalogueError("CPU is a number of cores, such as 2 or 0.5.") from err
    for label, value in (("Memory", entry.memory_limit), ("Disk", entry.disk_size)):
        if value and not _QUANTITY.match(value):
            raise CatalogueError(f"{label} must be a size such as 4Gi or 512Mi.")
    days = entry.release_after_days
    if days is not None and not 0 <= days <= MAX_RELEASE_DAYS:
        raise CatalogueError(f"Release after 0 (never) to {MAX_RELEASE_DAYS} days.")


class SandboxCatalogue:
    """Entries in PostgreSQL (tenant-scoped); a process-local dict without a database."""

    _MEMORY: ClassVar[dict[str, SandboxEntry]] = {}

    def __init__(self, db_manager: DatabaseSessionManager | None) -> None:
        self._db_manager = db_manager
        self._db = (
            db_manager
            if db_manager is not None and getattr(db_manager, "session_factory", None)
            else None
        )

    async def entries(self, org_id: str) -> list[SandboxEntry]:
        """The organization's entries, seeded on first use; the default comes first."""
        entries = await self._read(org_id)
        if not entries:
            entries = await self._seed(org_id)
        return sorted(entries, key=lambda e: (not e.is_default, e.name.lower()))

    async def default(self, org_id: str) -> SandboxEntry:
        entries = await self.entries(org_id)
        return next((e for e in entries if e.is_default), entries[0])

    async def resolve(
        self, org_id: str, *, sandbox_id: str | None = None, image: str | None = None
    ) -> SandboxEntry | None:
        """The entry a session uses: by id, else by the image it names, else the default."""
        entries = await self.entries(org_id)
        if sandbox_id:
            return next((e for e in entries if e.id == sandbox_id), None)
        if image:
            return next((e for e in entries if e.image == image), None)
        return next((e for e in entries if e.is_default), entries[0])

    async def chosen(
        self,
        org_id: str,
        *,
        session_sandbox_id: str | None,
        session_image: str | None,
        agent_sandbox_id: str,
    ) -> SandboxEntry | None:
        """The type chosen by name: the session's own, else its agent's.

        The session's creator (or the Slack channel that names an image) wins; the agent's
        type is the default when they chose nothing. A type deleted since is no choice.
        """
        if session_sandbox_id:
            return await self.resolve(org_id, sandbox_id=session_sandbox_id)
        if agent_sandbox_id and not session_image:
            return await self.resolve(org_id, sandbox_id=agent_sandbox_id)
        return None

    async def image_is_offered(self, org_id: str, image: str, legacy_patterns: list[str]) -> bool:
        """Whether ``image`` may be used: in the catalogue, or matching a legacy allowed pattern."""
        if any(e.image == image for e in await self.entries(org_id)):
            return True
        return any(fnmatch.fnmatchcase(image, pattern) for pattern in legacy_patterns)

    async def create(
        self,
        org_id: str,
        *,
        name: str,
        image: str,
        description: str = "",
        capabilities: list[str] | None = None,
        is_default: bool = False,
        **runtime: Any,
    ) -> SandboxEntry:
        capabilities = sorted(set(capabilities or []))
        validate_entry(name, image, capabilities)
        existing = await self.entries(org_id)
        if len(existing) >= MAX_ENTRIES_PER_ORG:
            raise CatalogueError("This organization has reached its sandbox limit.")
        if any(e.name.lower() == name.strip().lower() for e in existing):
            raise CatalogueError(f"A sandbox named '{name.strip()}' already exists.")
        entry = SandboxEntry(
            id=f"sbx_{uuid.uuid4().hex[:10]}",
            org_id=org_id,
            name=name.strip(),
            image=image.strip(),
            description=description.strip()[:500],
            capabilities=capabilities,
            is_default=False,
            created_at=int(time.time()),
            **{k: v for k, v in runtime.items() if k in _RUNTIME_FIELDS},
        )
        validate_runtime(entry)
        await self._insert(entry)
        if is_default:
            await self.make_default(org_id, entry.id)
            entry.is_default = True
        return entry

    async def update(self, org_id: str, entry_id: str, **changes: Any) -> SandboxEntry:
        entries = await self.entries(org_id)
        entry = next((e for e in entries if e.id == entry_id), None)
        if entry is None:
            raise CatalogueError("No such sandbox.")
        updated = SandboxEntry(
            **{
                **vars(entry),
                **{
                    k: v
                    for k, v in changes.items()
                    if (v is not None or k in _INHERITING) and k != "is_default"
                },
            }
        )
        updated.capabilities = sorted(set(updated.capabilities))
        validate_entry(updated.name, updated.image, updated.capabilities)
        validate_runtime(updated)
        if any(
            e.id != entry_id and e.name.lower() == updated.name.strip().lower() for e in entries
        ):
            raise CatalogueError(f"A sandbox named '{updated.name.strip()}' already exists.")
        await self._write(updated)
        if changes.get("is_default"):
            await self.make_default(org_id, entry_id)
            updated.is_default = True
        return updated

    async def make_default(self, org_id: str, entry_id: str) -> None:
        for entry in await self.entries(org_id):
            wanted = entry.id == entry_id
            if entry.is_default != wanted:
                entry.is_default = wanted
                await self._write(entry)

    async def delete(self, org_id: str, entry_id: str) -> None:
        entries = await self.entries(org_id)
        entry = next((e for e in entries if e.id == entry_id), None)
        if entry is None:
            raise CatalogueError("No such sandbox.")
        if len(entries) == 1:
            raise CatalogueError("An organization needs at least one sandbox.")
        if entry.is_default:
            raise CatalogueError("Make another sandbox the default before removing this one.")
        await self._remove(org_id, entry_id)

    # -- seeding -----------------------------------------------------------------------------

    async def _seed(self, org_id: str) -> list[SandboxEntry]:
        """Create the first entries from the settings that used to hold the image list."""
        config = await effective_settings(self._db_manager, org_id, "sandboxes")
        default_image = str(config["default_image"])
        seeds = [("Devbox", default_image, True)]
        for index, image in enumerate(config.get("allowed_images") or [], start=1):
            literal = str(image)
            if literal != default_image and "*" not in literal and _IMAGE.match(literal):
                seeds.append((f"Image {index}", literal, False))
        for name, image, is_default in seeds:
            entry = SandboxEntry(
                id=f"sbx_{uuid.uuid4().hex[:10]}",
                org_id=org_id,
                name=name,
                image=image,
                description=(
                    "The platform's default development environment." if is_default else ""
                ),
                capabilities=[],
                is_default=is_default,
                created_at=int(time.time()),
            )
            try:
                await self._insert(entry)
            except IntegrityError:
                logger.debug("Sandbox catalogue of %s was seeded concurrently", org_id)
        return await self._read(org_id)

    # -- storage -----------------------------------------------------------------------------

    async def _read(self, org_id: str) -> list[SandboxEntry]:
        if self._db is None:
            return [e for e in self._MEMORY.values() if e.org_id == org_id]
        async with self._db.session(tenant_org_id=org_id) as session:
            stmt = select(SandboxCatalogueModel).where(SandboxCatalogueModel.org_id == org_id)
            return [
                SandboxEntry(**{c: getattr(row, c) for c in _COLUMNS})
                for row in (await session.execute(stmt)).scalars()
            ]

    async def _insert(self, entry: SandboxEntry) -> None:
        if self._db is None:
            self._MEMORY[entry.id] = entry
            return
        async with self._db.session(tenant_org_id=entry.org_id) as session:
            session.add(SandboxCatalogueModel(**{c: getattr(entry, c) for c in _COLUMNS}))

    async def _write(self, entry: SandboxEntry) -> None:
        if self._db is None:
            self._MEMORY[entry.id] = entry
            return
        async with self._db.session(tenant_org_id=entry.org_id) as session:
            await session.execute(
                update(SandboxCatalogueModel)
                .where(SandboxCatalogueModel.id == entry.id)
                .values(**{c: getattr(entry, c) for c in _COLUMNS if c not in ("id", "org_id")})
            )

    async def _remove(self, org_id: str, entry_id: str) -> None:
        if self._db is None:
            self._MEMORY.pop(entry_id, None)
            return
        async with self._db.session(tenant_org_id=org_id) as session:
            await session.execute(
                delete(SandboxCatalogueModel).where(SandboxCatalogueModel.id == entry_id)
            )

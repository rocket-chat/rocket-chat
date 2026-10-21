"""Setups an organization defines for folders of its repositories, and running them on time.

A skill can say how to prepare a service ("run `vendir sync -l`, download the protocol modules,
`make base`, then `make mocks`"), but an agent can skip a step: one did, `make base` failed, and it
pushed to CI blind three times. A repository setup is the same steps as commands the platform runs
itself, once per workspace, right before the first command the agent runs in a matching folder
(like a session-start hook). The per-directory marker of ``repo_setup.run_setup`` makes it run once,
and again when the commands change or the workspace was released.

Unlike a repository's own ``.rocket/setup.sh``, these commands are written by the organization's
administrators, so they run with the agent's own access (the git gateway included): private
modules can be downloaded.
"""

from __future__ import annotations

import logging
import posixpath
import time
import uuid
from fnmatch import fnmatchcase
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import delete, select

from api.db.models import RepositorySetupModel
from api.services.repo_setup import SetupPlan, run_setup
from specifications.interfaces.agent import FolderSetupOutcome
from specifications.interfaces.sandbox import SandboxDriverProtocol

logger = logging.getLogger(__name__)

WORKSPACE = "/workspace"
MAX_SETUP_SECONDS = 3600
SOURCE = "organization setup"

# Without a database (development and tests) setups live in process memory, keyed by organization.
_memory_setups: dict[str, dict[str, RepositorySetupModel]] = {}


class RepositorySetup(BaseModel):
    """How to prepare a folder of a repository before the agent works there."""

    id: str = Field(default="", description="Identifier; empty to create")
    repository: str = Field(
        ..., min_length=3, max_length=255, description="owner/name, or a pattern such as acme/*"
    )
    folder: str = Field(
        default="",
        max_length=255,
        description="Folder inside the repository, or a pattern such as * for each top-level "
        "folder; empty for the repository's root",
    )
    command: str = Field(
        ..., min_length=1, max_length=8000, description="Bash commands, run in that folder"
    )
    timeout_seconds: int = Field(default=900, ge=10, le=MAX_SETUP_SECONDS)
    enabled: bool = Field(default=True)
    updated_at: int = Field(default=0, description="Last change, epoch seconds")


def _to_model(row: RepositorySetupModel) -> RepositorySetup:
    return RepositorySetup(
        id=row.id,
        repository=row.repository,
        folder=row.folder,
        command=row.command,
        timeout_seconds=row.timeout_seconds,
        enabled=row.enabled,
        updated_at=row.updated_at,
    )


class RepositorySetupStore:
    def __init__(self, db_mgr: Any) -> None:
        self._db = db_mgr

    async def list(self, org_id: str) -> list[RepositorySetup]:
        if self._db is None:
            rows = list(_memory_setups.get(org_id, {}).values())
        else:
            async with self._db.session(tenant_org_id=org_id) as session:
                result = await session.execute(
                    select(RepositorySetupModel).where(RepositorySetupModel.org_id == org_id)
                )
                rows = list(result.scalars().all())
        return sorted((_to_model(r) for r in rows), key=lambda s: (s.repository, s.folder))

    async def save(self, org_id: str, setup: RepositorySetup) -> RepositorySetup:
        now = int(time.time())
        setup_id = setup.id or f"rsetup_{uuid.uuid4().hex[:16]}"
        fields = {
            "repository": setup.repository.strip().lower(),
            "folder": setup.folder.strip().strip("/"),
            "command": setup.command,
            "timeout_seconds": setup.timeout_seconds,
            "enabled": setup.enabled,
            "updated_at": now,
        }
        if self._db is None:
            bucket = _memory_setups.setdefault(org_id, {})
            row = bucket.get(setup_id) or RepositorySetupModel(
                id=setup_id, org_id=org_id, created_at=now
            )
            for key, value in fields.items():
                setattr(row, key, value)
            bucket[setup_id] = row
            return _to_model(row)
        async with self._db.session(tenant_org_id=org_id) as session:
            row = await session.get(RepositorySetupModel, setup_id)
            if row is None or row.org_id != org_id:
                row = RepositorySetupModel(id=setup_id, org_id=org_id, created_at=now)
                session.add(row)
            for key, value in fields.items():
                setattr(row, key, value)
            await session.flush()
            return _to_model(row)

    async def delete(self, org_id: str, setup_id: str) -> bool:
        if self._db is None:
            return _memory_setups.get(org_id, {}).pop(setup_id, None) is not None
        async with self._db.session(tenant_org_id=org_id) as session:
            result = await session.execute(
                delete(RepositorySetupModel).where(
                    RepositorySetupModel.org_id == org_id, RepositorySetupModel.id == setup_id
                )
            )
            return bool(getattr(result, "rowcount", 0))


def matching_folders(
    setups: list[RepositorySetup], repository: str, relative: str
) -> list[tuple[RepositorySetup, str]]:
    """(setup, folder inside the repository) for each enabled setup that applies to a command
    run in ``relative`` (a path inside ``repository``), outermost folder first.

    A folder pattern is matched segment by segment against the command folder and its parents:
    ``*`` applies to the top-level folder the command runs in, ``""`` to the repository's root.
    """
    parts = [p for p in relative.strip("/").split("/") if p]
    candidates = ["/".join(parts[:depth]) for depth in range(len(parts) + 1)]
    found: list[tuple[RepositorySetup, str]] = []
    for setup in setups:
        if not setup.enabled or not fnmatchcase(repository.lower(), setup.repository):
            continue
        depth = 0 if not setup.folder else setup.folder.count("/") + 1
        if depth < len(candidates) and fnmatchcase(candidates[depth], setup.folder or ""):
            found.append((setup, candidates[depth]))
    return sorted(found, key=lambda item: item[1].count("/") if item[1] else -1)


async def repository_of(driver: SandboxDriverProtocol, session_id: str, root: str) -> str:
    """``owner/name`` of the repository checked out at ``root``, from its origin remote."""
    result = await driver.exec_command(
        session_id, "git remote get-url origin", workdir=root, timeout_seconds=10
    )
    url = (result.stdout or "").strip().removesuffix(".git")
    if result.exit_code != 0 or "/" not in url:
        return ""
    owner_name = url.replace(":", "/").split("/")[-2:]
    return "/".join(owner_name).lower()


async def prepare_folder(
    driver: SandboxDriverProtocol,
    session_id: str,
    setups: list[RepositorySetup],
    folder: str,
    agent_env: dict[str, str] | None = None,
) -> FolderSetupOutcome | None:
    """Run the setups that apply to a command in ``folder`` and have not run yet.

    Returns what ran (or what failed, with its output: the command then does not run), or
    ``None`` when nothing applied or everything had already run.
    """
    if not setups or not folder.startswith(f"{WORKSPACE}/"):
        return None
    relative = folder[len(WORKSPACE) + 1 :]
    name, _, inside = relative.partition("/")
    root = posixpath.join(WORKSPACE, name)
    repository = await repository_of(driver, session_id, root)
    notes: list[str] = []
    for setup, sub in matching_folders(setups, repository, inside) if repository else []:
        directory = posixpath.join(root, sub) if sub else root
        plan = SetupPlan(source=SOURCE, command=setup.command)
        started = time.monotonic()
        ok, output = await run_setup(
            driver,
            session_id,
            plan,
            directory,
            timeout_seconds=setup.timeout_seconds,
            trusted_env=dict(agent_env or {}),
        )
        where = posixpath.relpath(directory, WORKSPACE)
        if output == "already done":
            continue
        if not ok:
            return FolderSetupOutcome(
                f"[Setup of {where} failed (the {SOURCE}): your command did not run. Fix the "
                f"setup before building or testing there]\n{output}",
                ok=False,
            )
        notes.append(f"[Setup of {where} ran ({int(time.monotonic() - started)} s)]")
    return FolderSetupOutcome("\n".join(notes), ok=True) if notes else None

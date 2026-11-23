"""The files a skill brings from its repository (scripts, references), where the agent finds them.

A skill imported from an external source carries the other files of its folder. When the agent
loads the skill, they are written under ``/workspace/.rocket/skills/<name>/`` in the sandbox
(executables stay executable), and the skill's instructions end with a note naming that folder
and the files, so paths the instructions give relative to their folder resolve there.

The note is part of the skill's text wherever that text is computed (loading it, and checking
at each turn whether it changed), so a skill whose files did not change is not reported as
edited. The note names at most ``MAX_LISTED_FILES`` files: it is re-sent on every step.
"""

from __future__ import annotations

import posixpath
import shlex
from collections.abc import Awaitable, Callable
from typing import Any

from api.services.external_sources import external_source_store
from specifications.interfaces.external_sources import LinkedFile
from specifications.interfaces.sandbox import SandboxDriverProtocol

SKILLS_ROOT = "/workspace/.rocket/skills"
MAX_LISTED_FILES = 30
CHMOD_TIMEOUT_SECONDS = 20


def folder_of(name: str) -> str:
    return f"{SKILLS_ROOT}/{name}"


async def files_of(db_mgr: Any, org_id: str, skill_id: str) -> list[LinkedFile]:
    """The files a skill carries from its source (none for a skill without one)."""
    links = await external_source_store(db_mgr).links_of_objects(org_id, "skill", [skill_id])
    link = links.get(skill_id)
    return list(link.files) if link is not None else []


def files_note(name: str, files: list[LinkedFile]) -> str:
    """The lines appended to the skill's instructions; empty without files."""
    if not files:
        return ""
    listed = [f"{f.path} (executable)" if f.executable else f.path for f in files]
    shown = ", ".join(listed[:MAX_LISTED_FILES])
    more = len(listed) - MAX_LISTED_FILES
    if more > 0:
        shown += f", and {more} more (list the folder to see them)"
    return (
        f"\n\n---\nThis skill's files are in `{folder_of(name)}/` in the sandbox; paths in "
        f"these instructions are relative to that folder: {shown}."
    )


def with_files(name: str, body: str, files: list[LinkedFile]) -> str:
    return body.strip() + files_note(name, files)


def _safe_path(name: str, file: LinkedFile) -> str | None:
    """The file's place in the sandbox, or None when its path would leave the skill's folder."""
    relative = posixpath.normpath(file.path)
    if relative.startswith("..") or posixpath.isabs(relative) or relative == ".":
        return None
    return f"{folder_of(name)}/{relative}"


async def put_in_sandbox(
    driver: SandboxDriverProtocol, session_id: str, name: str, files: list[LinkedFile]
) -> None:
    """Write the skill's files into the sandbox, executables made executable."""
    executables: list[str] = []
    for file in files:
        path = _safe_path(name, file)
        if path is None:
            continue
        await driver.write_file(session_id, path, file.content)
        if file.executable:
            executables.append(path)
    if executables:
        await driver.exec_command(
            session_id,
            "chmod +x " + " ".join(shlex.quote(p) for p in executables),
            timeout_seconds=CHMOD_TIMEOUT_SECONDS,
        )


async def load_with_files(
    db_mgr: Any,
    org_id: str,
    skill_id: str,
    name: str,
    body: str,
    *,
    driver: SandboxDriverProtocol | None,
    session_id: str,
    prepare: Callable[[str], Awaitable[None]],
) -> str:
    """What loading an instruction skill gives the agent: its instructions and the note on its
    files, once they are in the sandbox (``prepare`` starts it). Without a sandbox the note is
    the same, so the text is the same as the one checked at the next turn."""
    files = await files_of(db_mgr, org_id, skill_id)
    if files and driver is not None:
        await prepare(session_id)
        await put_in_sandbox(driver, session_id, name, files)
    return with_files(name, body, files)

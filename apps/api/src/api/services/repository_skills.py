"""Skills a repository brings: ``.rocket/skills/<name>/SKILL.md`` (and ``.claude/skills/`` for
repositories written for Claude Code), found when the agent clones it.

Only their names and one-line descriptions enter the conversation (in the clone's result); the
agent reads a skill with ``execute_skill`` like any other, from the sandbox at that moment, clamped
like a repository's AGENTS.md. Bounded: ``MAX_SKILLS`` per repository, ``MAX_DESCRIPTION_CHARS``
each. ``general.repository_skills`` off ignores them.
"""

from __future__ import annotations

import posixpath
import re
import shlex
from typing import Any

from agent_core.tools.project_instructions import clamp_instructions

from specifications.interfaces.sandbox import SandboxDriverProtocol
from specifications.interfaces.session import SessionRecord

SKILL_DIRECTORIES = (".rocket/skills", ".claude/skills")
SKILL_FILE = "SKILL.md"
REPOSITORY_SKILLS_KEY = "repository_skills"
MAX_SKILLS = 20
MAX_DESCRIPTION_CHARS = 200
LIST_TIMEOUT_SECONDS = 20
_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
_FRONTMATTER = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def _frontmatter(text: str) -> dict[str, str]:
    match = _FRONTMATTER.match(text)
    if not match:
        return {}
    fields: dict[str, str] = {}
    for line in match.group(1).splitlines():
        key, sep, value = line.partition(":")
        if sep:
            fields[key.strip().lower()] = value.strip().strip("\"'")
    return fields


def _first_line(text: str) -> str:
    body = _FRONTMATTER.sub("", text, count=1)
    return next((line.strip("# ").strip() for line in body.splitlines() if line.strip()), "")


async def discover(
    driver: SandboxDriverProtocol, session_id: str, folder: str
) -> dict[str, dict[str, str]]:
    """``{name: {path, description}}`` of the repository's skills (``.rocket`` wins a clash)."""
    directories = " ".join(shlex.quote(posixpath.join(folder, d)) for d in SKILL_DIRECTORIES)
    command = (
        f'for d in {directories}; do [ -d "$d" ] && '
        f'find "$d" -mindepth 2 -maxdepth 2 -name {SKILL_FILE} -type f; done 2>/dev/null '
        f"| head -n {MAX_SKILLS * 2}"
    )
    listed = await driver.exec_command(session_id, command, timeout_seconds=LIST_TIMEOUT_SECONDS)
    found: dict[str, dict[str, str]] = {}
    for path in (listed.stdout or "").splitlines():
        path = path.strip()
        name = posixpath.basename(posixpath.dirname(path))
        if not path or not _NAME.match(name) or name in found or len(found) >= MAX_SKILLS:
            continue
        text = await _read(driver, session_id, path)
        meta = _frontmatter(text)
        description = (meta.get("description") or _first_line(text))[:MAX_DESCRIPTION_CHARS]
        declared = meta.get("name") or ""
        found[declared if _NAME.match(declared) else name] = {
            "path": path,
            "description": description,
        }
    return found


async def _read(driver: SandboxDriverProtocol, session_id: str, path: str) -> str:
    try:
        return await driver.read_file(session_id, path)
    except Exception:
        return ""


def describe(found: dict[str, dict[str, str]], folder: str) -> str:
    """The note a clone's result carries: one line per skill."""
    if not found:
        return ""
    lines = [f"- {name}: {entry['description']}" for name, entry in sorted(found.items())]
    return (
        f"{folder} brings its own skills; read one with execute_skill when its task comes up:\n"
        + "\n".join(lines)
    )


def remember(found: dict[str, dict[str, str]]) -> Any:
    """Add the skills to the session's (a later clone's skills win a name)."""

    def update(record: SessionRecord) -> None:
        known = record.metadata if isinstance(record.metadata, dict) else {}
        current = dict(known.get(REPOSITORY_SKILLS_KEY) or {})
        current.update(found)
        record.metadata = {**known, REPOSITORY_SKILLS_KEY: current}

    return update


async def read_skill(
    driver: SandboxDriverProtocol | None, record: SessionRecord | None, name: str
) -> str | None:
    """A repository skill's instructions, read now and clamped; None when it is not one."""
    metadata = record.metadata if record is not None and isinstance(record.metadata, dict) else {}
    entry = (metadata.get(REPOSITORY_SKILLS_KEY) or {}).get(name)
    if driver is None or record is None or not isinstance(entry, dict):
        return None
    path = str(entry.get("path") or "")
    text = await _read(driver, record.session_id, path)
    return clamp_instructions(_FRONTMATTER.sub("", text, count=1), path) if text.strip() else None

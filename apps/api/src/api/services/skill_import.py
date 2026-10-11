"""Import a prompt skill from a ``SKILL.md`` document.

``SKILL.md`` is the de-facto portable format for agent skills: YAML front matter carrying
``name`` and ``description``, then the instructions as Markdown. Importing it keeps skills
authored for other agent tools usable here without a conversion step.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import yaml

MAX_SKILL_DOCUMENT_BYTES = 64 * 1024
_NAME_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")
_FRONT_MATTER = re.compile(r"\A---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|\Z)(.*)\Z", re.DOTALL)


class SkillImportError(ValueError):
    """The document is not a valid SKILL.md."""


@dataclass(frozen=True)
class ImportedSkill:
    name: str
    description: str
    body: str


def parse_skill_markdown(document: str) -> ImportedSkill:
    if len(document.encode("utf-8")) > MAX_SKILL_DOCUMENT_BYTES:
        raise SkillImportError(f"SKILL.md is larger than {MAX_SKILL_DOCUMENT_BYTES // 1024} KiB.")
    match = _FRONT_MATTER.match(document.lstrip("﻿"))
    if match is None:
        raise SkillImportError("SKILL.md must start with a YAML front matter block (---).")
    try:
        meta = yaml.safe_load(match.group(1))
    except yaml.YAMLError as err:
        raise SkillImportError(f"Front matter is not valid YAML: {err}") from err
    if not isinstance(meta, dict):
        raise SkillImportError("Front matter must be a mapping with 'name' and 'description'.")

    name = str(meta.get("name") or "").strip()
    if not _NAME_PATTERN.fullmatch(name):
        raise SkillImportError(
            "Skill 'name' is required: letters, digits, '-' or '_', at most 64 characters."
        )
    body = match.group(2).strip()
    if not body:
        raise SkillImportError("SKILL.md has no instructions after the front matter.")
    return ImportedSkill(
        name=name, description=str(meta.get("description") or "").strip(), body=body
    )

"""A change to a skill linked to an external source: a pull request on its repository.

The change is never saved on the platform. The skill's ``SKILL.md`` is rewritten (its front
matter kept as written unless the name or description change) on a new branch from the branch
the source follows, and a pull request is opened with the linked GitHub account of the member
who asks: the repository's own review decides, and the skill follows once it is merged.
"""

from __future__ import annotations

import re
import uuid
from typing import Any

import yaml

from api.services.audit import AuditService
from api.services.skill_sources import link_and_source
from api.services.skill_store import SkillContent, get_skill
from api.services.source_access import source_access
from api.services.source_catalogue import SKILL_DOCUMENT, read_text
from specifications.interfaces.external_sources import SourceFile, SourceReadError
from specifications.interfaces.git_host import ChangeRequest
from specifications.interfaces.ownership import Viewer, can_see

_FRONT_MATTER = re.compile(
    r"\A(?P<head>---[ \t]*\r?\n(?P<yaml>.*?)\r?\n---[ \t]*(?:\r?\n|\Z))", re.DOTALL
)


class SkillChangeError(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code, self.detail = status_code, detail


def rewrite_document(document: str, current: SkillContent, wanted: SkillContent) -> str:
    """``document`` (a ``SKILL.md``) carrying ``wanted``; its front matter is kept as written
    when the name and description do not change (comments and key order survive)."""
    match = _FRONT_MATTER.match(document.lstrip("﻿"))
    head = match.group("head") if match else ""
    if (wanted.name, wanted.description) != (current.name, current.description) or not match:
        meta = yaml.safe_load(match.group("yaml")) if match else {}
        fields = dict(meta) if isinstance(meta, dict) else {}
        fields["name"], fields["description"] = wanted.name, wanted.description
        dumped = yaml.safe_dump(fields, sort_keys=False, allow_unicode=True, width=10_000)
        head = f"---\n{dumped}---\n"
    return f"{head}\n{wanted.body.strip()}\n"


async def propose_skill_change(
    state: Any,
    org_id: str,
    viewer: Viewer,
    skill_id: str,
    wanted: SkillContent,
    *,
    summary: str,
    rationale: str = "",
) -> ChangeRequest:
    """Open a pull request that gives the linked skill ``wanted`` (see the module)."""
    db_mgr = getattr(state, "db_manager", None)
    skill = await get_skill(db_mgr, org_id, skill_id)
    if skill is None or not can_see(viewer, skill.owner):
        raise SkillChangeError(404, "Skill not found")
    found = await link_and_source(state, org_id, skill_id)
    if found is None:
        raise SkillChangeError(409, "This skill has no source; edit it directly.")
    link, source = found
    current = SkillContent(skill.name, skill.description, skill.body)
    if wanted == current:
        raise SkillChangeError(422, "The change is the skill as it is.")
    access = source_access(state)
    path = f"{link.path}/{SKILL_DOCUMENT}"
    try:
        writer = await access.writer(org_id, viewer.user_id, source)
        reader = await access.reader(source)
        document = await read_text(reader, SourceFile(path=path, blob_id=link.document_id))
        change = await writer.propose_file_change(
            base=source.ref,
            path=path,
            content=rewrite_document(document, current, wanted),
            expected_blob_id=link.document_id,
            branch=f"rocket/skill-{wanted.name}-{uuid.uuid4().hex[:6]}",
            title=summary.strip() or f"Update the {wanted.name} skill",
            body=_pull_request_body(rationale),
        )
    except SourceReadError as err:
        raise SkillChangeError(409, str(err)) from err
    await AuditService(db_mgr).record(
        "external_source.change_proposed",
        org_id=org_id,
        actor_user_id=viewer.user_id,
        result_summary=f"{source.repository}#{change.number}: skill {skill.name}",
        metadata={"source_id": source.id, "skill_id": skill_id, "url": change.url},
    )
    return change


def _pull_request_body(rationale: str) -> str:
    # The pull request is the member's own (their account opened it): it names no platform id.
    lines = ["Proposed from Rocket Chat."]
    if rationale.strip():
        lines += ["", rationale.strip()]
    lines += ["", "The skill follows this repository: once merged, it is updated on the platform."]
    return "\n".join(lines)

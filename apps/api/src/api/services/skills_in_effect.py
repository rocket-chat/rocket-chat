"""The instruction skills a session has in effect, as they are now."""

from collections.abc import Sequence
from typing import Any

from api.services.skill_files import files_of, with_files
from api.services.skills_lookup import _find_visible_skill
from api.services.tool_loading import skills_read_of
from specifications.interfaces.session import SessionRecord


async def skill_texts_for(
    db_mgr: Any,
    session: SessionRecord,
    allowed_ids: Sequence[str] = (),
    agent_team_id: str | None = None,
) -> dict[str, str]:
    """The current instructions of the skills the session put in effect.

    A skill that was deleted, is no longer visible to the member or is no longer an instruction
    skill is left out; the engine also drops one whose text changed since it was read, so an
    edited skill has to be loaded again.
    """
    texts: dict[str, str] = {}
    for name in skills_read_of(session):
        skill = await _find_visible_skill(
            db_mgr, session.tenant_org_id, session.tenant_user_id, name, allowed_ids, agent_team_id
        )
        if skill is None or (skill.runtime == "python" and skill.code) or not skill.body.strip():
            continue
        # The same text ``execute_skill`` returned: a skill's files are part of it.
        files = await files_of(db_mgr, session.tenant_org_id, skill.id)
        texts[name] = with_files(name, skill.body, files)
    return texts

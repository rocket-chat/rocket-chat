"""The instruction skills a session has in effect, as they are now."""

from collections.abc import Sequence
from typing import Any

from api.services.skills_lookup import _find_visible_skill
from api.services.tool_loading import skills_read_of
from specifications.interfaces.session import SessionRecord


async def skill_texts_for(
    db_mgr: Any, session: SessionRecord, allowed_ids: Sequence[str] = ()
) -> dict[str, str]:
    """The current instructions of the skills the session put in effect.

    A skill that was deleted, is no longer visible to the member or is no longer an instruction
    skill is left out; the engine also drops one whose text changed since it was read, so an
    edited skill has to be loaded again.
    """
    texts: dict[str, str] = {}
    for name in skills_read_of(session):
        skill = await _find_visible_skill(
            db_mgr, session.tenant_org_id, session.tenant_user_id, name, allowed_ids
        )
        if skill is None or (skill.runtime == "python" and skill.code) or not skill.body.strip():
            continue
        texts[name] = skill.body.strip()
    return texts

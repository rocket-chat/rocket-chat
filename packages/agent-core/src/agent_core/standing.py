"""Instructions that stay in effect for a session: loaded skills and a repository's AGENTS.md.

A skill's instructions arrive the way Claude Code delivers them: as the ``execute_skill`` result,
one ``<skill>`` block in the conversation, at the moment the agent decided it needed them. That
message is never pruned to a receipt (``is_skill_result``), and a compaction re-attaches every
skill block of what it replaces (``skill_blocks``), so neither can take the instructions away.
An earlier design rendered the skills into every turn's context note instead; in a long turn the
note sat a hundred steps back, behind the repository's instructions, and the agent broke the
skill's rules while it was there. The note now only carries what the conversation lacks: a
session loaded under that design, and a one-line notice when a skill was edited since it was read.

The text is bounded: one skill at most ``MAX_SKILL_CHARS`` rendered, all skills together at most
``SKILL_BUDGET_CHARS``; beyond it the longest-unused skill is dropped and has to be loaded again.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence

from agent_core.context import ExecutionContext
from agent_core.guidance import digest_of
from agent_core.skill_blocks import (
    SKILL_BUDGET_CHARS,
    skill_blocks,
)
from specifications.interfaces.agent import SkillsInEffect

MAX_SKILL_CHARS = 20_000

_CLOSING_TAG = re.compile(r"</\s*skill\b", re.IGNORECASE)
_SKILL_FOLLOW = "Follow these instructions for the work they cover; they stay in effect for the rest of the session."


def skills_at_turn_start(skills: SkillsInEffect) -> dict[str, str]:
    """The skills the turn starts with, as skill -> instructions.

    What the session put in effect earlier, if the skill has not changed since and the member can
    still use it; within the budget, what was loaded last wins.
    """
    chosen: dict[str, str] = {}
    used = 0
    for name in reversed(list(skills.read)):
        body = skills.texts.get(name)
        if body is None or digest_of(body) != skills.read[name]:
            continue
        size = min(len(body), MAX_SKILL_CHARS)
        if used + size > SKILL_BUDGET_CHARS:
            continue
        used += size
        chosen[name] = body
    return dict(reversed(list(chosen.items())))


def _block(name: str, body: str) -> str:
    # A skill is the organization's text, but it may quote other material: it cannot close its
    # block early and pose as instructions of its own.
    safe = _CLOSING_TAG.sub("<\\/skill", body)
    if len(safe) > MAX_SKILL_CHARS:
        safe = (
            safe[:MAX_SKILL_CHARS].rstrip()
            + f"\n[... cut: {len(body) - MAX_SKILL_CHARS:,} more characters]"
        )
    return f'<skill name="{name}">\n{safe}\n</skill>'


def render_skills(skills: Mapping[str, str], *, added: bool = False) -> str:
    """The skills in effect, for the turn's context note (or what a turn adds to it)."""
    blocks = [_block(name, body) for name, body in skills.items()]
    if not blocks:
        return ""
    if added:
        return "[More skill instructions]\n" + "\n\n".join(blocks)
    return (
        "[Skill instructions in effect]\n"
        "Instructions of the skills you loaded. Follow them for the work they cover; they stay in "
        "effect for the whole session.\n\n" + "\n\n".join(blocks)
    )


def put_skill_in_effect(ctx: ExecutionContext, name: str, body: str) -> bool:
    """Record a skill as in effect for the session; whether it was not already (same text)."""
    text = body.strip()
    if not text or ctx.skill_texts.get(name) == text:
        return False
    ctx.skills_in_effect.pop(name, None)  # loaded again: now the most recent
    ctx.skills_in_effect[name] = digest_of(text)
    ctx.skill_texts[name] = text
    return True


def skill_result(name: str, body: str) -> str:
    """What ``execute_skill`` returns for an instruction skill: its block, kept in the
    conversation for the rest of the session."""
    return f"{_block(name, body.strip())}\n{_SKILL_FOLLOW}"


def skills_missing_from(
    skills: Mapping[str, str], conversation_texts: Sequence[str]
) -> dict[str, str]:
    """The skills in effect whose block the conversation does not carry (a session loaded when
    skills lived in the context note, or a skill dropped by a compaction's budget)."""
    present = skill_blocks(conversation_texts)
    return {name: body for name, body in skills.items() if name not in present}


def changed_skills(skills: SkillsInEffect) -> list[str]:
    """Skills the session read whose text has changed since (edited by the organization)."""
    return [
        name
        for name, digest in skills.read.items()
        if (body := skills.texts.get(name)) is not None and digest_of(body) != digest
    ]


def changed_skills_note(names: Sequence[str]) -> str:
    if not names:
        return ""
    listed = ", ".join(f"'{name}'" for name in names)
    return (
        f"[Skills changed] {listed} changed since you loaded them; the instructions you have are "
        "out of date. Call `execute_skill` again before relying on them."
    )

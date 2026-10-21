"""Instructions that stay in effect for a session without living in its conversation.

A skill's instructions, or a repository's AGENTS.md, used to arrive as a tool result. Tool results
are the first thing the engine prunes to a one-line receipt (and the compaction summarizes the
rest), so past a certain size of context the agent no longer had the instructions it had been told
to follow, and loaded them again or did without. They are kept instead as state of the session and
rendered into each turn's context note, like the guidance of an MCP server (see ``guidance.py``):
rebuilt every turn, never stored in the conversation, so pruning and compaction cannot lose them.

The text is bounded: one skill at most ``MAX_SKILL_CHARS`` rendered, all skills together at most
``SKILL_BUDGET_CHARS``; beyond it the longest-unused skill is dropped and has to be loaded again.
"""

from __future__ import annotations

import re
from collections.abc import Mapping

from agent_core.context import ExecutionContext
from agent_core.guidance import digest_of
from specifications.interfaces.agent import SkillsInEffect

MAX_SKILL_CHARS = 20_000
SKILL_BUDGET_CHARS = 30_000

_CLOSING_TAG = re.compile(r"</\s*skill\b", re.IGNORECASE)


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
    """Put a skill's instructions in effect; whether they were not already (never repeated)."""
    text = body.strip()
    if not text or ctx.skill_texts.get(name) == text:
        return False
    ctx.skills_in_effect.pop(name, None)  # loaded again: now the most recent
    ctx.skills_in_effect[name] = digest_of(text)
    ctx.skill_texts[name] = text
    ctx.standing_pending.append(render_skills({name: text}, added=True))
    return True

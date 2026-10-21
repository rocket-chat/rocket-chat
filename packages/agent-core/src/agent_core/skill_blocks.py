"""Recognising a skill's instructions in a conversation, for pruning and compaction.

A leaf module (no other agent-core import) because the context manager, which every other module
builds on, must leave these messages whole; ``standing`` holds the rest of the skills' handling.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

SKILL_RESULT_PREFIX = '<skill name="'
SKILL_TOOL = "execute_skill"
# All skills in effect together, in characters; past it the longest-unused one is dropped.
SKILL_BUDGET_CHARS = 30_000
_SKILL_BLOCK = re.compile(r'<skill name="([^"]+)">\n[\s\S]*?\n</skill>')


def is_skill_result(tool_name: str, content: str | None) -> bool:
    """Whether a tool result is a skill's instructions, which pruning must leave whole."""
    return tool_name == SKILL_TOOL and (content or "").startswith(SKILL_RESULT_PREFIX)


def skill_blocks(texts: Sequence[str]) -> dict[str, str]:
    """The ``<skill>`` blocks in ``texts``, by skill, the last occurrence of each winning, newest
    first, within ``SKILL_BUDGET_CHARS``."""
    found: dict[str, str] = {}
    for text in texts:
        for match in _SKILL_BLOCK.finditer(text or ""):
            found.pop(match.group(1), None)
            found[match.group(1)] = match.group(0)
    kept: dict[str, str] = {}
    used = 0
    for name, block in reversed(list(found.items())):
        if used + len(block) > SKILL_BUDGET_CHARS:
            continue
        kept[name] = block
        used += len(block)
    return kept

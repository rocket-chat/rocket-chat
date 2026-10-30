"""Command shortcuts typed in a Slack message: ``!fast``, ``!deep``, ``!agent <name>``,
``!repo <owner/name>``, and ``(aside)`` for a message the agent should not answer.

Shortcuts are stripped from the prompt and become choices for the turn. They choose nothing on
their own: the reasoning effort and the agent go through the platform's resolver and access
checks like a choice made in the web app, so a shortcut cannot get around a lock.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

EFFORTS = {"!fast": "low", "!deep": "high"}
_ASIDE = re.compile(r"^\s*\(aside\)", re.IGNORECASE)
_EFFORT = re.compile(r"(?<!\S)(!fast|!deep)(?!\S)", re.IGNORECASE)
_AGENT = re.compile(r"(?<!\S)!agent\s+(\"[^\"]{1,80}\"|[\w.-]{1,80})", re.IGNORECASE)
_REPO = re.compile(r"(?<!\S)!repo\s+([\w.-]{1,100}/[\w.-]{1,100})(?:@([\w./-]{1,100}))?", re.I)


@dataclass(frozen=True)
class Shortcuts:
    """What a message asked for with shortcuts, and its text without them."""

    text: str
    aside: bool = False
    reasoning_effort: str | None = None
    agent_name: str | None = None
    repository: str | None = None
    branch: str | None = None

    @property
    def any(self) -> bool:
        return bool(self.reasoning_effort or self.agent_name or self.repository)


def parse_shortcuts(text: str) -> Shortcuts:
    """Strip the shortcuts from ``text``; the last of each kind wins."""
    if _ASIDE.match(text):
        return Shortcuts(text=text, aside=True)
    effort = None
    for match in _EFFORT.finditer(text):
        effort = EFFORTS[match.group(1).lower()]
    agent = None
    for match in _AGENT.finditer(text):
        agent = match.group(1).strip('"').strip()
    repository = branch = None
    for match in _REPO.finditer(text):
        repository, branch = match.group(1), match.group(2)
    for pattern in (_EFFORT, _AGENT, _REPO):
        text = pattern.sub("", text)
    return Shortcuts(
        # Only the spaces a shortcut left: a message's line breaks (code, lists) are kept.
        text=re.sub(r"[ \t]{2,}", " ", text).strip(),
        reasoning_effort=effort,
        agent_name=agent or None,
        repository=repository,
        branch=branch,
    )

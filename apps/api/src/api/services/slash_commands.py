"""Slash commands: ``/name arguments`` expands a prompt skill into the model's instruction.

The person's message stays exactly as typed in the conversation; only what the model receives
changes. A name that matches no visible text skill is left alone, so a message that merely
starts with a path (``/etc/hosts is odd``) is never rewritten.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_COMMAND = re.compile(r"^/([A-Za-z0-9][A-Za-z0-9_-]{0,63})(?:\s+(.*))?$", re.DOTALL)
MAX_EXPANDED_CHARS = 20_000


@dataclass(frozen=True)
class SlashInvocation:
    name: str
    arguments: str


def parse_slash_command(prompt: str) -> SlashInvocation | None:
    match = _COMMAND.match(prompt.strip())
    if match is None:
        return None
    return SlashInvocation(name=match.group(1), arguments=(match.group(2) or "").strip())


def expand_skill(body: str, arguments: str) -> str:
    """Substitute ``$ARGUMENTS``; arguments given to a body without the placeholder follow it."""
    if "$ARGUMENTS" in body:
        expanded = body.replace("$ARGUMENTS", arguments)
    elif arguments:
        expanded = f"{body}\n\nArguments: {arguments}"
    else:
        expanded = body
    return expanded[:MAX_EXPANDED_CHARS]

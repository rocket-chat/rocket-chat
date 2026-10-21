"""Warnings about an agent's prompt: too long, or naming tools and arguments that do not exist.

A persona's prompt is sent with every step of every turn. One real prompt had grown to 9,000
characters, ending with five paragraphs of an essay explaining the prompt to a reader and teaching
`wait_for(ci_status, pr=…)`, a parameter the tool does not have; the agent followed it. These
checks never block a save: they say what to look at.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence

from pydantic import BaseModel, Field

MAX_PERSONA_CHARS = 4_000
# `name(args)` in the prompt: a call the agent may copy.
_CALL = re.compile(r"\b([a-z][a-z0-9]*(?:_[a-z0-9]+)+)\(([^()\n]*)\)")
_KEYWORD = re.compile(r"\b([a-z_][a-z0-9_]*)\s*=")
# `name` in backticks: a tool the prompt points the agent at.
_CODE = re.compile(r"`([a-z][a-z0-9]*(?:_[a-z0-9]+)+)`")


class PersonaWarning(BaseModel):
    kind: str = Field(..., description="too_long, unknown_tool or unknown_argument")
    message: str = Field(..., description="What to fix, in a sentence")


def _looks_like_a_tool(name: str, prefixes: frozenset[str]) -> bool:
    """A snake_case name sharing a tool's first word (``wait_``, ``git_``…): a likely tool
    name, rather than a code identifier the prompt quotes."""
    return name.split("_", 1)[0] in prefixes


def lint_persona(prompt: str, tools: Mapping[str, Sequence[str]]) -> list[PersonaWarning]:
    """Warnings for ``prompt`` given the tools that exist (name -> their parameter names)."""
    warnings: list[PersonaWarning] = []
    if len(prompt) > MAX_PERSONA_CHARS:
        warnings.append(
            PersonaWarning(
                kind="too_long",
                message=(
                    f"The prompt is {len(prompt):,} characters (over {MAX_PERSONA_CHARS:,}). It is "
                    "sent with every step: keep who the agent is and how it should work, and leave "
                    "what the platform already says (tools, workspace, git) to the platform."
                ),
            )
        )
    prefixes = frozenset(name.split("_", 1)[0] for name in tools)
    seen: set[str] = set()
    seen_arguments: set[tuple[str, str]] = set()
    for match in _CALL.finditer(prompt):
        name, arguments = match.group(1), match.group(2)
        if name in tools:
            for keyword in dict.fromkeys(_KEYWORD.findall(arguments)):
                if keyword not in tools[name] and (name, keyword) not in seen_arguments:
                    seen_arguments.add((name, keyword))
                    warnings.append(
                        PersonaWarning(
                            kind="unknown_argument",
                            message=(
                                f"`{name}` has no `{keyword}` parameter (it takes "
                                f"{', '.join(f'`{p}`' for p in tools[name]) or 'none'})."
                            ),
                        )
                    )
        elif _looks_like_a_tool(name, prefixes) and name not in seen:
            seen.add(name)
            warnings.append(_unknown(name))
    for name in _CODE.findall(prompt):
        if name not in tools and name not in seen and _looks_like_a_tool(name, prefixes):
            seen.add(name)
            warnings.append(_unknown(name))
    return warnings


def _unknown(name: str) -> PersonaWarning:
    return PersonaWarning(kind="unknown_tool", message=f"`{name}` is not a tool this platform has.")

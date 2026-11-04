"""The suggested next instruction shown under a finished answer.

A suggestion is worth showing only when the answer itself leads somewhere: a question the agent
asked, a step it said remains, a choice it offered. Anything else is a guess that costs the
person a read, so the model may answer nothing, and nothing is shown.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from typing import Any

from llm_gateway.structured import StructuredOutputError, generate_structured

from api.turns.shared import FollowUpSuggestion
from specifications.interfaces.llm import ChatMessage, ModelRequest

logger = logging.getLogger(__name__)

# What the model reads, bounded whatever the turn produced: the answer (head and tail, where a
# summary and its open questions sit), the person's last requests, and what the turn touched.
ANSWER_CHARS = 4000
REQUEST_CHARS = 600
EARLIER_REQUESTS = 3
TOUCHED_ITEMS = 12

_LEAK_INDICATORS = (
    "i think",
    "password",
    "we need to",
    "the user",
    "output only",
    "instruction",
    "begin_of_sentence",
    "end_of_sentence",
    "as an ai",
    "suggested prompt",
    "here is",
    "rule:",
    "rules:",
)

SUGGESTION_SYSTEM_PROMPT = (
    "You write the next message a person would send to their coding agent, offered as a "
    "one-key completion under the agent's answer. Suggest one only when the answer itself "
    "leads to it: the person's answer to a question the agent asked, a step the agent said "
    "remains, or the choice the agent offered. Otherwise leave 'suggestion' empty: a generic "
    "next step (run the tests, review the changes, what next?) is worse than none. "
    "Write it as the person would, in the language they write in, 3 to 14 words, plain text, "
    "specific to this work (name the file, branch, option or command). Answer in JSON."
)

_EDIT_TOOLS = frozenset({"file_write", "file_edit", "apply_patch"})


def clean_answer(content: str) -> str:
    """The answer without internal ``<think>`` scratchpads."""
    if "<think>" in content:
        content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL)
    return content.strip()


def _head_and_tail(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    half = limit // 2
    return f"{text[:half]}\n[… {len(text) - 2 * half} characters cut …]\n{text[-half:]}"


def _acceptable(candidate: str) -> bool:
    """A schema-valid string can still leak prompt text or exceed the length budget."""
    words = candidate.split()
    lowered = candidate.lower()
    return (
        8 <= len(candidate) <= 120
        and 2 <= len(words) <= 18
        and not any(indicator in lowered for indicator in _LEAK_INDICATORS)
    )


def _touched(executed_tools: Sequence[dict[str, Any]]) -> str:
    """The tools the turn ran, and the files it changed, in one bounded line."""
    names: list[str] = []
    files: list[str] = []
    for tool in executed_tools:
        name = str(tool.get("name") or "")
        if name and name not in names:
            names.append(name)
        arguments = tool.get("arguments")
        path = arguments.get("path") if isinstance(arguments, dict) else None
        if name in _EDIT_TOOLS and isinstance(path, str) and path not in files:
            files.append(path)
    parts = []
    if names:
        parts.append("Tools used: " + ", ".join(names[:TOUCHED_ITEMS]))
    if files:
        parts.append("Files changed: " + ", ".join(files[:TOUCHED_ITEMS]))
    return "\n".join(parts)


def _context(
    prompt: str,
    answer: str,
    executed_tools: Sequence[dict[str, Any]],
    earlier_requests: Sequence[str],
) -> str:
    earlier = [
        f"- {_head_and_tail(r.strip(), REQUEST_CHARS)}"
        for r in earlier_requests[-EARLIER_REQUESTS:]
        if r.strip()
    ]
    sections = []
    if earlier:
        sections.append("Earlier requests of the person:\n" + "\n".join(earlier))
    sections.append(f"The person's request:\n{_head_and_tail(prompt.strip(), REQUEST_CHARS)}")
    if touched := _touched(executed_tools):
        sections.append(touched)
    sections.append(f"The agent's answer:\n{_head_and_tail(answer, ANSWER_CHARS)}")
    return "\n\n".join(sections)


async def suggest_followup(
    gateway: Any,
    model: str,
    prompt: str,
    answer: str,
    executed_tools: Sequence[dict[str, Any]],
    earlier_requests: Sequence[str] = (),
) -> str | None:
    """The next instruction to offer, or ``None`` when the answer leads to none."""
    clean = clean_answer(answer)
    if not (clean and gateway):
        return None
    request = ModelRequest(
        model=model,
        messages=[
            ChatMessage(role="system", content=SUGGESTION_SYSTEM_PROMPT),
            ChatMessage(
                role="user", content=_context(prompt, clean, executed_tools, earlier_requests)
            ),
        ],
        max_tokens=80,
        temperature=0.0,
        stream=False,
    )
    try:
        suggestion = await generate_structured(gateway, request, FollowUpSuggestion)
    except StructuredOutputError as err:
        logger.debug("Structured suggestion generation failed: %s", err)
        return None
    first_line = suggestion.suggestion.split("\n")[0].strip().strip("\"'`")
    return first_line if _acceptable(first_line) else None

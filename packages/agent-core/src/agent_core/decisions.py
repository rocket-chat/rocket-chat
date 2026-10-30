"""A final answer that leaves a decision to the person instead of asking it.

``ask_question`` shows the choice as a decision card (cockpit or Slack) and keeps the turn, its
sandbox and its context alive until the person answers. A report that ends with "Decision
needed: 1. … 2. …" ends the turn instead, and the person has to find the options in prose and
type an answer that starts a new turn. The check is deliberately narrow: an explicit request to
choose, followed by at least two enumerated options, at the end of the answer. A closing offer
("Want me to also…?") is not a decision and never matches.
"""

from __future__ import annotations

import re

# Only the end of the answer matters: a decision asked mid-report and then settled is not open.
TAIL_CHARS = 2500
MIN_OPTIONS = 2

_ASKS_TO_CHOOSE = re.compile(
    r"decisions? needed"
    r"|needs? (?:your|a) (?:decision|choice|call)"
    r"|how (?:should|would you like|do you want) (?:me|i) to (?:proceed|continue|handle)"
    r"|how should i (?:proceed|continue)"
    r"|which (?:option|approach|one|path|way) (?:do|would|should) (?:you|i)"
    r"|(?:please|you need to|you must|you should) (?:choose|pick|decide|select)"
    r"|let me know which",
    re.IGNORECASE,
)
_OPTION_LINE = re.compile(r"^\s*(?:\d+[.)]|\(?[a-dA-D][.)]|option [a-d1-9]\b)\s*\S", re.IGNORECASE)


def leaves_decision_unasked(answer: str) -> bool:
    """Whether ``answer`` ends by asking the person to choose between listed options."""
    tail = answer[-TAIL_CHARS:]
    request = _ASKS_TO_CHOOSE.search(tail)
    if request is None:
        return False
    options = [line for line in tail[request.end() :].splitlines() if _OPTION_LINE.match(line)]
    return len(options) >= MIN_OPTIONS


def unasked_decision_notice(answer: str) -> str | None:
    """The reminder for an answer that ends on an open decision; ``None`` otherwise."""
    if not leaves_decision_unasked(answer):
        return None
    return (
        "Your answer ends by asking the person to choose between options, which would end the "
        "turn. Ask it with `ask_question` instead: the decision as the question (with what is "
        "blocked, in a sentence or two), each option as an entry, and your recommended default. "
        "Then continue with the answer."
    )

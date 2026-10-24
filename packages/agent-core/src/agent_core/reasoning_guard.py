"""A step that reasons at length without acting is stopped once and asked to decide.

Some models reason for many minutes over a choice they could test (22,000 tokens, eight minutes,
before the first tool call). That is not a limit on the work: the turn goes on with the same
step, asked again with reasoning off and a note to decide now. Only the reasoning of a step is
measured, and only until the model starts writing an answer or a tool call.
"""

from __future__ import annotations

from dataclasses import replace

from agent_core.notes import render_notes
from specifications.interfaces.llm import ChatMessage, ModelRequest

# Reasoning arrives as text, not as a count; four characters to a token is the usual estimate.
CHARACTERS_PER_TOKEN = 4

DECIDE_NOW_NOTE = (
    "Your last attempt at this step reasoned at length without acting, so it was stopped. "
    "Decide now with what you know: make the edit or the tool call that moves the task forward "
    "and let the result tell you whether you were right. If something is truly unknown, run a "
    "command to find out or ask the person one question."
)


class StepReasoningOverrunError(Exception):
    """The model reasoned past the step limit before acting; ``collector`` holds what it wrote."""

    def __init__(self, collector: object) -> None:
        super().__init__("reasoning limit reached before the model acted")
        self.collector = collector


def reasoning_tokens_estimate(reasoning: str) -> int:
    return len(reasoning) // CHARACTERS_PER_TOKEN


def decisive_request(
    request: ModelRequest,
    *,
    openrouter: bool,
    partial_reasoning: str | None = None,
) -> ModelRequest:
    """``request`` again with reasoning off and the note to decide appended."""
    notes = [DECIDE_NOW_NOTE]
    if partial_reasoning and partial_reasoning.strip():
        # Keep the tail of the reasoning so the decision can act on what was gathered
        tail = partial_reasoning.strip()[-3000:]
        notes.append(f"Your partial analysis before stopping was:\n{tail}")
    return replace(
        request,
        messages=[
            *request.messages,
            ChatMessage(role="user", content=render_notes(notes)),
        ],
        thinking_budget_tokens=None,
        # Only OpenRouter takes "none" as an effort; elsewhere, no effort is no reasoning request.
        reasoning_effort="none" if openrouter else None,
    )

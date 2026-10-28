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

    def __init__(self, collector: object, reason: str = "token_limit") -> None:
        super().__init__(f"reasoning limit reached before the model acted ({reason})")
        self.collector = collector
        self.reason = reason


def reasoning_tokens_estimate(reasoning: str) -> int:
    return len(reasoning) // CHARACTERS_PER_TOKEN


def detect_repetition_loop(reasoning: str) -> bool:
    """True if the recent tail of the reasoning stream contains degenerate repeating cycles.

    Catches autoregressive attractor loops early (e.g. models repeating 'OK. Writing. Let me go.'
    dozens of times) without waiting for whole-step token exhaustion.
    """
    if len(reasoning) < 120:
        return False
    tail = reasoning[-2000:]
    words = tail.split()
    if len(words) < 15:
        return False
    for cycle_len in range(1, 25):
        if len(words) < cycle_len * 3:
            continue
        pattern = words[-cycle_len:]
        repeats = 0
        idx = len(words)
        while idx >= cycle_len and words[idx - cycle_len : idx] == pattern:
            repeats += 1
            idx -= cycle_len
        threshold = 8 if cycle_len == 1 else (5 if cycle_len <= 4 else 3)
        if repeats >= threshold:
            return True
    return False


def sanitize_partial_reasoning(reasoning: str, max_chars: int = 3000) -> str:
    """Strips trailing repetitive loops and bounds the reasoning tail kept for retry notes."""
    if not reasoning or not reasoning.strip():
        return ""
    text = reasoning.strip()
    words = text.split()
    if len(words) >= 15:
        for cycle_len in range(1, 25):
            if len(words) < cycle_len * 3:
                continue
            pattern = words[-cycle_len:]
            repeats = 0
            idx = len(words)
            while idx >= cycle_len and words[idx - cycle_len : idx] == pattern:
                repeats += 1
                idx -= cycle_len
            threshold = 8 if cycle_len == 1 else (5 if cycle_len <= 4 else 3)
            if repeats >= threshold:
                kept = words[:idx]
                text = " ".join(kept) if kept else ""
                break
    if not text:
        return ""
    return text[-max_chars:].strip()


def decisive_request(
    request: ModelRequest,
    *,
    openrouter: bool,
    partial_reasoning: str | None = None,
) -> ModelRequest:
    """``request`` again with reasoning off and the note to decide appended."""
    notes = [DECIDE_NOW_NOTE]
    clean_tail = sanitize_partial_reasoning(partial_reasoning or "")
    if clean_tail:
        notes.append(f"Your partial analysis before stopping was:\n{clean_tail}")
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

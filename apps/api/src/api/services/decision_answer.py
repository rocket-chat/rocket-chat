"""Turning an answer to a question whose turn no longer exists into the next instruction.

When the API restarts while a turn waits on a person, the turn is gone but its question is
kept. The answer then starts a new turn, so it must carry the question it answers: the model
sees the earlier transcript, but not which choice the person made.
"""

from __future__ import annotations

from collections.abc import Sequence


def decision_answer_prompt(
    question_text: str, selected_options: Sequence[str], custom_text: str | None
) -> str:
    parts: list[str] = []
    if selected_options:
        parts.append(", ".join(selected_options))
    if custom_text and custom_text.strip():
        parts.append(custom_text.strip())
    answer = " - ".join(parts) or "(no preference given; use your best judgement)"
    return f'My answer to your question "{question_text.strip()}": {answer}\n\nPlease continue.'

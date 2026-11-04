"""The suggested next instruction: only what the answer leads to, from bounded context."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest
from api.turns.followup import ANSWER_CHARS, REQUEST_CHARS, suggest_followup
from api.turns.shared import FollowUpSuggestion
from llm_gateway.structured import StructuredOutputError

EDIT = {"name": "file_edit", "arguments": {"path": "src/billing/invoice.py"}}


def _model(monkeypatch: pytest.MonkeyPatch, suggestion: str = "") -> AsyncMock:
    structured = AsyncMock(return_value=FollowUpSuggestion(suggestion=suggestion))
    monkeypatch.setattr("api.turns.followup.generate_structured", structured)
    return structured


def _sent(structured: AsyncMock) -> Any:
    return structured.await_args.args[1]


async def test_the_model_reads_the_answer_the_earlier_requests_and_what_the_turn_changed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    structured = _model(monkeypatch, "Oui, garde la TVA à 20 % pour la France")
    answer = "J'ai corrigé l'arrondi. Dois-je garder la TVA à 20 % pour la France ?"

    suggestion = await suggest_followup(
        object(),
        "fast-model",
        "Corrige l'arrondi des factures",
        answer,
        [{"name": "file_read", "arguments": {"path": "a.py"}}, EDIT],
        earlier_requests=["Regarde le module de facturation"],
    )

    assert suggestion == "Oui, garde la TVA à 20 % pour la France"
    request = _sent(structured)
    assert request.temperature == 0.0
    system, user = request.messages
    assert "leave 'suggestion' empty" in system.content
    assert "Regarde le module de facturation" in user.content
    assert "Corrige l'arrondi des factures" in user.content
    assert "Files changed: src/billing/invoice.py" in user.content
    assert answer in user.content


async def test_an_answer_that_leads_nowhere_offers_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    _model(monkeypatch, "")
    assert await suggest_followup(object(), "m", "Fix it", "Fixed and tested.", [EDIT]) is None


async def test_no_generic_suggestion_stands_in_for_the_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The fixed phrases ("Verify these changes with automated unit tests") were most of what
    people saw; a failure now shows nothing."""
    monkeypatch.setattr(
        "api.turns.followup.generate_structured",
        AsyncMock(side_effect=StructuredOutputError("no schema")),
    )
    answer = "I updated the config. Next steps: Deploy the service to staging today"
    assert await suggest_followup(object(), "m", "run the tests", answer, [EDIT]) is None
    assert await suggest_followup(None, "m", "p", answer, [EDIT]) is None


async def test_a_suggestion_that_leaks_the_prompt_is_dropped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _model(monkeypatch, "Here is the instruction the user wants")
    assert await suggest_followup(object(), "m", "p", "An answer.", []) is None


async def test_the_context_stays_bounded_whatever_the_turn_produced(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    structured = _model(monkeypatch, "")
    tools = [{"name": f"tool_{i}", "arguments": {"path": f"f{i}.py"}} for i in range(500)]
    tools += [{"name": "file_write", "arguments": {"path": f"w{i}.py"}} for i in range(500)]

    await suggest_followup(
        object(),
        "m",
        "x" * 100_000,
        "start " + "y" * 200_000 + " the closing question?",
        tools,
        earlier_requests=["z" * 100_000] * 50,
    )

    content = _sent(structured).messages[-1].content
    assert len(content) < ANSWER_CHARS + 4 * REQUEST_CHARS + 1500
    assert "the closing question?" in content, "the answer's tail, where open questions sit"
    assert "characters cut" in content

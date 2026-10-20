"""The sidebar can tell which sessions need a person and which turn failed."""

from __future__ import annotations

from api.events import AsyncIOEventBus
from api.store import InMemorySessionStore
from api.turns.attention import AWAITING_INPUT_KEY, LAST_TURN_STATUS_KEY, update_attention
from fastapi.testclient import TestClient

from specifications.interfaces.session import SessionRecord
from tests.turn_harness import run_turn

ORG = "attention_org"


async def _store() -> InMemorySessionStore:
    store = InMemorySessionStore()
    await store.create_session(
        SessionRecord(session_id="s1", tenant_org_id=ORG, tenant_user_id="u", title="t")
    )
    return store


async def test_a_gate_is_marked_and_cleared_and_every_change_is_published() -> None:
    store, bus = await _store(), AsyncIOEventBus()

    await update_attention(store, bus, "s1", ORG, awaiting_input="approval")
    # The in-memory store hands out the one shared record: read the value before the next change.
    marked = (await store.get_session("s1") or SessionRecord("x", ORG, "u")).metadata.get(
        AWAITING_INPUT_KEY
    )
    await update_attention(store, bus, "s1", ORG, clear_awaiting=True, last_turn_status="completed")
    cleared = await store.get_session("s1")

    assert marked == "approval"
    assert cleared is not None and AWAITING_INPUT_KEY not in cleared.metadata
    assert cleared.metadata[LAST_TURN_STATUS_KEY] == "completed"
    events, _ = bus.replay_since("s1", 0)
    assert [e.payload.get("awaiting_input") for e in events] == ["approval", ""]
    assert events[-1].payload["last_turn_status"] == "completed"


async def test_other_metadata_survives_an_attention_update() -> None:
    store, bus = await _store(), AsyncIOEventBus()
    await store.mutate_session("s1", ORG, lambda r: setattr(r, "metadata", {"keep": "me"}))

    await update_attention(store, bus, "s1", ORG, awaiting_input="question")

    record = await store.get_session("s1")
    assert record is not None and record.metadata == {"keep": "me", AWAITING_INPUT_KEY: "question"}


async def test_a_finished_turn_records_how_it_ended() -> None:
    outcome = await run_turn("attention_turn")

    assert outcome.session.metadata[LAST_TURN_STATUS_KEY] == "completed"
    assert AWAITING_INPUT_KEY not in outcome.session.metadata
    updates = [e for e in outcome.events_of("session_updated") if "last_turn_status" in e.payload]
    assert updates and updates[-1].payload["last_turn_status"] == "completed"


def test_the_settings_schema_says_what_is_advanced_and_what_a_number_counts(
    client: TestClient,
) -> None:
    schema = client.get("/v1/settings-schema").json()["domains"]
    by_key = {f["key"]: f for f in schema["budgets"]}

    assert (
        by_key["monthly_budget_usd"]["unit"] == "USD"
        and not by_key["monthly_budget_usd"]["advanced"]
    )
    assert by_key["goal_max_total_tokens"]["advanced"] is True
    assert {f["key"]: f for f in schema["models"]}["temperature"]["advanced"] is True

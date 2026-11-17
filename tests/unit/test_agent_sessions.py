"""An agent reading its other sessions: opt-in, same organization, never private, bounded, audited."""

from __future__ import annotations

from typing import Any

from api.models import AgentPersonaModel
from api.services.agent_sessions import (
    MAX_EXCERPT_CHARS,
    MAX_MESSAGE_CHARS,
    BoundAgentSessions,
    InMemorySessionAgents,
    record_participation,
    render_excerpt,
)
from api.services.audit import AuditService
from api.services.personas import save_persona
from api.store import InMemorySessionStore
from turn_harness import TurnOutcome, run_turn

from specifications.interfaces.llm import ChatMessage
from specifications.interfaces.session import SessionRecord


def _record(
    session_id: str, org: str, messages: list[ChatMessage], **metadata: Any
) -> SessionRecord:
    return SessionRecord(
        session_id=session_id,
        tenant_org_id=org,
        tenant_user_id="u1",
        title=f"title of {session_id}",
        conversation_history=messages,
        metadata=dict(metadata),
    )


def _talk(*pairs: tuple[str, str]) -> list[ChatMessage]:
    return [ChatMessage(role=role, content=text) for role, text in pairs]


def test_a_window_caps_each_message_and_the_whole_and_says_where_to_read_on() -> None:
    huge = "x" * (MAX_MESSAGE_CHARS * 3)
    record = _record("s", "o", _talk(*[("user", huge), ("assistant", huge)] * 20))

    excerpt = render_excerpt(record, 0, 50)

    assert len(excerpt.text) <= MAX_EXCERPT_CHARS + 200
    assert excerpt.truncated and "chars cut" in excerpt.text
    assert f"first_index={excerpt.shown}" in excerpt.text
    assert excerpt.total_messages == 40


def test_tool_calls_and_results_are_never_shown() -> None:
    record = _record(
        "s",
        "o",
        [
            ChatMessage(role="user", content="How many orders?"),
            ChatMessage(role="assistant", content="", tool_calls=[{"id": "1"}]),
            ChatMessage(role="tool", content="SECRET-ROWS", tool_call_id="1"),
            ChatMessage(role="assistant", content="About 4,200."),
        ],
    )

    excerpt = render_excerpt(record, 0, 20)

    assert "SECRET-ROWS" not in excerpt.text
    assert "[0] Person: How many orders?" in excerpt.text
    assert "[1] Agent: About 4,200." in excerpt.text


async def _bound(
    store: InMemorySessionStore, participation: InMemorySessionAgents
) -> BoundAgentSessions:
    return BoundAgentSessions(
        participation=participation,
        sessions=store,
        audit=AuditService(None),
        org_id="org_a",
        agent_id="agent_data",
        current_session_id="current",
    )


async def test_only_non_private_sessions_the_agent_answered_in_its_organization_are_readable() -> (
    None
):
    store, participation = InMemorySessionStore(), InMemorySessionAgents()
    rows = {
        "public": _record("public", "org_a", _talk(("user", "q"), ("assistant", "a"))),
        "dm": _record("dm", "org_a", _talk(("user", "q")), slack_is_dm=True),
        "private": _record("private", "org_a", _talk(("user", "q")), slack_private=True),
        "other_org": _record("other_org", "org_b", _talk(("user", "q"))),
        "not_mine": _record("not_mine", "org_a", _talk(("user", "q"))),
        "current": _record("current", "org_a", _talk(("user", "q"))),
    }
    for record in rows.values():
        await store.create_session(record)
    for name in ("public", "dm", "private", "current"):
        await participation.record("org_a", name, "agent_data", 100)
    await participation.record("org_b", "other_org", "agent_data", 100)
    await participation.record("org_a", "not_mine", "another_agent", 100)
    sessions = await _bound(store, participation)

    listed = [s.session_id for s in await sessions.list_sessions("", 30)]

    assert listed == ["public"]
    assert await sessions.read_session("public", 0, 10) is not None
    for refused in ("dm", "private", "other_org", "not_mine", "current", "missing"):
        assert await sessions.read_session(refused, 0, 10) is None, refused


async def test_every_read_is_audited() -> None:
    store, participation = InMemorySessionStore(), InMemorySessionAgents()
    await store.create_session(_record("earlier", "org_a", _talk(("user", "q"))))
    await participation.record("org_a", "earlier", "agent_data", 100)
    sessions = await _bound(store, participation)

    await sessions.read_session("earlier", 0, 10)

    events = await sessions.audit.list_page("org_a", limit=10)
    assert any(
        e.event_type == "agent_session.read" and e.metadata.get("read_session_id") == "earlier"
        for e in events
    )


async def _agent(org: str, **switches: Any) -> str:
    persona = AgentPersonaModel(
        id=f"persona-{org}",
        name="Data assistant",
        role_title="Data assistant",
        system_prompt="You answer data questions.",
        scope="org",
        whitelisted_tools=["file_read"],
        **switches,
    )
    return (await save_persona(None, org, "admin", True, persona)).id


def _offered(outcome: TurnOutcome) -> set[str]:
    request = next(r for r in outcome.model.requests if r.tools)
    names = {tool.name for tool in request.tools}
    system = "\n".join(m.content or "" for m in request.messages if m.role == "system")
    return names | {n for n in ("agent_sessions_list", "agent_session_read") if n in system}


async def test_an_agent_with_the_switch_finds_and_reads_an_earlier_request_in_a_turn() -> None:
    org = "reads_sessions"
    agent_id = await _agent(org, reads_agent_sessions=True)
    store = InMemorySessionStore()
    await store.create_session(
        _record(
            "earlier_request",
            org,
            _talk(("user", "How many orders last week?"), ("assistant", "ORDERS-4200")),
        )
    )
    await record_participation(None, org, "earlier_request", agent_id)

    outcome = await run_turn(
        org,
        store=store,
        metadata={"agent_id": agent_id},
        script=[
            ("load_tools", {"names": ["agent_sessions_list", "agent_session_read"]}),
            ("agent_sessions_list", {}),
            ("agent_session_read", {"session_id": "earlier_request"}),
        ],
    )

    last = [r for r in outcome.model.requests if r.tools][-1]
    results = "\n".join(m.content or "" for m in last.messages if m.role == "tool")
    assert "earlier_request" in results and "ORDERS-4200" in results
    assert {"agent_sessions_list", "agent_session_read"} <= _offered(outcome)


async def test_an_agent_without_the_switch_has_no_session_tools() -> None:
    org = "no_session_reads"
    agent_id = await _agent(org)

    outcome = await run_turn(
        org,
        metadata={"agent_id": agent_id},
        script=[("agent_sessions_list", {})],
    )

    assert not {"agent_sessions_list", "agent_session_read"} & _offered(outcome)
    last = [r for r in outcome.model.requests if r.tools][-1]
    results = "\n".join(m.content or "" for m in last.messages if m.role == "tool")
    assert "Blocked" in results or "not" in results

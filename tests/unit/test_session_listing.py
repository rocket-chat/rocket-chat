"""Unit tests for keyset session pagination and the fork cut point.

Pagination correctness is proven against the in-memory store, which mirrors the
PostgreSQL keyset query: paging through a fixed set must visit every session exactly
once, in ``(updated_at, session_id)`` order, even when page sizes do not divide the set.
"""

from __future__ import annotations

import pytest
from api.routers.fork import truncate_history
from api.store import InMemorySessionStore
from sandbox_driver.fake_driver import FakeSandboxDriver
from sandbox_driver.seeding import ORIGIN_EMPTY, ORIGIN_FILES, ORIGIN_GIT, seed_workspace_from

from specifications.interfaces.llm import ChatMessage
from specifications.interfaces.sandbox import WorkspaceSpec
from specifications.interfaces.session import (
    SessionCursor,
    SessionRecord,
    decode_session_cursor,
    encode_session_cursor,
)


def _record(
    session_id: str,
    updated_at: int,
    *,
    title: str | None = None,
    tenant_user_id: str = "user_a",
    is_shared: bool = False,
    is_archived: bool = False,
    collaborators: list[str] | None = None,
    metadata: dict[str, object] | None = None,
) -> SessionRecord:
    return SessionRecord(
        session_id=session_id,
        tenant_org_id="org_a",
        tenant_user_id=tenant_user_id,
        title=title if title is not None else session_id,
        updated_at=updated_at,
        created_at=updated_at,
        is_shared=is_shared,
        is_archived=is_archived,
        collaborators=collaborators or [],
        metadata=metadata or {},
    )


def _spec(session_id: str) -> WorkspaceSpec:
    return WorkspaceSpec(
        session_id=session_id,
        tenant_org_id="org_a",
        tenant_user_id="user_a",
        container_image="fake:latest",
    )


def test_cursor_round_trips() -> None:
    cursor = SessionCursor(updated_at=1723456789, session_id="session_abc")
    decoded = decode_session_cursor(encode_session_cursor(cursor))
    assert decoded == cursor


def test_cursor_decode_rejects_garbage() -> None:
    with pytest.raises(ValueError):
        decode_session_cursor("not-a-valid-cursor!!")


@pytest.mark.asyncio
async def test_keyset_pagination_has_no_skips_or_duplicates() -> None:
    store = InMemorySessionStore()
    # Three sessions share one second, so the id tie-breaker is what keeps the page
    # boundary stable; a naive updated_at-only cursor would skip or repeat them.
    for session_id, updated_at in [
        ("session_1", 100),
        ("session_2", 100),
        ("session_3", 100),
        ("session_4", 99),
        ("session_5", 98),
        ("session_6", 97),
        ("session_7", 96),
    ]:
        await store.create_session(_record(session_id, updated_at))

    seen: list[str] = []
    cursor: SessionCursor | None = None
    for _ in range(10):
        page = await store.list_sessions_page("org_a", limit=3, cursor=cursor)
        seen.extend(record.session_id for record in page.records)
        if page.next_cursor is None:
            break
        cursor = decode_session_cursor(page.next_cursor)

    expected = [
        "session_3",
        "session_2",
        "session_1",
        "session_4",
        "session_5",
        "session_6",
        "session_7",
    ]
    assert seen == expected
    assert len(seen) == len(set(seen))


@pytest.mark.asyncio
async def test_listing_filters_archived_and_searches_title() -> None:
    store = InMemorySessionStore()
    await store.create_session(_record("session_live", 100, title="Refactor auth"))
    await store.create_session(
        _record("session_archived", 101, title="Old auth work", is_archived=True)
    )
    await store.create_session(_record("session_other", 102, title="Write docs"))

    default_page = await store.list_sessions_page("org_a", limit=50)
    assert {r.session_id for r in default_page.records} == {"session_live", "session_other"}

    archived_page = await store.list_sessions_page("org_a", include_archived=True, limit=50)
    assert {r.session_id for r in archived_page.records} == {
        "session_live",
        "session_archived",
        "session_other",
    }

    search_page = await store.list_sessions_page("org_a", query="auth", limit=50)
    assert {r.session_id for r in search_page.records} == {"session_live"}


@pytest.mark.asyncio
async def test_listing_enforces_non_admin_visibility() -> None:
    store = InMemorySessionStore()
    await store.create_session(_record("session_mine", 100))
    await store.create_session(
        _record("session_shared", 101, is_shared=True, metadata={"is_shared": True})
    )
    await store.create_session(
        _record(
            "session_collab", 102, collaborators=["user_a"], metadata={"collaborators": ["user_a"]}
        )
    )
    await store.create_session(_record("session_private", 103, tenant_user_id="user_b"))

    page = await store.list_sessions_page("org_a", visible_to_user_id="user_a", limit=50)
    assert {r.session_id for r in page.records} == {
        "session_mine",
        "session_shared",
        "session_collab",
    }


def test_truncate_history_copies_complete_turns() -> None:
    history = [
        ChatMessage(role="user", content="first"),
        ChatMessage(role="assistant", content="answer one"),
        ChatMessage(role="user", content="second"),
        ChatMessage(role="assistant", content="answer two"),
        ChatMessage(role="user", content="third"),
    ]

    assert truncate_history(history, None) == history
    first_turn = truncate_history(history, 1)
    assert [m.content for m in first_turn] == ["first", "answer one"]
    two_turns = truncate_history(history, 2)
    assert [m.content for m in two_turns] == ["first", "answer one", "second", "answer two"]


@pytest.mark.asyncio
async def test_seed_workspace_marks_plain_files_and_copies_them() -> None:
    driver = FakeSandboxDriver(seed_files={"README.md": "hello\n", "src/app.py": "x = 1\n"})
    await driver.ensure_workspace(_spec("parent"))
    result = await seed_workspace_from(driver, "parent", "child", _spec("child"))
    assert result.origin == ORIGIN_FILES
    assert result.file_count == 2
    assert driver.file_snapshot("child") == {"README.md": "hello\n", "src/app.py": "x = 1\n"}


@pytest.mark.asyncio
async def test_seed_workspace_marks_git_repositories() -> None:
    driver = FakeSandboxDriver(seed_files={".git/config": "[core]\n", "main.py": "print(1)\n"})
    await driver.ensure_workspace(_spec("parent"))
    result = await seed_workspace_from(driver, "parent", "child", _spec("child"))
    assert result.origin == ORIGIN_GIT
    assert ".git/config" in driver.file_snapshot("child")


@pytest.mark.asyncio
async def test_seed_workspace_marks_an_empty_parent() -> None:
    driver = FakeSandboxDriver(seed_files={})
    await driver.ensure_workspace(_spec("parent"))
    result = await seed_workspace_from(driver, "parent", "child", _spec("child"))
    assert result.origin == ORIGIN_EMPTY
    assert result.file_count == 0


@pytest.mark.asyncio
async def test_list_sessions_defers_conversation_history() -> None:
    store = InMemorySessionStore()
    record = _record("s_hist", 100)
    record.conversation_history = [
        ChatMessage(role="user", content="hello"),
        ChatMessage(role="assistant", content="world"),
    ]
    await store.create_session(record)

    # list_sessions and list_sessions_page must not carry conversation_history
    listed = await store.list_sessions("org_a")
    assert len(listed) == 1
    assert listed[0].conversation_history == []

    page = await store.list_sessions_page("org_a")
    assert len(page.records) == 1
    assert page.records[0].conversation_history == []

    # But direct get_session still preserves full history
    fetched = await store.get_session("s_hist")
    assert fetched is not None
    assert len(fetched.conversation_history) == 2

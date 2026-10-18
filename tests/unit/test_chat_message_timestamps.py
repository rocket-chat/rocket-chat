"""Unit tests for ChatMessage and tool timestamp serialization and preservation."""

from __future__ import annotations

from unittest.mock import MagicMock

from api.db.store import PostgresSessionStore

from specifications.interfaces.llm import ChatMessage


def test_chat_message_to_dict_preserves_created_at() -> None:
    store = PostgresSessionStore(MagicMock())
    ts = 1700005000
    msg = ChatMessage(role="user", content="hello", created_at=ts)
    data = store._chat_message_to_dict(msg)
    assert data.get("created_at") == ts


def test_dict_to_chat_message_reads_created_at() -> None:
    store = PostgresSessionStore(MagicMock())
    ts = 1700006000
    data = {"role": "assistant", "content": "world", "created_at": ts}
    msg = store._dict_to_chat_message(data)
    assert msg.created_at == ts


def test_dict_to_chat_message_falls_back_to_session_created_at_when_missing() -> None:
    store = PostgresSessionStore(MagicMock())
    session_ts = 1700001000
    data = {"role": "user", "content": "legacy message"}
    msg = store._dict_to_chat_message(data, fallback_created_at=session_ts)
    assert msg.created_at == session_ts


def test_dict_to_chat_message_prefers_stored_timestamp_over_fallback() -> None:
    store = PostgresSessionStore(MagicMock())
    stored_ts = 1700002000
    session_ts = 1700001000
    data = {"role": "user", "content": "timestamped message", "created_at": stored_ts}
    msg = store._dict_to_chat_message(data, fallback_created_at=session_ts)
    assert msg.created_at == stored_ts

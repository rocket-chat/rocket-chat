"""A long thread's context is its root and its newest replies, not its oldest."""

from __future__ import annotations

from typing import Any

from slack_assistant import thread_context
from slack_assistant.thread_context import build_thread_transcript, fetch_thread_messages


class _PagedThread:
    """``conversations.replies`` over a thread of ``count`` replies, oldest first, paged."""

    def __init__(self, count: int, bot_every: int = 0) -> None:
        self.messages: list[dict[str, Any]] = [{"ts": "100.0", "user": "U_ROOT", "text": "root"}]
        for i in range(1, count + 1):
            mine = bot_every and i % bot_every == 0
            self.messages.append(
                {"ts": f"{100 + i}.0", "user": "U_BOT" if mine else "U_P", "text": f"reply {i}"}
            )
        self.calls = 0

    async def conversations_replies(self, **kwargs: Any) -> dict[str, Any]:
        self.calls += 1
        start = int(kwargs.get("cursor") or 0)
        end = start + int(kwargs["limit"])
        more = end < len(self.messages)
        return {
            "messages": self.messages[start:end],
            "has_more": more,
            "response_metadata": {"next_cursor": str(end) if more else ""},
        }


async def test_the_root_and_the_newest_replies_are_kept(monkeypatch: Any) -> None:
    monkeypatch.setattr(thread_context, "REPLIES_PAGE_SIZE", 25)
    client = _PagedThread(60)

    messages = await fetch_thread_messages(client, "C", "100.0", limit=20)

    assert client.calls == 3
    assert [m.text for m in messages] == ["root"] + [f"reply {i}" for i in range(41, 61)]


async def test_the_bots_own_replies_are_left_out_of_the_turns_context() -> None:
    client = _PagedThread(6, bot_every=2)

    async def name(user: str) -> str:
        return user

    transcript = await build_thread_transcript(
        client, "C", "100.0", name, limit=10, bot_user_id="U_BOT", include_own_replies=False
    )

    assert "reply 1" in transcript and "reply 3" in transcript
    assert "reply 2" not in transcript and "reply 4" not in transcript

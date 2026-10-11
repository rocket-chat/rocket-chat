"""Transcript helpers: change tracking, tool-call pairing repair, turn slicing."""

from __future__ import annotations

from collections.abc import Iterable

from specifications.interfaces.llm import ChatMessage

INTERRUPTED_TOOL_RESULT = (
    "The tool did not return a result because the turn was interrupted before it finished."
)


class RecordingList(list[ChatMessage]):
    """A conversation that remembers which messages were appended since the last flush.

    Only ``append`` is recorded. Context compaction rewrites the *in-memory* window with
    ``del``/``extend``; that must not be mistaken for new conversation, because the durable
    log always keeps the full, uncompacted transcript.
    """

    def __init__(self, items: Iterable[ChatMessage] = ()) -> None:
        super().__init__(items)
        self.pending: list[ChatMessage] = []

    def append(self, item: ChatMessage) -> None:
        super().append(item)
        self.pending.append(item)

    def take_pending(self) -> list[ChatMessage]:
        taken, self.pending = self.pending, []
        return taken

    def restore_pending(self, items: list[ChatMessage]) -> None:
        """Re-queue messages whose flush failed so the next flush retries them."""
        self.pending = [*items, *self.pending]


def _call_ids(message: ChatMessage) -> list[str]:
    return [str(call.get("id")) for call in (message.tool_calls or []) if call.get("id")]


def repair_tool_pairs(messages: Iterable[ChatMessage]) -> list[ChatMessage]:
    """Return a transcript every provider will accept.

    Providers reject an assistant message whose ``tool_calls`` lack matching ``tool``
    results, and a ``tool`` message that matches no preceding call. Both appear after a
    crash, a cancel mid-batch, or compaction that cuts between a call and its result.
    Missing results get an explicit "interrupted" placeholder; orphan results are dropped.
    """
    source = list(messages)
    repaired: list[ChatMessage] = []
    index = 0
    while index < len(source):
        message = source[index]
        if message.role == "tool":
            index += 1  # orphan result: its call was never recorded before it
            continue
        repaired.append(message)
        index += 1
        if message.role != "assistant" or not message.tool_calls:
            continue
        wanted = _call_ids(message)
        answered: dict[str, ChatMessage] = {}
        while index < len(source) and source[index].role == "tool":
            result = source[index]
            if result.tool_call_id in wanted and result.tool_call_id not in answered:
                answered[str(result.tool_call_id)] = result
            index += 1
        for call_id in wanted:
            repaired.append(
                answered.get(call_id)
                or ChatMessage(role="tool", content=INTERRUPTED_TOOL_RESULT, tool_call_id=call_id)
            )
    return repaired


def user_turn_count(messages: Iterable[ChatMessage]) -> int:
    return sum(1 for message in messages if message.role == "user")


def slice_user_turns(messages: Iterable[ChatMessage], user_turns: int | None) -> list[ChatMessage]:
    """The messages up to (excluding) the ``user_turns + 1``-th user message."""
    source = list(messages)
    if user_turns is None:
        return source
    seen = 0
    for position, message in enumerate(source):
        if message.role == "user":
            seen += 1
            if seen > user_turns:
                return source[:position]
    return source

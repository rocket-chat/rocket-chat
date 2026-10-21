"""A compaction never stores an unusable summary, and never forgets what the person asked.

Two real compactions stored nothing usable: an empty answer, then a tool call written in the
model's own markup as the "summary". These tests pin the checks that now stand between the
model's answer and the checkpoint.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

from agent_core.compaction_summary import (
    DIGEST_HEADER,
    SUMMARY_PROMPT,
    SUMMARY_STATUS_DIGEST,
    SUMMARY_STATUS_MODEL,
    SUMMARY_STATUS_MODEL_RETRY,
    UPDATE_PROMPT,
    summary_prompt,
    tool_history_digest,
    valid_summary,
)
from agent_core.context import EARLIER_REQUESTS_BUDGET_CHARS, ContextManager
from agent_core.summarizer import FLATTENED_SUMMARY_SYSTEM, model_summarizer

from specifications.interfaces.llm import ChatMessage, ModelRequest, StreamChunk, ToolDefinition

GOOD = "## Objective\n- add the resume token checkpoint\n\n## Next Move\n1. run make test" + (
    "\n- `history/service/watcher/watcher.go`: the loop" * 3
)
# What the model answered in session_3116245f7a's second compaction.
LEAKED = (
    '<tool_calls>\n<invoke name="file_read">\n<parameter name="path">/workspace/backend-services/'
    "history/service/watcher/watcher.go</parameter>\n</invoke>\n</tool_calls>"
)


def _call(call_id: str, name: str, **arguments: Any) -> dict[str, Any]:
    return {"id": call_id, "type": "function",
            "function": {"name": name, "arguments": json.dumps(arguments)}}  # fmt: skip


def _long_session() -> list[ChatMessage]:
    messages = [
        ChatMessage(role="system", content="S"),
        ChatMessage(role="user", content="Implement SVC-21926; never re-consume an event twice"),
    ]
    for n in range(8):
        messages += [
            ChatMessage(
                role="assistant", tool_calls=[_call(f"e{n}", "file_edit", path=f"f{n}.go")]
            ),
            ChatMessage(role="tool", content="Successfully edited", tool_call_id=f"e{n}"),
        ]
    messages.append(ChatMessage(role="user", content="also add a 15 minute default"))
    for n in range(4):
        messages += [
            ChatMessage(role="assistant", tool_calls=[_call(f"b{n}", "bash_exec", command="ls")]),
            ChatMessage(role="tool", content="ok", tool_call_id=f"b{n}"),
        ]
    return messages


def test_a_summary_needs_the_goal_and_the_next_move_and_no_call_markup() -> None:
    assert valid_summary(GOOD)
    assert not valid_summary("")
    assert not valid_summary(None)
    assert not valid_summary(LEAKED)
    assert not valid_summary(GOOD + "\n" + LEAKED)
    assert not valid_summary("## Objective\n- something long enough to pass the size check" * 3)


def test_a_second_compaction_updates_the_first_checkpoint() -> None:
    plain = [ChatMessage(role="user", content="hello")]
    bridged = [ChatMessage(role="user", content="Summary of the compacted work:\n## Objective")]
    assert summary_prompt(plain) == SUMMARY_PROMPT
    assert summary_prompt(bridged) == UPDATE_PROMPT


def test_the_digest_lists_files_commands_and_publications_from_the_tool_history() -> None:
    middle = [
        ChatMessage(
            role="assistant",
            tool_calls=[
                _call("1", "file_edit", path="history/config/config.go"),
                _call("2", "bash_exec", command="make test"),
                _call("3", "git_commit", message="feat(history): checkpoint the resume token"),
                _call("4", "git_push", path="/workspace/backend-services"),
                _call("5", "git_open_pr", title="t"),
            ],
        ),
        ChatMessage(role="tool", content="Successfully edited", tool_call_id="1"),
        ChatMessage(role="tool", content="Command failed with exit code 2\nFAIL", tool_call_id="2"),
        ChatMessage(role="tool", content="Committed", tool_call_id="3"),
        ChatMessage(
            role="tool", content="Published 1 commit. Head: 2955ddfd3efb.", tool_call_id="4"
        ),
        ChatMessage(
            role="tool",
            content="Opened https://github.com/birotaio/backend-services/pull/3823",
            tool_call_id="5",
        ),
    ]

    digest = tool_history_digest(middle)

    assert digest.startswith(DIGEST_HEADER)
    assert "`history/config/config.go`" in digest
    assert "`make test` (exit 2)" in digest
    assert "commit: feat(history): checkpoint the resume token" in digest
    assert "pushed head 2955ddfd3efb" in digest
    assert "https://github.com/birotaio/backend-services/pull/3823" in digest


async def _compact(answers: list[str]) -> tuple[list[ChatMessage], str, int]:
    asked: list[list[ChatMessage]] = []

    async def summarize(request: list[ChatMessage]) -> str:
        asked.append(request)
        return answers[min(len(asked), len(answers)) - 1]

    result = await ContextManager(recent_turns_to_preserve=2).enforce_hygiene_async(
        "m", _long_session(), summarize, force_compaction=True
    )
    return list(result), result.summary_status, len(asked)


async def test_a_usable_first_answer_is_kept() -> None:
    compacted, status, asked = await _compact([GOOD])
    assert status == SUMMARY_STATUS_MODEL and asked == 1
    assert any(GOOD in (m.content or "") for m in compacted)


async def test_an_empty_answer_is_asked_again_once() -> None:
    compacted, status, asked = await _compact(["", GOOD])
    assert status == SUMMARY_STATUS_MODEL_RETRY and asked == 2
    assert any(GOOD in (m.content or "") for m in compacted)


async def test_two_unusable_answers_give_the_digest_never_the_markup() -> None:
    compacted, status, asked = await _compact(["", LEAKED])
    text = "\n".join(m.content or "" for m in compacted)
    assert status == SUMMARY_STATUS_DIGEST and asked == 2
    assert DIGEST_HEADER in text and "<tool_calls>" not in text
    assert "`f0.go`" in text


async def test_every_message_of_the_person_outlives_the_compaction() -> None:
    compacted, _, _ = await _compact([GOOD])
    text = "\n".join(m.content or "" for m in compacted)
    assert "> Implement SVC-21926; never re-consume an event twice" in text
    assert "also add a 15 minute default" in text


async def test_earlier_messages_are_kept_newest_first_within_their_budget() -> None:
    messages = [ChatMessage(role="system", content="S")]
    big = "x" * (EARLIER_REQUESTS_BUDGET_CHARS // 2 + 1)
    for n in range(3):
        messages += [
            ChatMessage(role="user", content=f"request {n} {big}"),
            ChatMessage(role="assistant", tool_calls=[_call(f"c{n}", "bash_exec", command="ls")]),
            ChatMessage(role="tool", content="ok", tool_call_id=f"c{n}"),
        ]
    messages += [
        ChatMessage(role="user", content="now"),
        *[
            m
            for n in range(3)
            for m in (
                ChatMessage(
                    role="assistant", tool_calls=[_call(f"t{n}", "bash_exec", command="ls")]
                ),
                ChatMessage(role="tool", content="ok", tool_call_id=f"t{n}"),
            )
        ],
    ]

    async def summarize(request: list[ChatMessage]) -> str:
        return GOOD

    result = await ContextManager(recent_turns_to_preserve=2).compact_with_summary(
        messages, summarize
    )
    text = "\n".join(m.content or "" for m in result)
    assert "request 2" in text and "request 0" not in text


class _Gateway:
    def __init__(self, fail_live: bool) -> None:
        self.fail_live = fail_live
        self.requests: list[ModelRequest] = []

    async def chat_stream(self, request: ModelRequest) -> AsyncIterator[StreamChunk]:
        self.requests.append(request)
        if request.tools and self.fail_live:
            raise RuntimeError("tool_choice none is not supported with tools")
        yield StreamChunk(text_delta=GOOD, is_finished=True)


TOOLS = [
    ToolDefinition(name="bash_exec", description="Run", parameters_json_schema={"type": "object"})
]


async def test_the_summary_is_asked_on_the_live_request_with_tools_and_no_reasoning() -> None:
    gateway = _Gateway(fail_live=False)
    request = [ChatMessage(role="system", content="S"), ChatMessage(role="user", content="P")]

    assert await model_summarizer(gateway, "openrouter/x/y", TOOLS)(request) == GOOD  # type: ignore[arg-type]

    sent = gateway.requests[0]
    assert sent.messages == request and sent.tools == TOOLS and sent.tool_choice == "none"
    assert sent.reasoning_effort == "none" and sent.thinking_budget_tokens is None


async def test_a_refused_live_request_is_asked_again_as_one_transcript() -> None:
    gateway = _Gateway(fail_live=True)
    request = [
        ChatMessage(role="system", content="S"),
        ChatMessage(role="user", content="Implement it"),
        ChatMessage(role="user", content=SUMMARY_PROMPT),
    ]

    assert await model_summarizer(gateway, "deepseek/v4", TOOLS)(request) == GOOD  # type: ignore[arg-type]

    flattened = gateway.requests[-1]
    assert flattened.tools is None and flattened.reasoning_effort is None
    assert flattened.messages[0].content == FLATTENED_SUMMARY_SYSTEM
    assert "user: Implement it" in (flattened.messages[1].content or "")
    assert (flattened.messages[1].content or "").endswith(SUMMARY_PROMPT)


async def test_a_second_compaction_still_carries_the_first_request() -> None:
    manager = ContextManager(recent_turns_to_preserve=2)

    async def summarize(request: list[ChatMessage]) -> str:
        return GOOD

    once = list(await manager.enforce_hygiene_async("m", _long_session(), summarize,
                                                    force_compaction=True))  # fmt: skip
    once.append(ChatMessage(role="user", content="and update the changelog"))
    for n in range(4):
        once += [
            ChatMessage(role="assistant", tool_calls=[_call(f"z{n}", "bash_exec", command="ls")]),
            ChatMessage(role="tool", content="ok", tool_call_id=f"z{n}"),
        ]
    twice = await manager.enforce_hygiene_async("m", once, summarize, force_compaction=True)

    text = "\n".join(m.content or "" for m in twice)
    assert "> Implement SVC-21926; never re-consume an event twice" in text
    assert "> also add a 15 minute default" in text
    assert text.count("Summary of the compacted work:") == 1

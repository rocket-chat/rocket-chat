"""The live progress message, the Stop button and the helpers for files."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

from slack_assistant.artifacts import (
    combined_diff,
    diff_is_large,
    head_of,
    screenshot_paths,
    upload_file,
)
from slack_assistant.block_kit import STOP_ACTION_ID, build_progress_blocks
from slack_assistant.progress import ProgressMessage

from specifications.interfaces.agent import FileDiff


class _Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


def _client() -> Any:
    client = AsyncMock()
    client.chat_postMessage.return_value = {"ts": "9.9"}
    return client


def _progress(client: Any, clock: _Clock, link: str | None = None) -> ProgressMessage:
    return ProgressMessage(
        client, "C1", "1.0", session_id="slack_C1_1_0", link_url=link, clock=clock
    )


def _actions(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [e for b in blocks if b["type"] == "actions" for e in b["elements"]]


async def test_the_message_starts_with_a_stop_button_and_a_link_to_the_session() -> None:
    client = _client()
    progress = _progress(client, _Clock(), "https://rocket.example/chat/slack_C1_1_0")
    await progress.start()

    sent = client.chat_postMessage.call_args.kwargs
    assert sent["thread_ts"] == "1.0"
    (button,) = _actions(sent["blocks"])
    assert button["action_id"] == STOP_ACTION_ID and button["value"] == "slack_C1_1_0"
    assert "Open in Rocket Chat" in str(sent["blocks"])
    assert progress.ts == "9.9"


async def test_updates_are_throttled_and_summarise_the_work() -> None:
    client, clock = _client(), _Clock()
    progress = _progress(client, clock)
    await progress.start()

    await progress.tool_started("file_edit", {"path": "a.py"}, "Applying edits to `a.py`...")
    assert client.chat_update.await_count == 0, "within the throttle window nothing is sent"

    clock.now += 2
    await progress.tool_started("bash_exec", {"command": "pytest"}, "Running test suite...")
    assert client.chat_update.await_count == 1
    text = str(client.chat_update.call_args.kwargs["blocks"])
    assert "Running test suite" in text and "Edited 1 file" in text and "Ran 1 command" in text


async def test_finishing_drops_the_stop_button_and_reports_the_outcome() -> None:
    client, clock = _client(), _Clock()
    progress = _progress(client, clock)
    await progress.start()
    await progress.tool_started("file_read", {"path": "a.py"}, "Reading")
    clock.now += 42

    await progress.finish(ok=True)

    blocks = client.chat_update.call_args.kwargs["blocks"]
    assert _actions(blocks) == []
    assert "Done" in str(blocks) and "42s" in str(blocks) and "Read 1 file" in str(blocks)


async def test_a_failed_or_stopped_turn_says_so() -> None:
    client = _client()
    progress = _progress(client, _Clock())
    await progress.start()
    await progress.finish(ok=False, error="model refused")
    assert "model refused" in str(client.chat_update.call_args.kwargs["blocks"])


async def test_a_workspace_that_refuses_the_message_just_loses_the_progress_message() -> None:
    client = AsyncMock()
    client.chat_postMessage.side_effect = RuntimeError("missing_scope")
    progress = _progress(client, _Clock())
    await progress.start()
    await progress.tool_started("bash_exec", {}, "x")
    await progress.finish(ok=True)
    assert not progress.active
    client.chat_update.assert_not_called()


async def test_repeated_update_failures_switch_the_message_off() -> None:
    client, clock = _client(), _Clock()
    client.chat_update.side_effect = RuntimeError("ratelimited")
    progress = _progress(client, clock)
    await progress.start()
    for _ in range(6):
        clock.now += 5
        await progress.note("working")
    assert client.chat_update.await_count == 3 and not progress.active


def test_without_a_stop_value_there_is_no_button_and_no_link_means_no_context() -> None:
    blocks = build_progress_blocks(headline="Done", steps=[], stop_value=None, link_url=None)
    assert [b["type"] for b in blocks] == ["section"]


def test_head_of_cuts_at_a_paragraph_when_it_can() -> None:
    text = "First paragraph here.\n\nSecond paragraph that is much longer than the limit allows."
    assert head_of(text, 40).startswith("First paragraph here.")
    assert head_of("short", 40) == "short"
    assert head_of("x" * 100, 40).endswith("…")


def test_screenshot_paths_are_found_once_and_only_inside_the_screenshot_folder() -> None:
    text = (
        "saved /workspace/.rocket/screenshots/home.png and again "
        "/workspace/.rocket/screenshots/home.png, not /workspace/secret.png"
    )
    assert screenshot_paths(text) == ["/workspace/.rocket/screenshots/home.png"]


def test_large_diffs_are_detected_and_combined() -> None:
    small = [FileDiff("a.py", "+x\n-y", 1, 1, False)]
    big = [FileDiff("b.py", "\n".join(f"+line {i}" for i in range(120)), 120, 0, True)]
    assert not diff_is_large(small) and diff_is_large(big)
    assert combined_diff(small).startswith("--- a.py\n+x")


async def test_an_upload_the_workspace_refuses_is_reported_not_raised() -> None:
    client = AsyncMock()
    client.files_upload_v2.side_effect = RuntimeError("missing_scope")
    assert not await upload_file(
        client, channel_id="C1", thread_ts="1.0", filename="a.md", content="x", title="t"
    )
    client.files_upload_v2.side_effect = None
    assert await upload_file(
        client, channel_id="C1", thread_ts="1.0", filename="a.png", content=b"\x89PNG", title="t"
    )
    assert client.files_upload_v2.call_args.kwargs["file"] == b"\x89PNG"

"""Tests for context policy: prefix stability, on-demand attachments, and tail notices."""

from __future__ import annotations

from agent_core.context_policy import (
    assemble_request_messages,
    format_attachment_manifest,
)

from specifications.interfaces.llm import AttachmentRef, ChatMessage


def test_attachment_manifest_format() -> None:
    att = AttachmentRef(
        name="dataset.json",
        mime="application/json",
        sha256="abc123hash",
        size=4096,
        workspace_path="/workspace/dataset.json",
        text='{"key": "huge payload"}',
    )
    manifest = format_attachment_manifest([att])
    assert "dataset.json" in manifest
    assert "application/json" in manifest
    assert "4,096 bytes" in manifest
    assert "/workspace/dataset.json" in manifest
    assert "file_read" in manifest
    assert "huge payload" not in manifest


def test_memory_text_appears_once_before_first_tool_result() -> None:
    system = ChatMessage(role="system", content="You are a helpful engineer.")
    user = ChatMessage(role="user", content="Please run analysis.")
    assistant_tool = ChatMessage(
        role="assistant",
        tool_calls=[
            {
                "id": "call_1",
                "type": "function",
                "function": {"name": "run_tests", "arguments": "{}"},
            }
        ],
    )
    tool_res = ChatMessage(role="tool", content="Tests passed.", tool_call_id="call_1")

    # Step 1: before tool calls
    step1_msgs = assemble_request_messages(
        [system, user],
        context_note="User role: Admin. Memory: prefer concise replies.",
    )
    # Memory must be right after the user message
    assert len(step1_msgs) == 3
    assert step1_msgs[0].content == system.content
    assert step1_msgs[1].content == user.content
    assert "prefer concise replies" in (step1_msgs[2].content or "")

    # Step 2: after tool calls
    step2_msgs = assemble_request_messages(
        [system, user, assistant_tool, tool_res],
        context_note="User role: Admin. Memory: prefer concise replies.",
    )
    # Memory must still be right after the user message and BEFORE the assistant tool call!
    assert len(step2_msgs) == 5
    assert step2_msgs[0].content == system.content
    assert step2_msgs[1].content == user.content
    assert "prefer concise replies" in (step2_msgs[2].content or "")
    assert step2_msgs[3].tool_calls == assistant_tool.tool_calls
    assert step2_msgs[4].content == tool_res.content


def test_requests_share_byte_identical_prefix() -> None:
    system = ChatMessage(role="system", content="System identity prompt.")
    user = ChatMessage(role="user", content="Deploy the app.")
    call1 = ChatMessage(
        role="assistant",
        tool_calls=[
            {"id": "c1", "type": "function", "function": {"name": "t1", "arguments": "{}"}}
        ],
    )
    res1 = ChatMessage(role="tool", content="t1 done", tool_call_id="c1")
    call2 = ChatMessage(
        role="assistant",
        tool_calls=[
            {"id": "c2", "type": "function", "function": {"name": "t2", "arguments": "{}"}}
        ],
    )
    res2 = ChatMessage(role="tool", content="t2 done", tool_call_id="c2")

    note = "Session memory: deployment target is us-east-1."

    # Request at step 2 (after tool 1)
    req2 = assemble_request_messages(
        [system, user, call1, res1],
        context_note=note,
        sticky_notes=["Budget notice: 5 steps remaining."],
    )
    # Request at step 3 (after tool 2)
    req3 = assemble_request_messages(
        [system, user, call1, res1, call2, res2],
        context_note=note,
        sticky_notes=["Budget notice: 4 steps remaining."],
    )

    # Prefix of req2 before tail notes: system, user, note, call1, res1
    # Check that req3's first 5 messages match req2's first 5 messages exactly!
    for i in range(5):
        assert req2[i].role == req3[i].role
        assert req2[i].content == req3[i].content
        assert req2[i].tool_calls == req3[i].tool_calls
        assert req2[i].tool_call_id == req3[i].tool_call_id


def test_view_image_at_step_3_leaves_earlier_requests_unchanged() -> None:
    system = ChatMessage(role="system", content="System prompt")
    user = ChatMessage(role="user", content="Check this diagram")
    call1 = ChatMessage(role="assistant", content="Checking files")
    img_ref = AttachmentRef(
        name="screenshot.png",
        mime="image/png",
        sha256="imgsha",
        size=10240,
        workspace_path="/workspace/screenshot.png",
        is_image=True,
    )

    # Request 1 (step 1): No images
    req1 = assemble_request_messages([system, user])
    assert not req1[1].attachments

    # Request 2 (step 2): Still no images
    req2 = assemble_request_messages([system, user, call1])
    assert not req2[1].attachments

    # Request 3 (step 3): view_image called, image added to turn_images
    req3 = assemble_request_messages([system, user, call1], turn_images=[img_ref])

    # Earlier user message in req3 must NOT have attachments retroactively modified
    assert not req3[1].attachments
    # Image rides at the tail!
    tail_msg = req3[-1]
    assert tail_msg.role == "user"
    assert tail_msg.attachments == [img_ref]

    # Requests 1 and 2 user message remains identical to req3's user message
    assert req1[1].attachments == req3[1].attachments

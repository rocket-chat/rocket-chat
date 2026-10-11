"""Unit tests for multimodal attachment formatting.

The gateway is the last stop before a provider call, so these tests pin the two
behaviours the migration depends on: an image reaches a vision model as an
``image_url`` block, and a text-only model degrades explicitly instead of receiving a
block it cannot parse.
"""

from __future__ import annotations

from llm_gateway.gateway import LiteLLMGateway

from specifications.interfaces.llm import AttachmentRef, ChatMessage


def _image_ref(name: str = "screenshot.png", mime: str = "image/png") -> AttachmentRef:
    return AttachmentRef(
        name=name,
        mime=mime,
        sha256="a" * 64,
        size=8,
        workspace_path=f"/workspace/.attachments/{'a' * 64}/{name}",
        is_image=True,
        data=b"\x89PNG\r\n\x1a\n",
    )


def _log_ref() -> AttachmentRef:
    return AttachmentRef(
        name="server.log",
        mime="text/plain",
        sha256="b" * 64,
        size=11,
        workspace_path=f"/workspace/.attachments/{'b' * 64}/server.log",
        is_image=False,
        text="ERROR boom",
    )


def test_supports_vision_truth_table() -> None:
    assert LiteLLMGateway._supports_vision("openrouter/anthropic/claude-3.7-sonnet")
    assert LiteLLMGateway._supports_vision("openrouter/openai/gpt-4o")
    assert LiteLLMGateway._supports_vision("gemini/gemini-2.5-pro")
    assert not LiteLLMGateway._supports_vision("openrouter/deepseek/deepseek-v4.1-flash")
    assert not LiteLLMGateway._supports_vision("")


def test_vision_model_emits_image_url_block() -> None:
    gateway = LiteLLMGateway()
    message = ChatMessage(
        role="user", content="What is in this screenshot?", attachments=[_image_ref()]
    )

    formatted = gateway._format_request_messages(
        [message], "openrouter/anthropic/claude-3.7-sonnet"
    )

    blocks = formatted[0]["content"]
    assert isinstance(blocks, list)
    image_blocks = [b for b in blocks if b["type"] == "image_url"]
    assert len(image_blocks) == 1
    assert image_blocks[0]["image_url"]["url"].startswith("data:image/png;base64,")


def test_non_vision_model_degrades_explicitly() -> None:
    gateway = LiteLLMGateway()
    message = ChatMessage(role="user", content="Read this", attachments=[_image_ref()])

    formatted = gateway._format_request_messages(
        [message], "openrouter/deepseek/deepseek-v4.1-flash"
    )

    blocks = formatted[0]["content"]
    assert all(block["type"] != "image_url" for block in blocks)
    notice = " ".join(block.get("text", "") for block in blocks)
    assert "cannot process image input" in notice
    assert "screenshot.png" in notice


def test_text_attachment_is_an_inlined_block() -> None:
    gateway = LiteLLMGateway()
    message = ChatMessage(role="user", content="Summarise this", attachments=[_log_ref()])

    formatted = gateway._format_request_messages(
        [message], "openrouter/deepseek/deepseek-v4.1-flash"
    )

    blocks = formatted[0]["content"]
    inlined = " ".join(block.get("text", "") for block in blocks)
    assert "ERROR boom" in inlined
    assert "server.log" in inlined


def test_message_without_attachments_is_unchanged() -> None:
    """A plain message must keep the scalar content form, not become a block list."""
    gateway = LiteLLMGateway()
    formatted = gateway._format_request_messages(
        [ChatMessage(role="user", content="hello")], "openrouter/deepseek/deepseek-v4.1-flash"
    )
    assert formatted[0]["content"] == "hello"

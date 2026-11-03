"""Unit tests for multimodal attachment formatting.

The gateway is the last stop before a provider call, so these tests pin the two
behaviours the migration depends on: an image reaches a vision model as an
``image_url`` block, and a text-only model degrades explicitly instead of receiving a
block it cannot parse.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from api.services.model_catalogue import ModelCatalogue
from api.services.provider_catalogue import parse_openrouter, provider_catalogue
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
    """Without the platform's catalogue (a gateway used on its own): LiteLLM, then the markers."""
    gateway = LiteLLMGateway()
    assert gateway._supports_vision("openrouter/anthropic/claude-3.7-sonnet")
    assert gateway._supports_vision("openrouter/openai/gpt-4o")
    assert gateway._supports_vision("gemini/gemini-2.5-pro")
    # Releases after the marker list was written: LiteLLM's model map knows they see images.
    assert gateway._supports_vision("openrouter/anthropic/claude-sonnet-5.5")
    assert gateway._supports_vision("anthropic/claude-opus-5-5")
    assert not gateway._supports_vision("")


@pytest.fixture
def openrouter_listing() -> Iterator[None]:
    """The provider's own listing, as fetched: one model reads images, one does not."""
    saved = dict(provider_catalogue._models)
    provider_catalogue.load(
        parse_openrouter(
            {
                "data": [
                    {
                        "id": "deepseek/deepseek-v4.1-flash",
                        "context_length": 1_048_576,
                        "architecture": {"input_modalities": ["text", "image"]},
                    },
                    {
                        "id": "acme/text-only-1",
                        "context_length": 131_072,
                        "architecture": {"input_modalities": ["text"]},
                    },
                ]
            }
        )
    )
    yield
    provider_catalogue._models = saved


def _formatted_blocks(model: str) -> list[dict[str, object]]:
    gateway = LiteLLMGateway()
    gateway.model_catalogue = ModelCatalogue()
    message = ChatMessage(role="user", content="Look at this", attachments=[_image_ref()])
    blocks = gateway._format_request_messages([message], model)[0]["content"]
    assert isinstance(blocks, list)
    return blocks


def test_a_model_the_provider_says_reads_images_gets_them(openrouter_listing: None) -> None:
    """LiteLLM's table does not list DeepSeek V4.1 Flash, so its images were withheld although
    OpenRouter serves it with image input."""
    blocks = _formatted_blocks("openrouter/deepseek/deepseek-v4.1-flash")
    assert [b["type"] for b in blocks].count("image_url") == 1


def test_a_model_the_provider_says_is_text_only_gets_the_notice(openrouter_listing: None) -> None:
    blocks = _formatted_blocks("openrouter/acme/text-only-1")
    assert all(b["type"] != "image_url" for b in blocks)
    assert "cannot process image input" in " ".join(str(b.get("text", "")) for b in blocks)


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

    formatted = gateway._format_request_messages([message], "openrouter/acme/text-only-1")

    blocks = formatted[0]["content"]
    assert all(block["type"] != "image_url" for block in blocks)
    notice = " ".join(block.get("text", "") for block in blocks)
    assert "cannot process image input" in notice
    assert "screenshot.png" in notice


def test_text_attachment_is_an_inlined_block() -> None:
    gateway = LiteLLMGateway()
    message = ChatMessage(role="user", content="Summarise this", attachments=[_log_ref()])

    formatted = gateway._format_request_messages([message], "openrouter/acme/text-only-1")

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


def test_the_model_listing_says_which_models_read_images(openrouter_listing: None) -> None:
    """The composer warns from this flag before an image goes to a model that cannot read it."""
    from api.routers.models_catalog import _with_image_input

    assert _with_image_input({"id": "openrouter/deepseek/deepseek-v4.1-flash"})["accepts_images"]
    assert _with_image_input({"id": "openrouter/acme/text-only-1"})["accepts_images"] is False
    # Nothing known about it: no flag, so nothing is claimed.
    assert "accepts_images" not in _with_image_input({"id": "acme-unknown/nothing"})

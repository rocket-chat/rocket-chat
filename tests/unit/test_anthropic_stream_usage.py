"""An Anthropic stream reports its real usage, prompt-cache reads and writes included, through
LiteLLM and the gateway: run against a local stand-in for the Messages API."""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest
from llm_gateway.gateway import LiteLLMGateway

from specifications.interfaces.llm import BYOKCredentials, ChatMessage, ModelRequest

USAGE = {
    "input_tokens": 1200,
    "cache_read_input_tokens": 30000,
    "cache_creation_input_tokens": 2000,
}
EVENTS: list[dict[str, Any]] = [
    {
        "type": "message_start",
        "message": {
            "id": "m1",
            "type": "message",
            "role": "assistant",
            "model": "claude-sonnet-5-5",
            "content": [],
            "stop_reason": None,
            "stop_sequence": None,
            "usage": {**USAGE, "output_tokens": 1},
        },
    },
    {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
    {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "Hi"}},
    {"type": "content_block_stop", "index": 0},
    {
        "type": "message_delta",
        "delta": {"stop_reason": "end_turn"},
        "usage": {"output_tokens": 487},
    },
    {"type": "message_stop"},
]


@pytest.fixture
def anthropic_api() -> Iterator[tuple[str, list[dict[str, Any]]]]:
    received: list[dict[str, Any]] = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_: Any) -> None:
            return None

        def do_POST(self) -> None:
            received.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            for event in EVENTS:
                self.wfile.write(f"event: {event['type']}\ndata: {json.dumps(event)}\n\n".encode())

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}", received
    server.shutdown()


async def test_an_anthropic_stream_reports_cache_reads_and_writes(
    anthropic_api: tuple[str, list[dict[str, Any]]],
) -> None:
    base, received = anthropic_api
    request = ModelRequest(
        model="anthropic/claude-sonnet-5-5",
        messages=[
            ChatMessage(role="system", content="Stable instructions. " * 400, cacheable=True),
            ChatMessage(role="user", content="hello"),
        ],
        credentials=BYOKCredentials(provider="anthropic", api_key="sk-ant-test", api_base=base),
        temperature=None,
    )

    final = [c async for c in LiteLLMGateway().chat_stream(request) if c.is_finished][-1]

    assert final.token_usage is not None, "the stream's usage was lost"
    assert final.token_usage["cache_read_tokens"] == 30000
    assert final.token_usage["cache_creation_tokens"] == 2000
    assert final.token_usage["completion_tokens"] == 487
    sent = received[0]
    assert "stream_options" not in sent, "the option must stay inside LiteLLM"
    assert sent["system"][0]["cache_control"] == {"type": "ephemeral"}

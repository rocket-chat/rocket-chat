"""Self-hosted models over the wire: vLLM-style and Ollama endpoints.

Real HTTP, real LiteLLM client: only the model server is a stand-in, running on loopback. This
is the evidence that an air-gapped deployment needs nothing but an ``api_base`` stored for the
organization - no external provider, key or network egress.
"""

from __future__ import annotations

import json
import threading
from collections.abc import AsyncIterator, Iterator
from typing import Any

import pytest
import uvicorn
from api.services.model_credentials import CredentialResolver, ModelCredentialStore
from api.services.settings import reset_memory_settings
from llm_gateway.gateway import LiteLLMGateway
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Route

from specifications.interfaces.llm import (
    ChatMessage,
    ModelRequest,
    ModelScope,
    StreamChunk,
    current_model_scope,
)

ORG, USER = "org_local", "member_local"


class FakeModelServer:
    """Speaks the OpenAI chat-completions SSE dialect (vLLM, LM Studio...) and Ollama's NDJSON."""

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self.headers: list[dict[str, str]] = []
        app = Starlette(
            routes=[
                Route("/v1/chat/completions", self._openai, methods=["POST"]),
                Route("/api/chat", self._ollama, methods=["POST"]),
            ]
        )
        self._server = uvicorn.Server(
            uvicorn.Config(app, host="127.0.0.1", port=0, log_level="error")
        )
        self._thread = threading.Thread(target=self._server.run, daemon=True)

    @property
    def port(self) -> int:
        return int(self._server.servers[0].sockets[0].getsockname()[1])

    def start(self) -> None:
        self._thread.start()
        while not self._server.started:
            threading.Event().wait(0.01)

    def stop(self) -> None:
        self._server.should_exit = True
        self._thread.join(timeout=5)

    async def _openai(self, request: Request) -> StreamingResponse:
        body = await request.json()
        self.requests.append(body)
        self.headers.append(dict(request.headers))

        async def events() -> AsyncIterator[str]:
            for piece in ("Hello from ", "the local model"):
                chunk = {
                    "id": "c1",
                    "object": "chat.completion.chunk",
                    "model": body["model"],
                    "choices": [{"index": 0, "delta": {"content": piece}, "finish_reason": None}],
                }
                yield f"data: {json.dumps(chunk)}\n\n"
            done = {
                "id": "c1",
                "object": "chat.completion.chunk",
                "model": body["model"],
                "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 7, "completion_tokens": 5, "total_tokens": 12},
            }
            yield f"data: {json.dumps(done)}\n\n"
            yield "data: [DONE]\n\n"

        return StreamingResponse(events(), media_type="text/event-stream")

    async def _ollama(self, request: Request) -> StreamingResponse | JSONResponse:
        body = await request.json()
        self.requests.append(body)
        self.headers.append(dict(request.headers))

        async def lines() -> AsyncIterator[str]:
            for piece in ("Ollama says ", "hi"):
                yield (
                    json.dumps(
                        {
                            "model": body["model"],
                            "message": {"role": "assistant", "content": piece},
                            "done": False,
                        }
                    )
                    + "\n"
                )
            yield (
                json.dumps(
                    {
                        "model": body["model"],
                        "message": {"role": "assistant", "content": ""},
                        "done": True,
                        "done_reason": "stop",
                        "prompt_eval_count": 9,
                        "eval_count": 4,
                    }
                )
                + "\n"
            )

        return StreamingResponse(lines(), media_type="application/x-ndjson")


@pytest.fixture
def model_server() -> Iterator[FakeModelServer]:
    server = FakeModelServer()
    server.start()
    yield server
    server.stop()


async def _ask(gateway: LiteLLMGateway, model: str) -> str:
    token = current_model_scope.set(ModelScope(ORG, USER, "s1"))
    try:
        request = ModelRequest(model=model, messages=[ChatMessage(role="user", content="hi")])
        chunks: list[StreamChunk] = [c async for c in gateway.chat_stream(request)]
    finally:
        current_model_scope.reset(token)
    errors = [c.error for c in chunks if c.error]
    assert not errors, errors
    return "".join(c.text_delta or "" for c in chunks)


async def _gateway_for(provider: str, port: int, key: str = "not-needed") -> LiteLLMGateway:
    reset_memory_settings()
    store = ModelCredentialStore(None)
    await store.put(
        ORG,
        None,
        provider,
        key,
        f"http://127.0.0.1:{port}" + ("/v1" if provider == "hosted_vllm" else ""),
    )
    gateway = LiteLLMGateway()
    gateway.credential_resolver = CredentialResolver(None, store)
    return gateway


async def test_an_openai_compatible_server_such_as_vllm_answers_through_the_org_endpoint(
    model_server: FakeModelServer,
) -> None:
    gateway = await _gateway_for("hosted_vllm", model_server.port)
    text = await _ask(gateway, "hosted_vllm/qwen2.5-coder-32b")

    assert text == "Hello from the local model"
    assert model_server.requests[0]["model"] == "qwen2.5-coder-32b"
    assert model_server.requests[0]["stream"] is True


async def test_an_ollama_server_answers_through_the_org_endpoint(
    model_server: FakeModelServer,
) -> None:
    gateway = await _gateway_for("ollama_chat", model_server.port)
    text = await _ask(gateway, "ollama_chat/qwen2.5-coder:32b")

    assert text == "Ollama says hi"
    assert model_server.requests[0]["model"] == "qwen2.5-coder:32b"


async def test_the_endpoint_comes_from_the_organization_not_the_environment(
    model_server: FakeModelServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Nothing about the model server is in the environment: the stored endpoint alone routes it.
    for name in ("OPENAI_API_BASE", "OLLAMA_API_BASE", "HOSTED_VLLM_API_BASE"):
        monkeypatch.delenv(name, raising=False)
    gateway = await _gateway_for("hosted_vllm", model_server.port)
    assert await _ask(gateway, "hosted_vllm/any-model")
    assert model_server.requests, "the request reached the self-hosted server"

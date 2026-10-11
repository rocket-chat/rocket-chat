"""Unit tests for the long-term-memory service.

Covers the four behaviours the service owns: dedupe-on-write, secret refusal, redaction
before storage, and bounded decay-ranked recall — all against the in-memory fallback so no
database is required. The cross-tenant integration proof lives in
``tests/integration/test_memory_recall.py``.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence

import pytest
from api.services import memory as memory_module
from api.services.memory import (
    MAX_IN_MEMORY_MEMORIES,
    MemoryPolicy,
    MemoryRecord,
    MemoryService,
    _decay_factor,
    format_recall_block,
    resolve_summary_model,
    secret_reason,
    summarise_transcript,
    write_session_summary,
)

from specifications.interfaces.llm import ChatMessage, StreamChunk


def _hash_embedder(dim: int = 64):
    """Deterministic, orthogonal-ish embeddings so distinct texts never dedupe."""

    async def _embed(text: str) -> list[float]:
        vector = [0.0] * dim
        digest = hashlib.sha256(text.strip().lower().encode()).hexdigest()
        vector[int(digest, 16) % dim] = 1.0
        return vector

    return _embed


def _mapped_embedder(mapping: dict[str, list[float]]):
    async def _embed(text: str) -> list[float]:
        return mapping.get(text.strip().lower(), [0.0, 0.0, 1.0])

    return _embed


def _record(*, created_at: int, last_used_at: int | None = None) -> MemoryRecord:
    return MemoryRecord(
        id="mem_test",
        org_id="default_org",
        user_id="dev_user",
        scope="user",
        kind="fact",
        content="x",
        confidence=1.0,
        source_session_id=None,
        created_at=created_at,
        last_used_at=last_used_at,
    )


def _service(**kwargs) -> MemoryService:
    kwargs.setdefault("embedder", _hash_embedder())
    return MemoryService(None, **kwargs)


@pytest.mark.asyncio
async def test_remember_creates_then_updates_near_duplicate() -> None:
    embedder = _mapped_embedder(
        {
            "production deploys run at 09:00 utc": [1.0, 0.0, 0.0],
            "production deploys happen at 09:00 utc": [0.99, 0.0995, 0.0],
        }
    )
    service = _service(embedder=embedder)

    first = await service.remember(
        org_id="default_org",
        user_id="dev_user",
        content="Production deploys run at 09:00 UTC",
    )
    second = await service.remember(
        org_id="default_org",
        user_id="dev_user",
        content="Production deploys happen at 09:00 UTC",
    )

    assert first.status == "created"
    assert second.status == "updated"
    assert second.record is not None
    assert first.record is not None
    assert second.record.id == first.record.id
    records = await service.list_memories(org_id="default_org", user_id="dev_user")
    assert len(records) == 1
    assert records[0].content == "Production deploys happen at 09:00 UTC"


@pytest.mark.asyncio
async def test_remember_refuses_credentials_and_does_not_echo_them() -> None:
    service = _service()
    secret = "Here is my key sk-abcdefghijklmnopqrstuvwxyz0123456789"

    outcome = await service.remember(org_id="default_org", user_id="dev_user", content=secret)

    assert outcome.status == "blocked"
    assert outcome.reason is not None
    assert "credential" in outcome.reason
    assert "sk-abcdefghijklmnopqrstuvwxyz" not in outcome.reason
    assert await service.list_memories(org_id="default_org", user_id="dev_user") == []


@pytest.mark.asyncio
async def test_remember_refuses_high_entropy_token() -> None:
    service = _service()
    token = "QWxhZGRpbjpvcGVuIHNlc2FtZQ" * 2 + "0123456789abcdef"

    outcome = await service.remember(org_id="default_org", user_id="dev_user", content=token)

    assert outcome.status == "blocked"
    assert outcome.reason is not None
    assert "entropy" in outcome.reason


def test_secret_reason_ignores_ordinary_prose() -> None:
    assert secret_reason("The team prefers trunk-based development.") is None
    assert secret_reason("api_key = 'abc'") is not None
    assert secret_reason("-----BEGIN RSA PRIVATE KEY-----") is not None


@pytest.mark.asyncio
async def test_redaction_hook_scrubs_before_storage() -> None:
    def redactor(text: str) -> tuple[str, int]:
        scrubbed = text.replace("Alice", "[REDACTED]")
        return scrubbed, 1 if scrubbed != text else 0

    service = _service(redactor=redactor)
    outcome = await service.remember(
        org_id="default_org", user_id="dev_user", content="Alice owns the deploy pipeline"
    )

    assert outcome.status == "created"
    assert outcome.redactions == 1
    records = await service.list_memories(org_id="default_org", user_id="dev_user")
    assert records[0].content == "[REDACTED] owns the deploy pipeline"


@pytest.mark.asyncio
async def test_recall_is_bounded_and_ranked_by_similarity() -> None:
    embedder = _mapped_embedder(
        {
            "deploys run at 09:00": [1.0, 0.0, 0.0],
            "releases every tuesday": [0.0, 1.0, 0.0],
            "lunch is at noon": [0.0, 0.0, 1.0],
        }
    )
    service = _service(embedder=embedder)
    for content in ("deploys run at 09:00", "releases every tuesday", "lunch is at noon"):
        await service.remember(org_id="default_org", user_id="dev_user", content=content)

    results = await service.recall(
        org_id="default_org", user_id="dev_user", query="deploys run at 09:00", limit=2
    )

    assert len(results) == 2
    assert results[0].content == "deploys run at 09:00"
    assert results[0].similarity is not None and results[0].similarity > 0.9


@pytest.mark.asyncio
async def test_recall_respects_hard_limit() -> None:
    service = _service()
    for index in range(30):
        await service.remember(
            org_id="default_org", user_id="dev_user", content=f"distinct memory number {index}"
        )

    results = await service.recall(
        org_id="default_org", user_id="dev_user", query="memory", limit=10_000
    )

    assert len(results) <= memory_module.MAX_RECALL_LIMIT


def test_decay_factor_halves_at_half_life_and_rewards_recency() -> None:
    now = 1_700_000_000
    day = 86400
    stale = _record(created_at=now - 30 * day)
    used = _record(created_at=now - 300 * day, last_used_at=now)

    assert _decay_factor(stale, now, 30.0) == pytest.approx(0.5, abs=1e-6)
    assert _decay_factor(used, now, 30.0) == pytest.approx(1.0, abs=1e-6)


@pytest.mark.asyncio
async def test_disabled_policy_blocks_writes_and_reads() -> None:
    service = _service(policy=MemoryPolicy(enabled=False))

    outcome = await service.remember(
        org_id="default_org", user_id="dev_user", content="Something worth keeping"
    )
    recalled = await service.recall(org_id="default_org", user_id="dev_user", query="something")

    assert outcome.status == "blocked"
    assert recalled == []


@pytest.mark.asyncio
async def test_scope_allowlist_rejects_disallowed_scope() -> None:
    service = _service(policy=MemoryPolicy(scope_allowlist=("user",)))

    outcome = await service.remember(
        org_id="default_org", user_id="dev_user", content="Org-wide convention", scope="org"
    )

    assert outcome.status == "blocked"
    assert outcome.reason is not None
    assert "scope" in outcome.reason


@pytest.mark.asyncio
async def test_in_memory_fallback_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(memory_module, "MAX_IN_MEMORY_MEMORIES", 5)
    service = _service()
    for index in range(12):
        await service.remember(
            org_id="default_org", user_id="dev_user", content=f"fact number {index}"
        )

    assert len(service._memory) == 5


@pytest.mark.asyncio
async def test_user_and_org_scope_visibility() -> None:
    service = _service()
    await service.remember(
        org_id="default_org", user_id="dev_user", content="Org shared convention", scope="org"
    )
    await service.remember(
        org_id="default_org", user_id="dev_user", content="Alice private preference", scope="user"
    )

    other = await service.list_memories(org_id="default_org", user_id="dev_bob")
    contents = {record.content for record in other}
    assert "Org shared convention" in contents
    assert "Alice private preference" not in contents


def test_format_recall_block_is_empty_without_records() -> None:
    assert format_recall_block([]) == ""
    records: Sequence[MemoryRecord] = [_record(created_at=1)]
    block = format_recall_block(records)
    assert "Recalled Long-Term Memory" in block
    assert "mem_test" not in block


def test_remember_max_constant_is_positive() -> None:
    assert MAX_IN_MEMORY_MEMORIES > 0


class _TextGateway:
    """Minimal gateway stand-in that returns one fixed summary sentence."""

    def __init__(self, text: str = "Refactored the deploy pipeline.") -> None:
        self.text = text

    async def chat_stream(self, request):
        yield StreamChunk(text_delta=self.text)


class _BrokenGateway:
    async def chat_stream(self, request):
        raise RuntimeError("summary provider down")
        yield StreamChunk()  # pragma: no cover - makes this an async generator


def _recording_service() -> tuple[MemoryService, dict]:
    service = _service()
    written: dict = {}

    async def _capture(**kwargs):
        written.update(kwargs)

    service.write_summary = _capture  # type: ignore[method-assign]
    return service, written


@pytest.mark.asyncio
async def test_write_session_summary_distils_and_persists() -> None:
    service, written = _recording_service()

    await write_session_summary(
        memory_service=service,
        gateway=_TextGateway(),
        model="openrouter/cheap-model",
        session_id="sess-summary",
        org_id="default_org",
        conversation=[
            ChatMessage(role="user", content="Refactor the deploy pipeline"),
            ChatMessage(role="assistant", content="Done, with a retry."),
        ],
        turn_index=2,
    )

    assert written["summary_text"] == "Refactored the deploy pipeline."
    assert written["session_id"] == "sess-summary"
    assert written["turn_end"] == 2


@pytest.mark.asyncio
async def test_write_session_summary_never_fails_the_turn() -> None:
    service, written = _recording_service()

    # A down provider is logged and skipped; it must not raise into the turn's finally.
    await write_session_summary(
        memory_service=service,
        gateway=_BrokenGateway(),
        model="m",
        session_id="sess-summary",
        org_id="default_org",
        conversation=[ChatMessage(role="user", content="hello")],
        turn_index=1,
    )

    assert written == {}


def test_resolve_summary_model_prefers_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(memory_module.SUMMARY_MODEL_ENV, raising=False)
    assert resolve_summary_model("default/model") == "default/model"

    monkeypatch.setenv(memory_module.SUMMARY_MODEL_ENV, "openrouter/cheap")
    assert resolve_summary_model("default/model") == "openrouter/cheap"


def test_summarise_transcript_is_bounded() -> None:
    messages = [ChatMessage(role="user", content="x" * 10_000)]
    assert len(summarise_transcript(messages)) <= memory_module.SUMMARY_TRANSCRIPT_CHARS

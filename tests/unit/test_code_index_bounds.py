"""Unit tests for the bounded in-memory semantic code index.

These tests pin the memory-bounding contract: float32 vectors, byte accounting, and a
cache that can never grow without limit (the most likely cause of an OOMKill in a
long-lived pod serving many sessions).
"""

from __future__ import annotations

import time
from array import array

import pytest
from api.services import code_index as code_index_module
from api.services.code_index import (
    CHUNK_OVERHEAD_BYTES,
    EMBEDDING_BYTES,
    CodeIndex,
    CodeIndexCache,
    build_code_index,
)
from sandbox_driver.fake_driver import FakeSandboxDriver

VECTOR_DIM = 1536


def _vector(seed: float = 1.0) -> list[float]:
    return [seed] * VECTOR_DIM


def _index_with(chunks: int, text_size: int = 40) -> CodeIndex:
    index = CodeIndex()
    for number in range(chunks):
        index.add(f"src/file_{number}.py", 1, "x" * text_size, _vector(seed=float(number + 1)))
    return index


def test_vectors_are_packed_as_float32() -> None:
    """Embeddings are stored as float32 arrays, not Python float lists."""
    index = CodeIndex()
    index.add("src/app.py", 1, "print(1)", _vector())

    stored = index.entries[0].embedding
    assert isinstance(stored, array)
    assert stored.typecode == "f"
    assert len(stored) == VECTOR_DIM
    # A list of Python floats costs ~24 bytes per element; float32 costs 4.
    assert index.byte_size < VECTOR_DIM * 8


def test_byte_accounting_matches_payload_and_overhead() -> None:
    """Byte accounting reflects vectors, text, path and per-chunk overhead."""
    text = "def handler():\n    return 1\n"
    index = CodeIndex()
    index.add("src/handler.py", 12, text, _vector())

    expected = (
        VECTOR_DIM * EMBEDDING_BYTES
        + len(text.encode("utf-8"))
        + len(b"src/handler.py")
        + CHUNK_OVERHEAD_BYTES
    )
    assert index.byte_size == expected
    assert index.size == 1


def test_float32_ranking_preserves_the_best_match() -> None:
    """Cosine ranking still finds the closest vector after packing."""
    index = CodeIndex()
    index.add("exact.py", 1, "exact", [1.0] + [0.0] * (VECTOR_DIM - 1))
    index.add("orthogonal.py", 1, "orthogonal", [0.0, 1.0] + [0.0] * (VECTOR_DIM - 2))

    results = index.search([1.0] + [0.0] * (VECTOR_DIM - 1), limit=2)

    assert next(chunk.path for _, chunk in results) == "exact.py"
    assert results[0][0] == pytest.approx(1.0, abs=1e-6)


@pytest.mark.asyncio
async def test_build_honours_the_byte_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    """A build stops once its byte budget is reached."""
    driver = FakeSandboxDriver(seed_files={f"src/f{number}.py": "y" * 500 for number in range(40)})

    async def fake_embedding(text: str, model: str = "") -> list[float]:
        return _vector(seed=0.5)

    monkeypatch.setattr(code_index_module, "compute_embedding", fake_embedding)

    budget = 200_000
    index = await build_code_index(driver, "session_build", max_bytes=budget, max_chunks=10_000)

    assert index.size > 0
    assert index.byte_size <= budget + VECTOR_DIM * EMBEDDING_BYTES + 1024


@pytest.mark.asyncio
async def test_build_honours_the_chunk_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    """The chunk budget still caps the number of indexed chunks."""
    driver = FakeSandboxDriver(seed_files={f"src/f{number}.py": "y" * 100 for number in range(20)})

    async def fake_embedding(text: str, model: str = "") -> list[float]:
        return _vector(seed=0.25)

    monkeypatch.setattr(code_index_module, "compute_embedding", fake_embedding)

    index = await build_code_index(driver, "session_chunks", max_chunks=3, max_bytes=10**9)

    assert index.size == 3


def test_cache_evicts_least_recently_used_sessions() -> None:
    """Session count is bounded and the oldest untouched entry goes first."""
    cache = CodeIndexCache(max_sessions=2, max_bytes=10**9, ttl_seconds=0)
    cache.put("s1", _index_with(1))
    cache.put("s2", _index_with(1))

    # Touch s1 so s2 becomes the least recently used entry.
    assert cache.get("s1") is not None
    cache.put("s3", _index_with(1))

    assert cache.get("s2") is None
    assert cache.get("s1") is not None
    assert cache.get("s3") is not None
    assert cache.stats()["sessions"] == 2
    assert cache.stats()["evictions"] == 1


def test_cache_evicts_when_the_byte_budget_is_exceeded() -> None:
    """Total resident bytes stay within the byte budget."""
    single = _index_with(1)
    cache = CodeIndexCache(max_sessions=100, max_bytes=single.byte_size * 2, ttl_seconds=0)

    cache.put("s1", _index_with(1))
    cache.put("s2", _index_with(1))
    cache.put("s3", _index_with(1))

    assert cache.stats()["sessions"] == 2
    assert cache.total_bytes <= cache.max_bytes
    assert cache.get("s1") is None


def test_cache_expires_entries_after_the_ttl() -> None:
    """A stored index older than the TTL is discarded on access."""
    cache = CodeIndexCache(max_sessions=4, max_bytes=10**9, ttl_seconds=0.01)
    cache.put("s1", _index_with(1))

    time.sleep(0.05)

    assert cache.get("s1") is None
    assert cache.stats()["sessions"] == 0
    assert cache.stats()["evictions"] == 1


def test_cache_refuses_an_index_larger_than_the_whole_budget() -> None:
    """An over-budget index is refused loudly rather than stored."""
    cache = CodeIndexCache(max_sessions=4, max_bytes=1024, ttl_seconds=0)

    assert cache.put("huge", _index_with(50)) is False
    assert cache.stats()["refusals"] == 1
    assert cache.stats()["sessions"] == 0


def test_cache_invalidate_and_clear() -> None:
    """Session eviction and full teardown both release memory."""
    cache = CodeIndexCache(max_sessions=4, max_bytes=10**9, ttl_seconds=0)
    cache.put("s1", _index_with(1))
    cache.put("s2", _index_with(1))

    assert cache.invalidate("s1") is True
    assert cache.invalidate("s1") is False
    assert cache.stats()["sessions"] == 1

    cache.clear()
    assert cache.stats()["sessions"] == 0
    assert cache.total_bytes == 0


def test_cache_rejects_invalid_limits() -> None:
    """Misconfiguration fails at construction rather than at runtime."""
    with pytest.raises(ValueError):
        CodeIndexCache(max_sessions=0)
    with pytest.raises(ValueError):
        CodeIndexCache(max_bytes=0)


def test_hit_and_miss_counters_are_observable() -> None:
    """Counters exist so the cache can be monitored in production."""
    cache = CodeIndexCache(max_sessions=2, max_bytes=10**9, ttl_seconds=0)
    cache.put("s1", _index_with(1))

    assert cache.get("s1") is not None
    assert cache.get("absent") is None

    stats = cache.stats()
    assert stats["hits"] == 1
    assert stats["misses"] == 1
    assert stats["max_sessions"] == 2
    assert stats["max_bytes"] == 10**9

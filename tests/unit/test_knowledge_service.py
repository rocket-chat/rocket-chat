"""Unit tests for pgvector Knowledge Base ingestion, chunking, and similarity query service."""

import pytest
from api.services.knowledge import chunk_text, compute_embedding, generate_deterministic_embedding


def test_chunk_text_preserves_boundaries() -> None:
    text = "Paragraph one with some technical explanations.\n\nParagraph two with architecture definitions."
    chunks = chunk_text(text, max_chars=100, overlap_chars=20)
    assert len(chunks) >= 1
    assert any("Paragraph one" in c for c in chunks)


def test_generate_deterministic_embedding_dimension() -> None:
    embedding = generate_deterministic_embedding(
        "Platform architecture and docker drivers", dim=1536
    )
    assert len(embedding) == 1536
    assert isinstance(embedding[0], float)
    # Norm check: should be unit length
    magnitude = sum(x * x for x in embedding) ** 0.5
    assert abs(magnitude - 1.0) < 1e-4


@pytest.mark.asyncio
async def test_compute_embedding_returns_unit_vector() -> None:
    embedding = await compute_embedding("Platform architecture and docker drivers")
    assert len(embedding) == 1536
    assert isinstance(embedding[0], float)


class _RecordingSession:
    """Just enough of AsyncSession for ingestion: collects adds, no database."""

    def __init__(self) -> None:
        self.added: list[object] = []

    def add(self, row: object) -> None:
        self.added.append(row)

    async def flush(self) -> None:
        return None

    async def refresh(self, row: object) -> None:
        return None


@pytest.mark.asyncio
async def test_oversized_document_is_refused_before_any_embedding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from api.services import knowledge

    monkeypatch.setenv("ROCKET_KB_MAX_DOCUMENT_CHARS", "100")
    embedded: list[str] = []

    async def spy(text: str, model_name: str = "x") -> list[float]:
        embedded.append(text)
        return [0.0]

    monkeypatch.setattr(knowledge, "compute_embedding", spy)
    session = _RecordingSession()
    with pytest.raises(knowledge.IngestionLimitError, match="limit is 100"):
        await knowledge.ingest_document(session, "kb", "org", "t", "x" * 101)  # type: ignore[arg-type]
    assert embedded == [] and session.added == []


@pytest.mark.asyncio
async def test_too_many_chunks_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    from api.services import knowledge

    monkeypatch.setenv("ROCKET_KB_MAX_CHUNKS_PER_DOCUMENT", "2")
    content = "\n\n".join("paragraph " * 150 for _ in range(6))
    with pytest.raises(knowledge.IngestionLimitError, match="chunks"):
        await knowledge.ingest_document(_RecordingSession(), "kb", "org", "t", content)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_chunks_are_embedded_in_order_with_bounded_concurrency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import asyncio

    from api.services import knowledge

    in_flight = peak = 0

    async def fake(text: str, model_name: str = "x") -> list[float]:
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        await asyncio.sleep(0.001)
        in_flight -= 1
        return [float(len(text))]

    monkeypatch.setattr(knowledge, "compute_embedding", fake)
    content = "\n\n".join(f"paragraph {i} " * 120 for i in range(30))
    session = _RecordingSession()
    await knowledge.ingest_document(session, "kb", "org", "t", content)  # type: ignore[arg-type]

    chunks = [row for row in session.added if hasattr(row, "chunk_index")]
    assert len(chunks) > knowledge.EMBEDDING_CONCURRENCY
    assert [c.chunk_index for c in chunks] == list(range(len(chunks)))
    assert all(c.embedding == [float(len(c.content))] for c in chunks)
    assert peak <= knowledge.EMBEDDING_CONCURRENCY

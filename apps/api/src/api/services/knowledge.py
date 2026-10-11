"""Knowledge Base ingestion, chunking, embedding generation, and pgvector cosine search."""

import asyncio
import hashlib
import logging
import math
import os
import re
import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.db.models import KnowledgeBaseModel, KnowledgeChunkModel, KnowledgeDocumentModel

logger = logging.getLogger(__name__)


# Fallback deterministic pseudo-embedding generator (1536d) when external embedding API key is absent
def generate_deterministic_embedding(text: str, dim: int = 1536) -> list[float]:
    """Generate normalized 1536-dimensional embedding vector from text hash."""
    vector = [0.0] * dim
    clean = text.strip().lower()
    for i, word in enumerate(clean.split()):
        h = int(hashlib.sha256(f"{word}_{i % 64}".encode()).hexdigest(), 16)
        idx = h % dim
        vector[idx] += 1.0

    # Fallback if text is empty
    if sum(vector) == 0.0:
        h = int(hashlib.sha256(clean.encode() or b"empty").hexdigest(), 16)
        vector[h % dim] = 1.0

    norm = math.sqrt(sum(v * v for v in vector))
    if norm > 0:
        vector = [v / norm for v in vector]
    return vector


async def compute_embedding(text: str, model_name: str = "text-embedding-3-small") -> list[float]:
    """Compute text embedding vector via LiteLLM or deterministic fallback."""
    try:
        import litellm

        logger.debug("Requesting embedding from LiteLLM for text prefix: '%s...'", text[:40])
        response = await litellm.aembedding(
            model=model_name,
            input=[text],
        )
        if response and hasattr(response, "data") and len(response.data) > 0:
            raw_vec = response.data[0]["embedding"]
            logger.debug("Successfully generated embedding of dimension %d", len(raw_vec))
            return [float(x) for x in raw_vec]
    except Exception as err:
        logger.debug("LiteLLM embedding call unavailable (%s); using deterministic vector", err)

    return generate_deterministic_embedding(text)


def chunk_text(content: str, max_chars: int = 1200, overlap_chars: int = 150) -> list[str]:
    """Split content into overlapping text chunks honoring paragraph boundaries."""
    if len(content) <= max_chars:
        return [content.strip()] if content.strip() else []

    paragraphs = re.split(r"\n\s*\n", content)
    chunks: list[str] = []
    current_chunk = ""

    for para in paragraphs:
        cleaned_para = para.strip()
        if not cleaned_para:
            continue

        if len(current_chunk) + len(cleaned_para) + 2 <= max_chars:
            current_chunk = f"{current_chunk}\n\n{cleaned_para}".strip()
        else:
            if current_chunk:
                chunks.append(current_chunk)
            if len(cleaned_para) > max_chars:
                # Sub-chunk very long single paragraph
                step = max_chars - overlap_chars
                for i in range(0, len(cleaned_para), step):
                    sub = cleaned_para[i : i + max_chars].strip()
                    if sub:
                        chunks.append(sub)
                current_chunk = ""
            else:
                current_chunk = cleaned_para

    if current_chunk:
        chunks.append(current_chunk)

    return chunks


class IngestionLimitError(ValueError):
    """The document exceeds what one ingestion call may embed."""


# One document is embedded chunk by chunk, so an unbounded upload is both a memory and a
# provider-cost amplifier. The defaults hold a long book; deployments tune them by env.
DEFAULT_MAX_DOCUMENT_CHARS = 2_000_000
DEFAULT_MAX_CHUNKS_PER_DOCUMENT = 2_000
EMBEDDING_CONCURRENCY = 8


def _env_limit(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    return int(raw) if raw.isdigit() and int(raw) >= 1 else default


def max_document_chars() -> int:
    return _env_limit("ROCKET_KB_MAX_DOCUMENT_CHARS", DEFAULT_MAX_DOCUMENT_CHARS)


def max_chunks_per_document() -> int:
    return _env_limit("ROCKET_KB_MAX_CHUNKS_PER_DOCUMENT", DEFAULT_MAX_CHUNKS_PER_DOCUMENT)


async def _embed_all(chunks: list[str]) -> list[list[float]]:
    gate = asyncio.Semaphore(EMBEDDING_CONCURRENCY)

    async def embed(chunk: str) -> list[float]:
        async with gate:
            return await compute_embedding(chunk)

    return list(await asyncio.gather(*(embed(chunk) for chunk in chunks)))


async def ingest_document(
    session: AsyncSession,
    kb_id: str,
    org_id: str,
    title: str,
    content: str,
    source_type: str = "text",
    metadata_json: dict[str, Any] | None = None,
) -> KnowledgeDocumentModel:
    """Chunk and vectorize a document into knowledge_documents and knowledge_chunks."""
    if len(content) > max_document_chars():
        raise IngestionLimitError(
            f"Document is {len(content):,} characters; the limit is {max_document_chars():,}."
        )
    chunks_text = chunk_text(content)
    chunk_count = len(chunks_text)
    if chunk_count > max_chunks_per_document():
        raise IngestionLimitError(
            f"Document splits into {chunk_count:,} chunks; the limit is "
            f"{max_chunks_per_document():,}."
        )
    doc_id = f"doc-{uuid.uuid4().hex[:12]}"
    vectors = await _embed_all(chunks_text)

    doc = KnowledgeDocumentModel(
        id=doc_id,
        kb_id=kb_id,
        org_id=org_id,
        title=title,
        source_type=source_type,
        content=content,
        chunk_count=chunk_count,
        metadata_json=metadata_json or {},
    )
    session.add(doc)

    logger.info(
        "Ingesting document '%s' (id=%s) into KB %s with %d chunks",
        title,
        doc_id,
        kb_id,
        chunk_count,
    )

    for idx, (c_text, vector) in enumerate(zip(chunks_text, vectors, strict=True)):
        chunk_id = f"chunk-{uuid.uuid4().hex[:12]}"
        chunk_model = KnowledgeChunkModel(
            id=chunk_id,
            doc_id=doc_id,
            kb_id=kb_id,
            org_id=org_id,
            chunk_index=idx,
            content=c_text,
            embedding=vector,
            token_count=max(1, len(c_text) // 4),
            metadata_json={"title": title, "chunk_index": idx},
        )
        session.add(chunk_model)

    # Flush, don't commit: the tenant context is transaction-local, so a commit here would
    # make the refresh (and the caller's next statement) run with no organization and, under
    # row-level security, see nothing. The caller's session context commits.
    await session.flush()
    await session.refresh(doc)
    return doc


async def query_knowledge_base(
    session: AsyncSession,
    kb_id: str,
    org_id: str,
    query_text: str,
    limit: int = 5,
) -> list[dict[str, Any]]:
    """Execute cosine distance query against knowledge_chunks."""
    query_vector = await compute_embedding(query_text)
    logger.debug("Querying KB %s for '%s' with limit %d", kb_id, query_text, limit)

    # pgvector cosine distance: embedding.cosine_distance(query_vector)
    # Cosine similarity: 1 - cosine_distance
    distance_expr = KnowledgeChunkModel.embedding.cosine_distance(query_vector)
    stmt = (
        select(
            KnowledgeChunkModel.id,
            KnowledgeChunkModel.content,
            KnowledgeChunkModel.chunk_index,
            KnowledgeDocumentModel.title,
            (1.0 - distance_expr).label("similarity"),
        )
        .join(KnowledgeDocumentModel, KnowledgeChunkModel.doc_id == KnowledgeDocumentModel.id)
        .where(KnowledgeChunkModel.kb_id == kb_id)
        .where(KnowledgeChunkModel.org_id == org_id)
        .order_by(distance_expr.asc())
        .limit(limit)
    )

    result = await session.execute(stmt)
    rows = result.all()

    results: list[dict[str, Any]] = []
    for row in rows:
        sim = float(row.similarity) if row.similarity is not None else 0.0
        results.append(
            {
                "id": str(row.id),
                "content": str(row.content),
                "chunk_index": int(row.chunk_index),
                "title": str(row.title),
                "score": round(max(0.0, min(1.0, sim)), 4),
            }
        )

    logger.debug("Query returned %d matching chunks", len(results))
    return results


# Searching more collections than this per call costs an embedding lookup each and rarely helps.
MAX_COLLECTIONS_PER_SEARCH = 5


async def search_knowledge(
    session: AsyncSession,
    *,
    org_id: str,
    user_id: str,
    query_text: str,
    kb_ref: str | None = None,
    limit: int = 5,
    allowed_kb_ids: Sequence[str] = (),
) -> str:
    """Answer the agent's ``kb_search`` from the collections it may read.

    The candidates are the organization's collections plus the member's own personal ones,
    narrowed to the bound persona's ``allowed_kb_ids`` when it has any. ``kb_ref`` selects one
    by id or (case-insensitive) name; an unknown or disallowed reference lists what is
    available instead of silently searching something else.
    """
    stmt = select(KnowledgeBaseModel).where(
        KnowledgeBaseModel.org_id == org_id,
        or_(KnowledgeBaseModel.scope == "org", KnowledgeBaseModel.user_id == user_id),
    )
    if allowed_kb_ids:
        stmt = stmt.where(KnowledgeBaseModel.id.in_(list(allowed_kb_ids)))
    candidates = list((await session.execute(stmt.order_by(KnowledgeBaseModel.name))).scalars())
    if not candidates:
        return "No knowledge base is available to this agent."

    if kb_ref:
        wanted = kb_ref.strip().lower()
        candidates = [kb for kb in candidates if kb.id == kb_ref or kb.name.lower() == wanted]
        if not candidates:
            return f"Knowledge base '{kb_ref}' is not available to this agent."

    hits: list[dict[str, Any]] = []
    for kb in candidates[:MAX_COLLECTIONS_PER_SEARCH]:
        for hit in await query_knowledge_base(session, kb.id, org_id, query_text, limit=limit):
            hits.append({**hit, "kb": kb.name})
    hits.sort(key=lambda hit: hit["score"], reverse=True)
    if not hits:
        return f"No knowledge base entries matched '{query_text}'."
    return "\n\n".join(
        f"[{h['title']} - {h['kb']} (score {h['score']})]\n{h['content']}" for h in hits[:limit]
    )

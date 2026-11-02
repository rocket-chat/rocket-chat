"""Knowledge bases: create, ingest, query."""

import logging
import time
import uuid
from typing import Any, Literal

from fastapi import (
    APIRouter,
    HTTPException,
    Request,
)
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth.access import _get_request_identity
from api.db.models import (
    KnowledgeBaseModel,
    KnowledgeDocumentModel,
)
from api.models import (
    KnowledgeBaseCreateRequest,
    KnowledgeBaseResponse,
    KnowledgeBaseUpdateRequest,
    KnowledgeDocumentCreateRequest,
    KnowledgeDocumentResponse,
    KnowledgeQueryRequest,
    KnowledgeQueryResponse,
    KnowledgeQueryResultItem,
)
from api.services.git_credentials import resolve_session_git_credentials
from api.services.git_gateway_env import github_api_url_of
from api.services.knowledge import (
    IngestionLimitError,
    ingest_document,
    max_document_chars,
    query_knowledge_base,
)
from api.services.knowledge_import import (
    ImportedDocument,
    KnowledgeImportError,
    import_repository_docs,
    import_site,
    import_url,
    pdf_from_base64,
)
from api.services.memory import configured_embedding_model
from api.services.ownership import (
    OwnershipError,
    manageable,
    owner_columns,
    owner_for_write,
    owner_of,
    request_viewer,
    visible,
    visible_clause,
)
from api.services.settings import effective_settings
from api.services.teams import note_memory_owned
from specifications.interfaces.ownership import Owner, Viewer

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1", tags=["Sessions & Telemetry"])


# ============================================================================
# KNOWLEDGE BASES (PGVECTOR) REST API
# ============================================================================

# In-memory fallback cache when PostgreSQL DatabaseManager is absent in tests
_kb_fallback_store: dict[str, dict[str, Any]] = {
    "kb-architecture": {
        "id": "kb-architecture",
        "name": "Platform Architecture & RFCs",
        "description": "Architectural guidelines, ReAct state diagrams, and RFC contracts",
        "vector_index": "pgvector_hnsw_platform_docs",
        "embedding_model": "text-embedding-3-small (1536d)",
        "hybrid_search": True,
        "documents": {},
        "created_at": int(time.time()),
        "updated_at": int(time.time()),
    }
}


async def _embedding_model(db_mgr: Any, org_id: str) -> str:
    """The organization's embedding model: documents and queries must use the same one."""
    return configured_embedding_model(await effective_settings(db_mgr, org_id, "memory"))


def _base_response(kb: Any, documents_count: int) -> KnowledgeBaseResponse:
    """A stored knowledge base (a row or an in-memory record) as the API reports it."""

    def field(name: str, default: Any = None) -> Any:
        value = kb.get(name) if isinstance(kb, dict) else getattr(kb, name, None)
        return default if value is None else value

    return KnowledgeBaseResponse(
        id=field("id"),
        name=field("name"),
        description=field("description", ""),
        scope=field("scope", "org"),
        user_id=field("user_id"),
        team_id=field("team_id"),
        vector_index=field("vector_index"),
        embedding_model=field("embedding_model"),
        hybrid_search=field("hybrid_search", True),
        documents_count=documents_count,
        created_at=field("created_at", 0),
        updated_at=field("updated_at", field("created_at", 0)),
    )


def _memory_bases(org_id: str, viewer: Viewer) -> list[dict[str, Any]]:
    # The development store's sample collection has no organization: every one sees it.
    return [
        k
        for k in _kb_fallback_store.values()
        if k.get("org_id", org_id) == org_id and visible(k, viewer)
    ]


async def _readable_base(
    session: AsyncSession, org_id: str, kb_id: str, viewer: Viewer, *, to_change: bool
) -> KnowledgeBaseModel:
    """The knowledge base, when the caller may read it (404 otherwise) and, ``to_change``,
    manage it (403)."""
    kb = (
        await session.execute(
            select(KnowledgeBaseModel).where(
                KnowledgeBaseModel.id == kb_id, KnowledgeBaseModel.org_id == org_id
            )
        )
    ).scalar_one_or_none()
    if kb is None or not (visible(kb, viewer) or manageable(kb, viewer)):
        raise HTTPException(status_code=404, detail="Knowledge base not found")
    if to_change and not manageable(kb, viewer):
        raise HTTPException(status_code=403, detail="Forbidden")
    return kb


def _readable_memory_base(
    org_id: str, kb_id: str, viewer: Viewer, *, to_change: bool
) -> dict[str, Any]:
    kb = _kb_fallback_store.get(kb_id)
    if kb is None or kb.get("org_id", org_id) != org_id or not visible(kb, viewer):
        raise HTTPException(status_code=404, detail="Knowledge base not found")
    if to_change and not manageable(kb, viewer):
        raise HTTPException(status_code=403, detail="Forbidden")
    return kb


@router.get("/knowledge/bases", response_model=list[KnowledgeBaseResponse])
async def list_knowledge_bases(request: Request) -> list[KnowledgeBaseResponse]:
    """The knowledge bases the caller may use: the organization's, their teams' and their own."""
    org_id, viewer = await request_viewer(request)
    db_mgr = getattr(request.app.state, "db_manager", None)

    if db_mgr:
        async with db_mgr.session() as session:
            stmt = select(KnowledgeBaseModel).where(
                KnowledgeBaseModel.org_id == org_id, visible_clause(KnowledgeBaseModel, viewer)
            )
            bases = (await session.execute(stmt)).scalars().all()
            result: list[KnowledgeBaseResponse] = []
            for b in bases:
                cnt_stmt = select(KnowledgeDocumentModel).where(
                    KnowledgeDocumentModel.kb_id == b.id
                )
                doc_count = len((await session.execute(cnt_stmt)).scalars().all())
                result.append(_base_response(b, doc_count))
            return result

    return [_base_response(k, len(k.get("documents", {}))) for k in _memory_bases(org_id, viewer)]


@router.post("/knowledge/bases", response_model=KnowledgeBaseResponse)
async def create_knowledge_base(
    req: KnowledgeBaseCreateRequest,
    request: Request,
) -> KnowledgeBaseResponse:
    """Create a pgvector knowledge base owned by the caller, one of their teams or the
    organization."""
    org_id, viewer = await request_viewer(request)
    db_mgr = getattr(request.app.state, "db_manager", None)
    try:
        owner = await owner_for_write(
            db_mgr,
            org_id,
            viewer,
            scope=req.scope,
            team_id=req.team_id,
            current=None,
            what="knowledge bases",
        )
    except OwnershipError as err:
        raise err.as_http() from err
    kb_id = f"kb-{uuid.uuid4().hex[:8]}"
    now = int(time.time())

    if db_mgr:
        async with db_mgr.session() as session:
            kb = KnowledgeBaseModel(
                id=kb_id,
                org_id=org_id,
                name=req.name,
                description=req.description,
                vector_index=req.vector_index,
                embedding_model=req.embedding_model,
                hybrid_search=req.hybrid_search,
                created_at=now,
                updated_at=now,
                **owner_columns(owner),
            )
            session.add(kb)
            await session.commit()
            return _base_response(kb, 0)

    rec: dict[str, Any] = {
        "id": kb_id,
        "org_id": org_id,
        "name": req.name,
        "description": req.description,
        "vector_index": req.vector_index,
        "embedding_model": req.embedding_model,
        "hybrid_search": req.hybrid_search,
        "documents": {},
        "created_at": now,
        "updated_at": now,
        **owner_columns(owner),
    }
    _kb_fallback_store[kb_id] = rec
    if owner.team_id:
        note_memory_owned(org_id, owner.team_id, "knowledge_bases", 1)
    return _base_response(rec, 0)


async def _moved_owner(
    request: Request, org_id: str, viewer: Viewer, req: KnowledgeBaseUpdateRequest, kb: Any
) -> Owner:
    current = owner_of(kb)
    try:
        return await owner_for_write(
            getattr(request.app.state, "db_manager", None),
            org_id,
            viewer,
            scope=req.scope or current.scope,
            team_id=req.team_id if req.scope else current.team_id,
            current=current,
            what="knowledge bases",
        )
    except OwnershipError as err:
        raise err.as_http() from err


@router.patch("/knowledge/bases/{kb_id}", response_model=KnowledgeBaseResponse)
async def update_knowledge_base(
    kb_id: str, req: KnowledgeBaseUpdateRequest, request: Request
) -> KnowledgeBaseResponse:
    """Rename a knowledge base or move it to another owner (whoever manages it, and for a move
    the destination too)."""
    org_id, viewer = await request_viewer(request)
    db_mgr = getattr(request.app.state, "db_manager", None)
    now = int(time.time())

    if db_mgr:
        async with db_mgr.session() as session:
            kb = await _readable_base(session, org_id, kb_id, viewer, to_change=True)
            owner = await _moved_owner(request, org_id, viewer, req, kb)
            if req.name is not None:
                kb.name = req.name
            if req.description is not None:
                kb.description = req.description
            for column, value in owner_columns(owner).items():
                setattr(kb, column, value)
            kb.updated_at = now
            count = len(
                (
                    await session.execute(
                        select(KnowledgeDocumentModel).where(KnowledgeDocumentModel.kb_id == kb_id)
                    )
                )
                .scalars()
                .all()
            )
            await session.commit()
            return _base_response(kb, count)

    record = _readable_memory_base(org_id, kb_id, viewer, to_change=True)
    owner = await _moved_owner(request, org_id, viewer, req, record)
    if record.get("team_id"):
        note_memory_owned(org_id, record["team_id"], "knowledge_bases", -1)
    if owner.team_id:
        note_memory_owned(org_id, owner.team_id, "knowledge_bases", 1)
    if req.name is not None:
        record["name"] = req.name
    if req.description is not None:
        record["description"] = req.description
    record.update(owner_columns(owner), updated_at=now)
    return _base_response(record, len(record.get("documents", {})))


@router.delete("/knowledge/bases/{kb_id}")
async def delete_knowledge_base(kb_id: str, request: Request) -> dict[str, bool]:
    """Delete a Knowledge Base collection and cascade delete documents and chunks."""
    org_id, viewer = await request_viewer(request)
    db_mgr = getattr(request.app.state, "db_manager", None)

    if db_mgr:
        async with db_mgr.session() as session:
            kb = await _readable_base(session, org_id, kb_id, viewer, to_change=True)
            await session.delete(kb)
            await session.commit()
            return {"deleted": True}

    record = _readable_memory_base(org_id, kb_id, viewer, to_change=True)
    if record.get("team_id"):
        note_memory_owned(org_id, record["team_id"], "knowledge_bases", -1)
    del _kb_fallback_store[kb_id]
    return {"deleted": True}


@router.post("/knowledge/bases/{kb_id}/documents", response_model=KnowledgeDocumentResponse)
async def ingest_knowledge_document(
    kb_id: str,
    req: KnowledgeDocumentCreateRequest,
    request: Request,
) -> KnowledgeDocumentResponse:
    """Ingest, chunk, and embed a document into the Knowledge Base (whoever manages it)."""
    org_id, viewer = await request_viewer(request)
    db_mgr = getattr(request.app.state, "db_manager", None)

    if db_mgr:
        async with db_mgr.session() as session:
            await _readable_base(session, org_id, kb_id, viewer, to_change=True)

            try:
                doc = await ingest_document(
                    session=session,
                    kb_id=kb_id,
                    org_id=org_id,
                    title=req.title,
                    content=req.content,
                    source_type=req.source_type,
                    metadata_json=req.metadata,
                    embedding_model=await _embedding_model(db_mgr, org_id),
                )
            except IngestionLimitError as err:
                raise HTTPException(status_code=413, detail=str(err)) from err
            return KnowledgeDocumentResponse(
                id=doc.id,
                kb_id=doc.kb_id,
                title=doc.title,
                source_type=doc.source_type,
                chunk_count=doc.chunk_count,
                created_at=doc.created_at,
            )

    # In-memory fallback
    _readable_memory_base(org_id, kb_id, viewer, to_change=True)
    if len(req.content) > max_document_chars():
        raise HTTPException(
            status_code=413,
            detail=f"Document is {len(req.content):,} characters; the limit is {max_document_chars():,}.",
        )
    doc_id = f"doc-{uuid.uuid4().hex[:8]}"
    now_ts = int(time.time())
    doc_rec = {
        "id": doc_id,
        "title": req.title,
        "content": req.content,
        "source_type": req.source_type,
        "created_at": now_ts,
    }
    _kb_fallback_store[kb_id]["documents"][doc_id] = doc_rec
    return KnowledgeDocumentResponse(
        id=doc_id,
        kb_id=kb_id,
        title=req.title,
        source_type=req.source_type,
        chunk_count=1,
        created_at=now_ts,
    )


class KnowledgeImportRequest(BaseModel):
    """Where the documents come from: ``url`` (a page or a PDF), ``site`` (a page and the pages
    under it), ``pdf`` (``content_base64``) or ``repository`` (its docs folder on GitHub)."""

    kind: Literal["url", "site", "pdf", "repository"]
    url: str = Field(default="", max_length=2000)
    repository: str = Field(default="", max_length=200)
    path: str = Field(default="docs", max_length=300)
    title: str = Field(default="", max_length=200)
    content_base64: str = Field(default="", max_length=45_000_000)
    max_pages: int = Field(default=25, ge=1, le=25)


class KnowledgeImportResponse(BaseModel):
    documents: list[KnowledgeDocumentResponse]


async def _imported(
    req: KnowledgeImportRequest, request: Request, org_id: str, user_id: str
) -> list[ImportedDocument]:
    if req.kind == "url":
        return await import_url(req.url)
    if req.kind == "site":
        return await import_site(req.url, req.max_pages)
    if req.kind == "pdf":
        return [pdf_from_base64(req.content_base64, req.title)]
    engine = getattr(request.app.state, "git_engine", None)
    bundle = await resolve_session_git_credentials(
        user_id=user_id,
        org_id=org_id,
        git_engine=engine,
        db_mgr=getattr(request.app.state, "db_manager", None),
        repo=req.repository,
    )
    return await import_repository_docs(
        req.repository, req.path, bundle.token or bundle.user_token, github_api_url_of(engine)
    )


@router.post("/knowledge/bases/{kb_id}/import", response_model=KnowledgeImportResponse)
async def import_knowledge(
    kb_id: str, req: KnowledgeImportRequest, request: Request
) -> KnowledgeImportResponse:
    """Import documents from a web page, a documentation site, a PDF or a repository's docs."""
    org_id, user_id, _ = _get_request_identity(request)
    _, viewer = await request_viewer(request)
    db_mgr = getattr(request.app.state, "db_manager", None)
    if db_mgr is None:
        raise HTTPException(status_code=503, detail="Imports need the database")
    async with db_mgr.session() as session:
        await _readable_base(session, org_id, kb_id, viewer, to_change=True)
    try:
        documents = await _imported(req, request, org_id, user_id)
    except KnowledgeImportError as err:
        raise HTTPException(status_code=422, detail=str(err)) from err
    model = await _embedding_model(db_mgr, org_id)
    created: list[KnowledgeDocumentResponse] = []
    async with db_mgr.session() as session:
        for document in documents:
            try:
                doc = await ingest_document(
                    session=session,
                    kb_id=kb_id,
                    org_id=org_id,
                    title=document.title,
                    content=document.content,
                    source_type=req.kind,
                    metadata_json={"source": document.source},
                    embedding_model=model,
                )
            except IngestionLimitError as err:
                raise HTTPException(status_code=413, detail=f"{document.title}: {err}") from err
            created.append(
                KnowledgeDocumentResponse(
                    id=doc.id,
                    kb_id=doc.kb_id,
                    title=doc.title,
                    source_type=doc.source_type,
                    chunk_count=doc.chunk_count,
                    created_at=doc.created_at,
                )
            )
    return KnowledgeImportResponse(documents=created)


@router.post("/knowledge/bases/{kb_id}/query", response_model=KnowledgeQueryResponse)
async def query_knowledge_collection(
    kb_id: str,
    req: KnowledgeQueryRequest,
    request: Request,
) -> KnowledgeQueryResponse:
    """Execute cosine similarity search against pgvector chunks."""
    org_id, viewer = await request_viewer(request)
    db_mgr = getattr(request.app.state, "db_manager", None)

    if db_mgr:
        async with db_mgr.session() as session:
            await _readable_base(session, org_id, kb_id, viewer, to_change=False)
            chunks = await query_knowledge_base(
                session=session,
                kb_id=kb_id,
                org_id=org_id,
                query_text=req.query,
                limit=req.limit,
                embedding_model=await _embedding_model(db_mgr, org_id),
            )
            return KnowledgeQueryResponse(
                query=req.query,
                results=[
                    KnowledgeQueryResultItem(
                        id=c["id"],
                        score=c["score"],
                        title=c["title"],
                        content=c["content"],
                        chunk_index=c["chunk_index"],
                    )
                    for c in chunks
                ],
            )

    # In-memory fallback search
    _readable_memory_base(org_id, kb_id, viewer, to_change=False)
    results: list[KnowledgeQueryResultItem] = []
    for d in _kb_fallback_store[kb_id]["documents"].values():
        results.append(
            KnowledgeQueryResultItem(
                id=d["id"],
                score=0.92,
                title=d["title"],
                content=d["content"][:300],
                chunk_index=0,
            )
        )
    return KnowledgeQueryResponse(query=req.query, results=results[: req.limit])

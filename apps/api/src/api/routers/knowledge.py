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
from sqlalchemy import or_, select

from api.auth.access import _get_request_identity
from api.auth.roles import is_admin as holds_admin
from api.db.models import (
    KnowledgeBaseModel,
    KnowledgeDocumentModel,
)
from api.models import (
    KnowledgeBaseCreateRequest,
    KnowledgeBaseResponse,
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
from api.services.settings import effective_settings

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


@router.get("/knowledge/bases", response_model=list[KnowledgeBaseResponse])
async def list_knowledge_bases(request: Request) -> list[KnowledgeBaseResponse]:
    """List all Knowledge Bases accessible to the organization and current user."""
    org_id, user_id, _ = _get_request_identity(request)
    db_mgr = getattr(request.app.state, "db_manager", None)

    if db_mgr:
        async with db_mgr.session() as session:
            stmt = select(KnowledgeBaseModel).where(
                KnowledgeBaseModel.org_id == org_id,
                or_(KnowledgeBaseModel.scope == "org", KnowledgeBaseModel.user_id == user_id),
            )
            res = await session.execute(stmt)
            bases = res.scalars().all()
            result: list[KnowledgeBaseResponse] = []
            for b in bases:
                cnt_stmt = select(KnowledgeDocumentModel).where(
                    KnowledgeDocumentModel.kb_id == b.id
                )
                cnt_res = await session.execute(cnt_stmt)
                doc_count = len(cnt_res.scalars().all())
                result.append(
                    KnowledgeBaseResponse(
                        id=b.id,
                        name=b.name,
                        description=b.description,
                        scope=b.scope,
                        user_id=b.user_id,
                        vector_index=b.vector_index,
                        embedding_model=b.embedding_model,
                        hybrid_search=b.hybrid_search,
                        documents_count=doc_count,
                        created_at=b.created_at,
                        updated_at=b.updated_at,
                    )
                )
            return result

    # In-memory store
    return [
        KnowledgeBaseResponse(
            id=k["id"],
            name=k["name"],
            description=k["description"],
            scope=k.get("scope", "org"),
            user_id=k.get("user_id"),
            vector_index=k["vector_index"],
            embedding_model=k["embedding_model"],
            hybrid_search=k["hybrid_search"],
            documents_count=len(k.get("documents", {})),
            created_at=k["created_at"],
            updated_at=k["updated_at"],
        )
        for k in _kb_fallback_store.values()
    ]


@router.post("/knowledge/bases", response_model=KnowledgeBaseResponse)
async def create_knowledge_base(
    req: KnowledgeBaseCreateRequest,
    request: Request,
) -> KnowledgeBaseResponse:
    """Create a new pgvector semantic knowledge base collection."""
    org_id, user_id, roles = _get_request_identity(request)
    is_admin = holds_admin(roles)

    if req.scope == "org" and not is_admin:
        raise HTTPException(
            status_code=403,
            detail="Only Organization Administrators can create organization-wide knowledge bases.",
        )

    target_user_id = user_id if req.scope == "user" else None
    db_mgr = getattr(request.app.state, "db_manager", None)
    kb_id = f"kb-{uuid.uuid4().hex[:8]}"
    now = int(time.time())

    if db_mgr:
        async with db_mgr.session() as session:
            kb = KnowledgeBaseModel(
                id=kb_id,
                org_id=org_id,
                user_id=target_user_id,
                scope=req.scope,
                name=req.name,
                description=req.description,
                vector_index=req.vector_index,
                embedding_model=req.embedding_model,
                hybrid_search=req.hybrid_search,
                created_at=now,
                updated_at=now,
            )
            session.add(kb)
            await session.commit()
            return KnowledgeBaseResponse(
                id=kb.id,
                name=kb.name,
                description=kb.description,
                scope=kb.scope,
                user_id=kb.user_id,
                vector_index=kb.vector_index,
                embedding_model=kb.embedding_model,
                hybrid_search=kb.hybrid_search,
                documents_count=0,
                created_at=kb.created_at,
                updated_at=kb.updated_at,
            )

    rec: dict[str, Any] = {
        "id": kb_id,
        "name": req.name,
        "description": req.description,
        "scope": req.scope,
        "user_id": target_user_id,
        "vector_index": req.vector_index,
        "embedding_model": req.embedding_model,
        "hybrid_search": req.hybrid_search,
        "documents": {},
        "created_at": now,
        "updated_at": now,
    }
    _kb_fallback_store[kb_id] = rec
    return KnowledgeBaseResponse(
        id=kb_id,
        name=req.name,
        description=req.description,
        scope=req.scope,
        user_id=target_user_id,
        vector_index=req.vector_index,
        embedding_model=req.embedding_model,
        hybrid_search=req.hybrid_search,
        documents_count=0,
        created_at=now,
        updated_at=now,
    )


@router.delete("/knowledge/bases/{kb_id}")
async def delete_knowledge_base(kb_id: str, request: Request) -> dict[str, bool]:
    """Delete a Knowledge Base collection and cascade delete documents and chunks."""
    org_id, user_id, roles = _get_request_identity(request)
    is_admin = holds_admin(roles)
    db_mgr = getattr(request.app.state, "db_manager", None)

    if db_mgr:
        async with db_mgr.session() as session:
            stmt = select(KnowledgeBaseModel).where(
                KnowledgeBaseModel.id == kb_id, KnowledgeBaseModel.org_id == org_id
            )
            res = await session.execute(stmt)
            kb = res.scalar_one_or_none()
            if not kb:
                raise HTTPException(status_code=404, detail="Knowledge base not found")
            if kb.scope == "org" and not is_admin:
                raise HTTPException(status_code=403, detail="Forbidden")
            if kb.scope == "user" and kb.user_id != user_id and not is_admin:
                raise HTTPException(status_code=403, detail="Forbidden")

            await session.delete(kb)
            await session.commit()
            return {"deleted": True}

    if kb_id in _kb_fallback_store:
        del _kb_fallback_store[kb_id]
        return {"deleted": True}
    raise HTTPException(status_code=404, detail="Knowledge base not found")


@router.post("/knowledge/bases/{kb_id}/documents", response_model=KnowledgeDocumentResponse)
async def ingest_knowledge_document(
    kb_id: str,
    req: KnowledgeDocumentCreateRequest,
    request: Request,
) -> KnowledgeDocumentResponse:
    """Ingest, chunk, and embed a document into the Knowledge Base."""
    org_id, _, _ = _get_request_identity(request)
    db_mgr = getattr(request.app.state, "db_manager", None)

    if db_mgr:
        async with db_mgr.session() as session:
            kb_stmt = select(KnowledgeBaseModel).where(
                KnowledgeBaseModel.id == kb_id, KnowledgeBaseModel.org_id == org_id
            )
            kb_res = await session.execute(kb_stmt)
            if not kb_res.scalar_one_or_none():
                raise HTTPException(status_code=404, detail="Knowledge base not found")

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
    if kb_id not in _kb_fallback_store:
        raise HTTPException(status_code=404, detail="Knowledge base not found")
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
    db_mgr = getattr(request.app.state, "db_manager", None)
    if db_mgr is None:
        raise HTTPException(status_code=503, detail="Imports need the database")
    try:
        documents = await _imported(req, request, org_id, user_id)
    except KnowledgeImportError as err:
        raise HTTPException(status_code=422, detail=str(err)) from err
    model = await _embedding_model(db_mgr, org_id)
    created: list[KnowledgeDocumentResponse] = []
    async with db_mgr.session() as session:
        kb = await session.execute(
            select(KnowledgeBaseModel).where(
                KnowledgeBaseModel.id == kb_id, KnowledgeBaseModel.org_id == org_id
            )
        )
        if not kb.scalar_one_or_none():
            raise HTTPException(status_code=404, detail="Knowledge base not found")
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
    org_id, _, _ = _get_request_identity(request)
    db_mgr = getattr(request.app.state, "db_manager", None)

    if db_mgr:
        async with db_mgr.session() as session:
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
    if kb_id not in _kb_fallback_store:
        raise HTTPException(status_code=404, detail="Knowledge base not found")
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

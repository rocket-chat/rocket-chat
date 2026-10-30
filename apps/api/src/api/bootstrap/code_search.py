"""Session-scoped semantic code search bound to the tool registry."""

import os
from typing import Any

from agent_core.context import current_execution_ctx
from agent_core.tools.registry import ToolRegistry

from api.services.code_index import (
    DEFAULT_CACHE_MAX_BYTES,
    DEFAULT_CACHE_MAX_SESSIONS,
    DEFAULT_CACHE_TTL_SECONDS,
    DEFAULT_MAX_CHUNKS,
    DEFAULT_MAX_FILES,
    CodeEmbeddings,
    CodeIndexCache,
    build_code_index,
    format_results,
    search_code_index,
)
from api.services.memory import configured_embedding_model
from api.services.settings import effective_settings
from specifications.interfaces.sandbox import SandboxDriverProtocol

MAX_RESULTS = 20


def _int_setting(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    return int(raw) if raw.isdigit() else default


def _code_index_budgets() -> tuple[int, int, float, int, int]:
    """Read the code-index budgets from the environment (tunable per deployment)."""
    return (
        _int_setting("ROCKET_CODE_INDEX_MAX_SESSIONS", DEFAULT_CACHE_MAX_SESSIONS),
        _int_setting("ROCKET_CODE_INDEX_MAX_BYTES", DEFAULT_CACHE_MAX_BYTES),
        float(_int_setting("ROCKET_CODE_INDEX_TTL_SECONDS", int(DEFAULT_CACHE_TTL_SECONDS))),
        _int_setting("ROCKET_CODE_INDEX_MAX_FILES", DEFAULT_MAX_FILES),
        _int_setting("ROCKET_CODE_INDEX_MAX_CHUNKS", DEFAULT_MAX_CHUNKS),
    )


def register_code_search(
    registry: ToolRegistry, driver: SandboxDriverProtocol | None, db_mgr: Any = None
) -> CodeIndexCache:
    """Bind a session-scoped semantic code-search callback to the tool registry.

    The index is built lazily on first use and cached per session in a bounded LRU +
    byte budget (``CodeIndexCache``), so long sessions do not re-embed the workspace on
    every query while a busy process cannot grow without limit. The cache is returned
    so the application can evict an entry when its session is deleted or archived.
    """
    max_sessions, max_bytes, ttl_seconds, max_files, max_chunks = _code_index_budgets()
    cache = CodeIndexCache(max_sessions=max_sessions, max_bytes=max_bytes, ttl_seconds=ttl_seconds)
    embeddings = CodeEmbeddings(db_mgr)

    async def _code_search(query: str, limit: int) -> str:
        ctx = current_execution_ctx.get()
        session_id = ctx.session_id if ctx else None
        if not session_id or driver is None:
            return ""
        index = cache.get(session_id)
        if index is None:
            org_id = ctx.tenant_org_id if ctx else "default_org"
            memory_settings = await effective_settings(db_mgr, org_id, "memory")
            index = await build_code_index(
                driver,
                session_id,
                max_files=max_files,
                max_chunks=max_chunks,
                max_bytes=max_bytes,
                embeddings=embeddings,
                org_id=org_id,
                model=configured_embedding_model(memory_settings),
            )
            cache.put(session_id, index)
        results = await search_code_index(index, query, limit=min(limit, MAX_RESULTS))
        return format_results(results)

    registry.set_code_search_callback(_code_search)
    return cache

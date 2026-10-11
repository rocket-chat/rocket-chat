"""Long-term-memory tools: ``remember`` and ``recall``.

Agent-core cannot reach PostgreSQL, so the tool bodies are thin: they validate the call
shape and delegate to the tenant-scoped callbacks the API layer binds (mirroring
``kb_search``). This keeps the database/RLS concerns in ``apps/api`` while the tool schema
and registry wiring live with the other Tier-1 tools.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

RememberCallback = Callable[[str, str, str], Awaitable[str]]
RecallCallback = Callable[[str, str | None, int], Awaitable[str]]

DEFAULT_RECALL_LIMIT = 5
_UNAVAILABLE = (
    "Long-term memory is not available in this deployment; no memory was written or read."
)


async def remember(
    callback: RememberCallback | None,
    content: str,
    kind: str = "fact",
    scope: str = "user",
) -> str:
    """Delegate one deliberate memory write to the tenant-scoped service."""
    if callback is None:
        return _UNAVAILABLE
    return await callback(content, kind, scope)


async def recall(
    callback: RecallCallback | None,
    query: str,
    scope: str | None = None,
    limit: int = DEFAULT_RECALL_LIMIT,
) -> str:
    """Delegate one memory search to the tenant-scoped service."""
    if callback is None:
        return _UNAVAILABLE
    return await callback(query, scope, limit)

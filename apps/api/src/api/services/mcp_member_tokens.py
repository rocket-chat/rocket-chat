"""Per-member OAuth tokens for organization MCP servers.

The shared server row holds the connector's configuration (endpoints, client id); in
``oauth_mode == "member"`` it holds no tokens. Each member's tokens are kept here, encrypted by
the caller before they arrive, under row-level security keyed on the organization and read only
for the member whose turn needs them.
"""

from __future__ import annotations

import time
from typing import Any

from sqlalchemy import delete, select

from api.db.models import McpMemberTokenModel

OAUTH_MODE_ORG = "org"
OAUTH_MODE_MEMBER = "member"


def is_member_mode(auth: dict[str, Any] | None) -> bool:
    return bool(auth) and (auth or {}).get("oauth_mode") == OAUTH_MODE_MEMBER


# Without a database (development) tokens live in this process, shared by every store instance
# exactly like the other process-local development stores.
_MEMORY: dict[tuple[str, str, str], dict[str, Any]] = {}


class McpMemberTokenStore:
    """Tokens in PostgreSQL (tenant-scoped), or in this process without a database."""

    def __init__(self, db_manager: Any = None) -> None:
        self._db = (
            db_manager
            if db_manager is not None and getattr(db_manager, "session_factory", None)
            else None
        )
        self._memory = _MEMORY

    async def get(self, org_id: str, server_id: str, user_id: str) -> dict[str, Any] | None:
        if self._db is None:
            found = self._memory.get((org_id, server_id, user_id))
            return dict(found) if found else None
        async with self._db.session(tenant_org_id=org_id) as session:
            row = (
                await session.execute(
                    select(McpMemberTokenModel).where(
                        McpMemberTokenModel.server_id == server_id,
                        McpMemberTokenModel.user_id == user_id,
                    )
                )
            ).scalar_one_or_none()
            return dict(row.tokens) if row is not None else None

    async def connected_servers(self, org_id: str, user_id: str) -> set[str]:
        """Ids of the servers this member has connected an account to."""
        if self._db is None:
            return {s for (o, s, u) in self._memory if o == org_id and u == user_id}
        async with self._db.session(tenant_org_id=org_id) as session:
            rows = await session.execute(
                select(McpMemberTokenModel.server_id).where(McpMemberTokenModel.user_id == user_id)
            )
            return {str(r) for r in rows.scalars()}

    async def put(self, org_id: str, server_id: str, user_id: str, tokens: dict[str, Any]) -> None:
        if self._db is None:
            self._memory[(org_id, server_id, user_id)] = dict(tokens)
            return
        async with self._db.session(tenant_org_id=org_id) as session:
            row = (
                await session.execute(
                    select(McpMemberTokenModel).where(
                        McpMemberTokenModel.server_id == server_id,
                        McpMemberTokenModel.user_id == user_id,
                    )
                )
            ).scalar_one_or_none()
            if row is None:
                session.add(
                    McpMemberTokenModel(
                        server_id=server_id,
                        user_id=user_id,
                        org_id=org_id,
                        tokens=dict(tokens),
                        updated_at=int(time.time()),
                    )
                )
            else:
                row.tokens = dict(tokens)
                row.updated_at = int(time.time())

    async def delete(self, org_id: str, server_id: str, user_id: str) -> bool:
        if self._db is None:
            return self._memory.pop((org_id, server_id, user_id), None) is not None
        async with self._db.session(tenant_org_id=org_id) as session:
            result = await session.execute(
                delete(McpMemberTokenModel).where(
                    McpMemberTokenModel.server_id == server_id,
                    McpMemberTokenModel.user_id == user_id,
                )
            )
            return bool(getattr(result, "rowcount", 0))

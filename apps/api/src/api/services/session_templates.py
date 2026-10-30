"""Session templates / playbooks."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select

from api.db.models import SessionTemplateModel
from api.db.session import DatabaseSessionManager
from api.services.repository_hint import with_repository_hint

MAX_TEMPLATES_PER_ORG = 200


@dataclass
class SessionTemplate:
    id: str
    org_id: str
    user_id: str | None  # None: organization-wide
    name: str
    prompt: str
    description: str = ""
    agent_id: str | None = None
    git_repo: str | None = None
    git_branch: str | None = None
    permission_mode: str | None = None
    created_at: int = 0


class TemplateLimitError(ValueError):
    pass


class TemplateStore:
    """Templates in PostgreSQL (tenant-scoped); a process-local dict without a database."""

    def __init__(self, db_manager: DatabaseSessionManager | None) -> None:
        self._db = (
            db_manager
            if db_manager is not None and getattr(db_manager, "session_factory", None)
            else None
        )
        self._memory: dict[str, SessionTemplate] = {}

    async def _all(self, org_id: str) -> list[SessionTemplate]:
        if self._db is None:
            return [t for t in self._memory.values() if t.org_id == org_id]
        async with self._db.session(tenant_org_id=org_id) as session:
            stmt = select(SessionTemplateModel).where(SessionTemplateModel.org_id == org_id)
            return [
                SessionTemplate(
                    **{c.name: getattr(r, c.name) for c in SessionTemplateModel.__table__.columns}
                )
                for r in (await session.execute(stmt)).scalars()
            ]

    async def all(self, org_id: str) -> list[SessionTemplate]:
        """Every template of the organization, personal ones included (platform use only)."""
        return await self._all(org_id)

    async def visible_to(self, org_id: str, user_id: str) -> list[SessionTemplate]:
        """Organization-wide templates plus the caller's own, never another member's."""
        found = [t for t in await self._all(org_id) if t.user_id in (None, user_id)]
        return sorted(found, key=lambda t: (t.user_id is not None, t.name.lower()))

    async def get(self, org_id: str, user_id: str, template_id: str) -> SessionTemplate | None:
        return next(
            (t for t in await self.visible_to(org_id, user_id) if t.id == template_id), None
        )

    async def create(self, template: SessionTemplate) -> SessionTemplate:
        if len(await self._all(template.org_id)) >= MAX_TEMPLATES_PER_ORG:
            raise TemplateLimitError("This organization has reached its template limit.")
        template.id = f"tpl_{uuid.uuid4().hex[:10]}"
        template.created_at = int(time.time())
        if self._db is None:
            self._memory[template.id] = template
            return template
        async with self._db.session(tenant_org_id=template.org_id) as session:
            session.add(SessionTemplateModel(**vars(template)))
        return template

    async def delete(self, org_id: str, template_id: str) -> bool:
        if self._db is None:
            return self._memory.pop(template_id, None) is not None
        async with self._db.session(tenant_org_id=org_id) as session:
            row = await session.get(SessionTemplateModel, template_id)
            if row is None:
                return False
            await session.delete(row)
        return True


def template_store(state: Any) -> TemplateStore:
    """The application's template store (``app.state``), made on first use."""
    store = getattr(state, "template_store", None)
    if not isinstance(store, TemplateStore):
        store = TemplateStore(getattr(state, "db_manager", None))
        state.template_store = store
    return store


def as_session_request(template: SessionTemplate) -> dict[str, Any]:
    """The ``POST /v1/sessions`` fields this template stands for."""
    fields: dict[str, Any] = {
        "title": template.name,
        "initial_prompt": with_repository_hint(
            template.prompt, template.git_repo, template.git_branch
        ),
        "agent_id": template.agent_id,
        "permission_mode": template.permission_mode,
    }
    return {k: v for k, v in fields.items() if v is not None}

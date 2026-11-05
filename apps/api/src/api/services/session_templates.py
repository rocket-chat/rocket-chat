"""Session templates / playbooks."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select

from api.db.models import SessionTemplateModel
from api.db.session import DatabaseSessionManager
from api.services.ownership import visible
from api.services.repository_hint import with_repository_hint
from api.services.teams import note_memory_owned
from specifications.interfaces.ownership import Viewer

MAX_TEMPLATES_PER_ORG = 200


@dataclass
class SessionTemplate:
    id: str
    org_id: str
    user_id: str | None  # the owner of a personal template
    name: str
    prompt: str
    description: str = ""
    agent_id: str | None = None
    git_repo: str | None = None
    git_branch: str | None = None
    permission_mode: str | None = None
    created_at: int = 0
    # Owner: ``user`` (``user_id``), ``team`` (``team_id``) or ``org``.
    scope: str = "user"
    team_id: str | None = None


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

    async def visible_to(self, org_id: str, viewer: Viewer) -> list[SessionTemplate]:
        """The organization's templates, the caller's teams' and their own, never another
        member's."""
        found = [t for t in await self._all(org_id) if visible(t, viewer)]
        order = {"org": 0, "team": 1, "user": 2}
        return sorted(found, key=lambda t: (order.get(t.scope, 2), t.name.lower()))

    async def get(self, org_id: str, viewer: Viewer, template_id: str) -> SessionTemplate | None:
        return next((t for t in await self.visible_to(org_id, viewer) if t.id == template_id), None)

    async def create(self, template: SessionTemplate) -> SessionTemplate:
        if len(await self._all(template.org_id)) >= MAX_TEMPLATES_PER_ORG:
            raise TemplateLimitError("This organization has reached its template limit.")
        template.id = f"tpl_{uuid.uuid4().hex[:10]}"
        template.created_at = int(time.time())
        if self._db is None:
            self._memory[template.id] = template
            if template.team_id:
                note_memory_owned(template.org_id, template.team_id, "templates", 1)
            return template
        async with self._db.session(tenant_org_id=template.org_id) as session:
            session.add(SessionTemplateModel(**vars(template)))
        return template

    async def save(self, template: SessionTemplate, before: SessionTemplate) -> SessionTemplate:
        """Replace a stored template (its owner may change: a move)."""
        if self._db is None:
            if before.team_id:
                note_memory_owned(template.org_id, before.team_id, "templates", -1)
            if template.team_id:
                note_memory_owned(template.org_id, template.team_id, "templates", 1)
            self._memory[template.id] = template
            return template
        async with self._db.session(tenant_org_id=template.org_id) as session:
            row = await session.get(SessionTemplateModel, template.id)
            if row is not None and row.org_id == template.org_id:
                for name, value in vars(template).items():
                    if name not in ("id", "org_id", "created_at"):
                        setattr(row, name, value)
        return template

    async def delete(self, org_id: str, template_id: str) -> bool:
        if self._db is None:
            removed = self._memory.pop(template_id, None)
            if removed is not None and removed.team_id:
                note_memory_owned(org_id, removed.team_id, "templates", -1)
            return removed is not None
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

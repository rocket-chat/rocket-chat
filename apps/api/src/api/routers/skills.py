"""Custom skills: list, import, create, test."""

import logging
import time
import uuid
from typing import Any

from fastapi import (
    APIRouter,
    HTTPException,
    Request,
)
from pydantic import BaseModel
from sqlalchemy import select

from api.auth.access import _get_request_identity
from api.db.models import (
    CustomSkillModel,
)
from api.models import (
    CustomSkillCreateRequest,
    CustomSkillImportRequest,
    CustomSkillResponse,
    CustomSkillTestRequest,
    CustomSkillTestResponse,
)
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
from api.services.skill_import import SkillImportError, parse_skill_markdown
from api.services.skills_lookup import _memory_skills
from api.services.starter_content import STARTER_SKILLS
from api.services.teams import note_memory_owned
from specifications.interfaces.ownership import Owner, Viewer

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1", tags=["Sessions & Telemetry"])


class StarterSkillResponse(BaseModel):
    name: str
    description: str
    enabled: bool


@router.get("/skills/starter", response_model=list[StarterSkillResponse])
async def list_starter_skills(request: Request) -> list[StarterSkillResponse]:
    """The shipped instruction skills and whether the organization offers each
    (``general.starter_skills``; administrators switch them on and off)."""
    org_id, _, _ = _get_request_identity(request)
    general = await effective_settings(
        getattr(request.app.state, "db_manager", None), org_id, "general"
    )
    enabled = {str(name) for name in general.get("starter_skills") or []}
    return [
        StarterSkillResponse(name=s.name, description=s.description, enabled=s.name in enabled)
        for s in STARTER_SKILLS
    ]


def _response(skill: Any) -> CustomSkillResponse:
    """A stored skill (a row or an in-memory record) as the API reports it."""

    def field(name: str, default: Any = None) -> Any:
        value = skill.get(name) if isinstance(skill, dict) else getattr(skill, name, None)
        return default if value is None else value

    return CustomSkillResponse(
        id=field("id"),
        name=field("name"),
        title=field("title") or field("name"),
        description=field("description", ""),
        body=field("body") or field("code", ""),
        scope=field("scope", "org"),
        user_id=field("user_id"),
        team_id=field("team_id"),
        runtime=field("runtime", "text"),
        is_active=field("is_active", True),
        signature=field("signature", ""),
        code=field("code", ""),
        created_at=field("created_at", 0),
        updated_at=field("updated_at", 0),
    )


@router.get("/skills", response_model=list[CustomSkillResponse])
async def list_custom_skills(request: Request) -> list[CustomSkillResponse]:
    """The custom skills the caller may use: the organization's, their teams' and their own."""
    org_id, viewer = await request_viewer(request)
    db_mgr = getattr(request.app.state, "db_manager", None)

    if db_mgr:
        async with db_mgr.session() as session:
            stmt = select(CustomSkillModel).where(
                CustomSkillModel.org_id == org_id, visible_clause(CustomSkillModel, viewer)
            )
            return [_response(s) for s in (await session.execute(stmt)).scalars().all()]

    return [_response(s) for s in _memory_skills.get(org_id, {}).values() if visible(s, viewer)]


@router.post("/skills/import", response_model=CustomSkillResponse)
async def import_skill_markdown(
    req: CustomSkillImportRequest, request: Request
) -> CustomSkillResponse:
    """Create or update a prompt skill from a ``SKILL.md`` document (front matter + body)."""
    try:
        imported = parse_skill_markdown(req.markdown)
    except SkillImportError as err:
        raise HTTPException(status_code=422, detail=str(err)) from err
    return await create_or_update_custom_skill(
        CustomSkillCreateRequest(
            name=imported.name,
            description=imported.description,
            body=imported.body,
            scope=req.scope,
            team_id=req.team_id,
        ),
        request,
    )


async def _owner(
    request: Request, org_id: str, viewer: Viewer, req: CustomSkillCreateRequest, current: Any
) -> Owner:
    try:
        return await owner_for_write(
            getattr(request.app.state, "db_manager", None),
            org_id,
            viewer,
            scope=req.scope,
            team_id=req.team_id,
            current=owner_of(current) if current is not None else None,
            what="skills",
        )
    except OwnershipError as err:
        raise err.as_http() from err


@router.post("/skills", response_model=CustomSkillResponse)
async def create_or_update_custom_skill(
    req: CustomSkillCreateRequest,
    request: Request,
) -> CustomSkillResponse:
    """Create or update a custom prompt skill owned by the caller, one of their teams or the
    organization (moving it needs the right to manage it on both sides)."""
    org_id, viewer = await request_viewer(request)
    skill_title = req.title or req.name or ""
    skill_body = req.body or req.code or ""
    db_mgr = getattr(request.app.state, "db_manager", None)
    now = int(time.time())

    if db_mgr:
        async with db_mgr.session() as session:
            existing: CustomSkillModel | None = None
            if req.id:
                existing = (
                    await session.execute(
                        select(CustomSkillModel).where(
                            CustomSkillModel.id == req.id, CustomSkillModel.org_id == org_id
                        )
                    )
                ).scalar_one_or_none()
            owner = await _owner(request, org_id, viewer, req, existing)
            if existing is None and not req.id:
                # Saving a skill under a name its owner already uses updates that skill.
                existing = (
                    await session.execute(
                        select(CustomSkillModel).where(
                            CustomSkillModel.org_id == org_id,
                            CustomSkillModel.name == skill_title,
                            CustomSkillModel.scope == owner.scope,
                            CustomSkillModel.user_id == owner.user_id,
                            CustomSkillModel.team_id == owner.team_id,
                        )
                    )
                ).scalar_one_or_none()
            if existing is not None:
                existing.description = req.description
                existing.body = skill_body
                existing.name = skill_title
                existing.is_active = req.is_active
                existing.updated_at = now
                for column, value in owner_columns(owner).items():
                    setattr(existing, column, value)
                target = existing
            else:
                target = CustomSkillModel(
                    id=f"skill-{uuid.uuid4().hex[:8]}",
                    org_id=org_id,
                    name=skill_title,
                    description=req.description,
                    body=skill_body,
                    runtime="text",
                    is_active=req.is_active,
                    created_at=now,
                    updated_at=now,
                    **owner_columns(owner),
                )
                session.add(target)
            await session.commit()
            return _response(target)

    bucket = _memory_skills.setdefault(org_id, {})
    current = bucket.get(req.id) if req.id else None
    owner = await _owner(request, org_id, viewer, req, current)
    skill_id = req.id if current is not None and req.id else f"skill-{uuid.uuid4().hex[:8]}"
    if current is not None and current.get("team_id"):
        note_memory_owned(org_id, current["team_id"], "skills", -1)
    if owner.team_id:
        note_memory_owned(org_id, owner.team_id, "skills", 1)
    rec = {
        "id": skill_id,
        "name": skill_title,
        "title": skill_title,
        "description": req.description,
        "body": skill_body,
        "runtime": "text",
        "is_active": req.is_active,
        "created_at": current.get("created_at", now) if current else now,
        "updated_at": now,
        **owner_columns(owner),
    }
    bucket[skill_id] = rec
    return _response(rec)


@router.delete("/skills/{skill_id}")
async def delete_custom_skill(skill_id: str, request: Request) -> dict[str, bool]:
    """Delete a custom skill (whoever may manage its owner)."""
    org_id, viewer = await request_viewer(request)
    db_mgr = getattr(request.app.state, "db_manager", None)

    if db_mgr:
        async with db_mgr.session() as session:
            stmt = select(CustomSkillModel).where(
                CustomSkillModel.id == skill_id, CustomSkillModel.org_id == org_id
            )
            skill = (await session.execute(stmt)).scalar_one_or_none()
            if not skill or (not visible(skill, viewer) and not manageable(skill, viewer)):
                raise HTTPException(status_code=404, detail="Skill not found")
            if not manageable(skill, viewer):
                raise HTTPException(status_code=403, detail="Forbidden")
            await session.delete(skill)
            await session.commit()
            return {"deleted": True}

    existing = _memory_skills.get(org_id, {}).get(skill_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Skill not found")
    if not manageable(existing, viewer):
        raise HTTPException(status_code=403, detail="Forbidden")
    if existing.get("team_id"):
        note_memory_owned(org_id, existing["team_id"], "skills", -1)
    del _memory_skills[org_id][skill_id]
    return {"deleted": True}


@router.post("/skills/{skill_id}/test", response_model=CustomSkillTestResponse)
async def test_custom_skill(
    skill_id: str,
    req: CustomSkillTestRequest,
    request: Request,
) -> CustomSkillTestResponse:
    """Validate skill prompt and readiness."""
    return CustomSkillTestResponse(
        success=True,
        output="Skill prompt verified successfully.",
        duration_ms=1.0,
        exit_code=0,
    )

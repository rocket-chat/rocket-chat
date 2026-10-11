"""Session templates / playbooks API.

Organization-wide templates are administrator-managed; members keep personal ones. Starting a
template is exactly ``POST /v1/sessions`` with the template's fields, so every check that
applies to a hand-made session (repository validation, permission mode, read-only roles)
applies here too.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from api.auth.access import _get_request_identity
from api.models import CreateSessionRequest, SessionResponse
from api.routers.session_crud import create_session
from api.services.repository_validation import (
    InvalidRepositoryError,
    parse_repository,
    validate_branch,
)
from api.services.session_templates import (
    SessionTemplate,
    TemplateLimitError,
    TemplateStore,
    as_session_request,
)
from specifications.interfaces.agent import PERMISSION_MODES

router = APIRouter(prefix="/v1", tags=["Templates"])


class TemplateBody(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    description: str = Field(default="", max_length=500)
    prompt: str = Field(..., min_length=1, max_length=20_000)
    scope: str = Field(default="user", pattern="^(user|org)$")
    agent_id: str | None = None
    git_repo: str | None = None
    git_branch: str | None = None
    permission_mode: str | None = None


class TemplateResponse(BaseModel):
    id: str
    name: str
    description: str
    prompt: str
    scope: str
    agent_id: str | None
    git_repo: str | None
    git_branch: str | None
    permission_mode: str | None


def _store(request: Request) -> TemplateStore:
    store = getattr(request.app.state, "template_store", None)
    if store is None:
        store = TemplateStore(getattr(request.app.state, "db_manager", None))
        request.app.state.template_store = store
    return store


def _view(template: SessionTemplate) -> TemplateResponse:
    data = {k: v for k, v in vars(template).items() if k in TemplateResponse.model_fields}
    return TemplateResponse(scope="org" if template.user_id is None else "user", **data)


@router.get("/templates", response_model=list[TemplateResponse])
async def list_templates(request: Request) -> list[TemplateResponse]:
    org_id, user_id, _ = _get_request_identity(request)
    return [_view(t) for t in await _store(request).visible_to(org_id, user_id)]


@router.post("/templates", response_model=TemplateResponse)
async def create_template(body: TemplateBody, request: Request) -> TemplateResponse:
    org_id, user_id, roles = _get_request_identity(request)
    is_admin = any(role in ("admin", "owner") for role in roles)
    if "readonly" in roles:
        raise HTTPException(
            status_code=403, detail="Forbidden: Read-only role cannot create templates"
        )
    if body.scope == "org" and not is_admin:
        raise HTTPException(
            status_code=403, detail="Only administrators can create organization-wide templates."
        )
    if body.permission_mode is not None and body.permission_mode not in PERMISSION_MODES:
        raise HTTPException(
            status_code=422, detail=f"permission_mode must be one of: {', '.join(PERMISSION_MODES)}"
        )
    try:
        repo = parse_repository(body.git_repo).url if body.git_repo else None
        branch = validate_branch(body.git_branch) if body.git_branch else None
        template = await _store(request).create(
            SessionTemplate(
                id="",
                org_id=org_id,
                user_id=None if body.scope == "org" else user_id,
                name=body.name,
                description=body.description,
                prompt=body.prompt,
                agent_id=body.agent_id,
                git_repo=repo,
                git_branch=branch,
                permission_mode=body.permission_mode,
            )
        )
    except (InvalidRepositoryError, TemplateLimitError) as err:
        raise HTTPException(status_code=422, detail=str(err)) from err
    return _view(template)


@router.delete("/templates/{template_id}")
async def delete_template(template_id: str, request: Request) -> dict[str, str]:
    org_id, user_id, roles = _get_request_identity(request)
    store = _store(request)
    template = await store.get(org_id, user_id, template_id)
    if template is None:
        raise HTTPException(status_code=404, detail="Template not found")
    if template.user_id is None and not any(role in ("admin", "owner") for role in roles):
        raise HTTPException(
            status_code=403, detail="Only administrators can delete organization-wide templates."
        )
    await store.delete(org_id, template_id)
    return {"status": "deleted"}


@router.post("/templates/{template_id}/start", response_model=SessionResponse)
async def start_from_template(template_id: str, request: Request) -> SessionResponse:
    """Start a session from the template (the opening prompt runs immediately)."""
    org_id, user_id, _ = _get_request_identity(request)
    template = await _store(request).get(org_id, user_id, template_id)
    if template is None:
        raise HTTPException(status_code=404, detail="Template not found")
    return await create_session(CreateSessionRequest(**as_session_request(template)), request)

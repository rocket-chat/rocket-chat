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
from api.auth.roles import can_write
from api.auth.roles import is_admin as holds_admin
from api.models import CreateSessionRequest, SessionResponse
from api.routers.session_crud import create_session
from api.services.agent_access import (
    TEMPLATE_BLOCKED_REASON,
    AgentAccessDenied,
    blocked_for,
    require_usable_agent,
)
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
    template_store,
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
    blocked: bool = Field(
        default=False,
        description=(
            "The agent it names is no longer available to the caller (access revoked, or the "
            "agent deleted): starting it is refused"
        ),
    )
    blocked_reason: str = Field(default="", description="Why it is blocked, for the caller")


def _store(request: Request) -> TemplateStore:
    return template_store(request.app.state)


def _view(template: SessionTemplate, blocked_agents: set[str] | None = None) -> TemplateResponse:
    data = {k: v for k, v in vars(template).items() if k in TemplateResponse.model_fields}
    blocked = bool(template.agent_id) and template.agent_id in (blocked_agents or set())
    return TemplateResponse(
        scope="org" if template.user_id is None else "user",
        blocked=blocked,
        blocked_reason=TEMPLATE_BLOCKED_REASON if blocked else "",
        **data,
    )


@router.get("/templates", response_model=list[TemplateResponse])
async def list_templates(request: Request) -> list[TemplateResponse]:
    """The organization's templates and the caller's own; ``blocked`` marks those naming an
    agent the caller may no longer use."""
    org_id, user_id, roles = _get_request_identity(request)
    templates = await _store(request).visible_to(org_id, user_id)
    db_mgr = getattr(request.app.state, "db_manager", None)
    blocked = await blocked_for(db_mgr, org_id, user_id, roles, templates)
    return [_view(t, blocked) for t in templates]


@router.post("/templates", response_model=TemplateResponse)
async def create_template(body: TemplateBody, request: Request) -> TemplateResponse:
    org_id, user_id, roles = _get_request_identity(request)
    is_admin = holds_admin(roles)
    if not can_write(roles):
        raise HTTPException(status_code=403, detail="Forbidden: viewers cannot create templates")
    if body.scope == "org" and not is_admin:
        raise HTTPException(
            status_code=403, detail="Only administrators can create organization-wide templates."
        )
    if body.permission_mode is not None and body.permission_mode not in PERMISSION_MODES:
        raise HTTPException(
            status_code=422, detail=f"permission_mode must be one of: {', '.join(PERMISSION_MODES)}"
        )
    await _usable_agent(request, org_id, user_id, roles, body.agent_id)
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
    if template.user_id is None and not holds_admin(roles):
        raise HTTPException(
            status_code=403, detail="Only administrators can delete organization-wide templates."
        )
    await store.delete(org_id, template_id)
    return {"status": "deleted"}


@router.post("/templates/{template_id}/start", response_model=SessionResponse)
async def start_from_template(template_id: str, request: Request) -> SessionResponse:
    """Start a session from the template (the opening prompt runs immediately); refused (403)
    while the template is blocked."""
    org_id, user_id, roles = _get_request_identity(request)
    template = await _store(request).get(org_id, user_id, template_id)
    if template is None:
        raise HTTPException(status_code=404, detail="Template not found")
    if template.agent_id and await blocked_for(
        getattr(request.app.state, "db_manager", None), org_id, user_id, roles, [template]
    ):
        raise HTTPException(status_code=403, detail=TEMPLATE_BLOCKED_REASON)
    return await create_session(CreateSessionRequest(**as_session_request(template)), request)


async def _usable_agent(
    request: Request, org_id: str, user_id: str, roles: list[str], agent_id: str | None
) -> None:
    """403 for an agent the caller may not use (``agent_access``)."""
    try:
        await require_usable_agent(
            getattr(request.app.state, "db_manager", None), org_id, user_id, roles, agent_id
        )
    except AgentAccessDenied as err:
        raise HTTPException(status_code=403, detail=str(err)) from err

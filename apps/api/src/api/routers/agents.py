"""Agent personas and organization subagent roles."""

import logging

from agent_core.subagents import DEFAULT_SUBAGENT_CONFIGS
from fastapi import (
    APIRouter,
    HTTPException,
    Query,
    Request,
)
from pydantic import BaseModel, Field

from api.auth.access import _get_request_identity, _require_admin
from api.models import (
    AgentLimitsResponse,
    AgentPersonaModel,
    SubagentConfigModel,
)
from api.services.persona_lint import PersonaWarning, lint_persona
from api.services.personas import (
    PersonaError,
    delete_persona,
    list_personas,
    save_persona,
)
from api.services.subagents import (
    ROLE_ID_PATTERN,
    delete_subagent_config,
    list_subagent_configs,
    save_subagent_config,
)
from api.services.turn_limits import organization_max_turns

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1", tags=["Agents"])


@router.get("/agents", response_model=list[AgentPersonaModel])
async def list_agents(request: Request) -> list[AgentPersonaModel]:
    """List the built-in personas plus the organization's and the caller's own."""
    org_id, user_id, _ = _get_request_identity(request)
    return await list_personas(getattr(request.app.state, "db_manager", None), org_id, user_id)


@router.get("/agents/limits", response_model=AgentLimitsResponse)
async def agent_limits(request: Request) -> AgentLimitsResponse:
    """The ceilings an agent's own settings must stay under (today: steps per turn)."""
    org_id, _user_id, _roles = _get_request_identity(request)
    return AgentLimitsResponse(
        max_turns=await organization_max_turns(
            getattr(request.app.state, "db_manager", None), org_id
        )
    )


@router.post("/agents", response_model=AgentPersonaModel)
async def create_or_update_agent(persona: AgentPersonaModel, request: Request) -> AgentPersonaModel:
    """Create or update a custom agent persona, enforcing scope boundaries.

    A non-administrator who does not name a scope gets a personal persona. The returned
    persona carries the stored id, which can differ from the proposed one.
    """
    org_id, user_id, roles = _get_request_identity(request)
    is_admin = "admin" in roles or "owner" in roles
    if persona.scope == "org" and not is_admin and "scope" not in persona.model_fields_set:
        persona.scope = "user"
    try:
        return await save_persona(
            getattr(request.app.state, "db_manager", None), org_id, user_id, is_admin, persona
        )
    except PersonaError as err:
        raise HTTPException(status_code=err.status_code, detail=err.detail) from err


class PersonaLintRequest(BaseModel):
    system_prompt: str = Field(..., max_length=200_000, description="The prompt to check")


class PersonaLintResponse(BaseModel):
    warnings: list[PersonaWarning] = Field(
        default_factory=list, description="What to look at; a save is never refused for these"
    )


@router.post("/agents/lint", response_model=PersonaLintResponse)
async def lint_agent_prompt(body: PersonaLintRequest, request: Request) -> PersonaLintResponse:
    """Warnings about an agent's prompt: over 4,000 characters, a tool that does not exist, or
    an argument a tool does not take. For the agent editor; nothing is stored."""
    _org_id, _user_id, roles = _get_request_identity(request)
    if "readonly" in roles:
        raise HTTPException(status_code=403, detail="Forbidden: Read-only role cannot edit agents")
    registry = getattr(request.app.state, "tool_registry", None)
    definitions = registry.get_tool_definitions() if registry is not None else []
    tools = {
        d.name: list((d.parameters_json_schema or {}).get("properties", {})) for d in definitions
    }
    return PersonaLintResponse(warnings=lint_persona(body.system_prompt, tools))


@router.delete("/agents/{persona_id}")
async def delete_agent(
    persona_id: str, request: Request, disable: bool = Query(default=False)
) -> dict[str, bool]:
    """Delete a custom agent persona, or reset/disable a built-in persona."""
    org_id, user_id, roles = _get_request_identity(request)
    is_admin = "admin" in roles or "owner" in roles
    try:
        await delete_persona(
            getattr(request.app.state, "db_manager", None),
            org_id,
            user_id,
            is_admin,
            persona_id,
            disable=disable,
        )
    except PersonaError as err:
        raise HTTPException(status_code=err.status_code, detail=err.detail) from err
    return {"deleted": True}


# --- SUBAGENTS CONFIGURATION API ---


@router.get("/subagents", response_model=list[SubagentConfigModel])
async def list_subagents(request: Request) -> list[SubagentConfigModel]:
    """List the organization's subagent roles (built-in defaults plus its overrides)."""
    org_id, _, _ = _get_request_identity(request)
    return await list_subagent_configs(getattr(request.app.state, "db_manager", None), org_id)


@router.put("/subagents/{subagent_id}", response_model=SubagentConfigModel)
async def update_subagent_config(
    subagent_id: str, cfg: SubagentConfigModel, request: Request
) -> SubagentConfigModel:
    """Override a subagent role for this organization (admin only; other orgs are unaffected)."""
    org_id, _, _ = _require_admin(request)
    if not ROLE_ID_PATTERN.match(subagent_id) or cfg.id != subagent_id:
        raise HTTPException(
            status_code=422,
            detail="The role id must match the URL and be 1-32 chars of a-z, 0-9, '_' or '-'.",
        )
    return await save_subagent_config(getattr(request.app.state, "db_manager", None), org_id, cfg)


@router.delete("/subagents/{subagent_id}")
async def delete_subagent(
    subagent_id: str, request: Request, disable: bool = Query(default=False)
) -> dict[str, bool]:
    """Delete a custom subagent role or reset/disable an overridden built-in role."""
    org_id, _, _ = _require_admin(request)
    deleted, reset = await delete_subagent_config(
        getattr(request.app.state, "db_manager", None), org_id, subagent_id, disable=disable
    )
    if not deleted and subagent_id not in DEFAULT_SUBAGENT_CONFIGS:
        raise HTTPException(status_code=404, detail="Subagent not found")
    return {"deleted": deleted, "reset_to_default": reset}

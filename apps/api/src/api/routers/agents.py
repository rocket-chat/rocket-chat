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
from api.auth.capabilities import Capability, has_capability
from api.auth.roles import ROLES, can_write
from api.auth.roles import is_admin as holds_admin
from api.models import (
    AgentLimitsResponse,
    AgentPersonaModel,
    SubagentConfigModel,
)
from api.routers.credentials import credential_store
from api.services.agent_access import (
    PrincipalKind,
    grants_for,
    pause_missions_without_access,
    set_grants,
    templates_blocked_by,
)
from api.services.audit import AuditService
from api.services.persona_lint import PersonaWarning, lint_persona
from api.services.personas import (
    PersonaError,
    delete_persona,
    get_persona,
    list_personas,
    save_persona,
    stored_bindings,
)
from api.services.session_templates import template_store
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
    """The agents the caller may use: built-in, the organization's (per their access grants)
    and the caller's own. Administrators also see disabled organization agents."""
    org_id, user_id, roles = _get_request_identity(request)
    return await list_personas(
        getattr(request.app.state, "db_manager", None), org_id, user_id, roles
    )


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
    if not can_write(roles):
        raise HTTPException(status_code=403, detail="Forbidden: viewers cannot edit agents")
    is_admin = holds_admin(roles)
    if persona.scope == "org" and not is_admin and "scope" not in persona.model_fields_set:
        persona.scope = "user"
    db_mgr = getattr(request.app.state, "db_manager", None)
    if persona.scope == "user" and not await has_capability(
        db_mgr, org_id, roles, Capability.CREATE_PERSONAL_AGENTS
    ):
        raise HTTPException(
            status_code=403,
            detail="Forbidden: your role does not have the create_personal_agents capability",
        )
    before = await stored_bindings(db_mgr, org_id, persona.id)
    try:
        saved = await save_persona(
            db_mgr, org_id, user_id, is_admin, persona, credentials=credential_store(request)
        )
    except PersonaError as err:
        raise HTTPException(status_code=err.status_code, detail=err.detail) from err
    await _audit_bindings(request, org_id, user_id, saved, before)
    return saved


async def _audit_bindings(
    request: Request,
    org_id: str,
    user_id: str,
    saved: AgentPersonaModel,
    before: list[tuple[str, str]],
) -> None:
    """Record each credential bound to or unbound from the agent."""
    after = [(b.provider, b.credential_id) for b in saved.credential_bindings]
    audit = AuditService(getattr(request.app.state, "db_manager", None))
    for action, pairs in (
        ("bound", [p for p in after if p not in before]),
        ("unbound", [p for p in before if p not in after]),
    ):
        for provider, credential_id in pairs:
            await audit.record(
                "credential_change",
                org_id=org_id,
                actor_user_id=user_id,
                result_summary=f"{action} a {provider} credential on agent {saved.name}",
                metadata={
                    "agent_id": saved.id,
                    "credential_id": credential_id,
                    "provider": provider,
                },
            )


class AgentGrantModel(BaseModel):
    principal_kind: PrincipalKind
    principal_id: str = Field(default="", max_length=64)


class AgentAccessResponse(BaseModel):
    agent_id: str
    # Empty: every member may use the agent (guests excluded).
    grants: list[AgentGrantModel]


class AgentAccessRequest(BaseModel):
    grants: list[AgentGrantModel] = Field(default_factory=list, max_length=500)


@router.get("/agents/{persona_id}/access", response_model=AgentAccessResponse)
async def get_agent_access(persona_id: str, request: Request) -> AgentAccessResponse:
    """Who may use an organization agent (administrators)."""
    org_id, _, _ = _require_admin(request)
    grants = await grants_for(getattr(request.app.state, "db_manager", None), org_id, persona_id)
    return AgentAccessResponse(
        agent_id=persona_id,
        grants=[
            AgentGrantModel(principal_kind=g.principal_kind, principal_id=g.principal_id)
            for g in grants
        ],
    )


@router.put("/agents/{persona_id}/access", response_model=AgentAccessResponse)
async def put_agent_access(
    persona_id: str, body: AgentAccessRequest, request: Request
) -> AgentAccessResponse:
    """Replace who may use an organization agent (administrators). Turns already running
    finish; the next turn of someone who lost access fails with ``agent_access_denied``, and
    scheduled missions of people who lost it are paused."""
    org_id, admin_id, roles = _require_admin(request)
    db_mgr = getattr(request.app.state, "db_manager", None)
    agent = await get_persona(db_mgr, org_id, admin_id, persona_id, roles=roles)
    if agent is None or agent.scope == "user" or agent.user_id:
        raise HTTPException(status_code=404, detail="Organization agent not found")
    for grant in body.grants:
        if grant.principal_kind in ("member", "role") and not grant.principal_id:
            raise HTTPException(status_code=422, detail=f"A {grant.principal_kind} grant names one")
        if grant.principal_kind == "role" and grant.principal_id not in ROLES:
            raise HTTPException(status_code=422, detail=f"Unknown role {grant.principal_id!r}")
    saved = await set_grants(
        db_mgr,
        org_id,
        persona_id,
        [(g.principal_kind, g.principal_id) for g in body.grants],
        admin_id,
    )
    paused = await pause_missions_without_access(db_mgr, org_id, agent)
    blocked = await templates_blocked_by(
        db_mgr, org_id, agent, await template_store(request.app.state).all(org_id)
    )
    audit = AuditService(db_mgr)
    await audit.record(
        "settings_change",
        org_id=org_id,
        actor_user_id=admin_id,
        result_summary=f"changed who may use agent {agent.name}",
        metadata={
            "agent_id": persona_id,
            "grants": [f"{g.principal_kind}:{g.principal_id}" for g in saved],
            "paused_missions": paused,
            "blocked_templates": blocked,
        },
    )
    if blocked:
        await audit.record(
            "settings_change",
            org_id=org_id,
            actor_user_id=admin_id,
            result_summary=(
                f"blocked {len(blocked)} template(s) naming agent {agent.name}: "
                "their owners lost access"
            ),
            metadata={"agent_id": persona_id, "blocked_templates": blocked},
        )
    return AgentAccessResponse(
        agent_id=persona_id,
        grants=[
            AgentGrantModel(principal_kind=g.principal_kind, principal_id=g.principal_id)
            for g in saved
        ],
    )


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
    if not can_write(roles):
        raise HTTPException(status_code=403, detail="Forbidden: viewers cannot edit agents")
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
    if not can_write(roles):
        raise HTTPException(status_code=403, detail="Forbidden: viewers cannot edit agents")
    is_admin = holds_admin(roles)
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

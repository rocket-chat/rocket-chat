"""Tool catalog for the agent-configuration UI.

Exposes every tool the runtime can offer — built-in sandbox/repo tools, memory, knowledge,
web, MCP connectors and transport toolsets (e.g. Slack) — so an admin can see the real
toolset and pick which ones a custom agent may use. Previously the agent settings page
showed a stale hard-coded list, so most tools (memory, Slack, MCP, …) were neither visible
nor configurable.
"""

from __future__ import annotations

from types import SimpleNamespace

from agent_core.permissions import permission_class
from agent_core.tool_loading import ALWAYS_LOADED, MCP_PREFIX, deferred_names, sent_count
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from api.auth.access import _get_request_identity
from api.services.tool_loading import describe_loading, tool_loading_for
from specifications.interfaces.agent import ACTIVE_TOOLS_WARNING, CORE_TOOLS
from specifications.interfaces.llm import ToolDefinition

router = APIRouter(prefix="/v1", tags=["Tools"])


class ToolCatalogEntry(BaseModel):
    name: str
    description: str
    category: str = Field(
        ...,
        description="The tool's group: its built-in domain (e.g. Git), toolset (e.g. Slack) or Other",
    )
    toolset: str | None = None
    permission: str = Field(
        default="command", description="read, edit, command or publish (what approval it needs)"
    )
    core: bool = Field(
        default=False, description="A core tool: sent with every step whenever the agent may use it"
    )


class ToolCatalogResponse(BaseModel):
    tools: list[ToolCatalogEntry]
    categories: list[str]


def _category(name: str, domain: str | None, toolset: str | None) -> str:
    if toolset:
        return toolset.capitalize()
    if domain:
        return domain
    if name.startswith(MCP_PREFIX):
        return "Connectors"
    return "Other"


@router.get("/tools", response_model=ToolCatalogResponse)
async def list_tools(request: Request) -> ToolCatalogResponse:
    """The full tool catalog: each tool's name, description, group (its built-in domain or
    toolset), the approval it needs and whether it is a core tool."""
    registry = getattr(request.app.state, "tool_registry", None)
    if registry is None:
        raise HTTPException(status_code=503, detail="Tool registry is not available")

    entries = [
        ToolCatalogEntry(
            name=definition.name,
            description=(definition.description or "").strip(),
            category=_category(
                definition.name,
                registry.domain_of(definition.name),
                registry.toolset_of(definition.name),
            ),
            toolset=registry.toolset_of(definition.name),
            permission=permission_class(definition.name),
            core=definition.name in CORE_TOOLS,
        )
        for definition in registry.catalog()
    ]
    entries.sort(key=lambda entry: (entry.category, entry.name))
    categories = sorted({entry.category for entry in entries})
    return ToolCatalogResponse(tools=entries, categories=categories)


class ToolLoadingPreviewRequest(BaseModel):
    active_tools: list[str] = Field(
        default_factory=list, description="Tools kept active besides the core set, or patterns"
    )
    tool_names: list[str] = Field(
        default_factory=list, max_length=5000, description="The tools the agent may use"
    )


class ToolLoadingPreview(BaseModel):
    core: list[str] = Field(..., description="The core tools, sent with every step when allowed")
    sent: int = Field(..., description="Tools sent with every step: core, active and loading")
    total: int = Field(..., description="Tools the agent may use")
    deferred: list[str] = Field(..., description="Allowed tools listed by name, loaded on demand")
    warning_at: int = Field(..., description="Above this many tools sent, the page warns")
    reason: str = Field(..., description="What this means for the agent, in a sentence")


@router.post("/tools/loading-preview", response_model=ToolLoadingPreview)
async def preview_tool_loading(
    body: ToolLoadingPreviewRequest, request: Request
) -> ToolLoadingPreview:
    """Which of an agent's tools are sent with every step and which wait to be loaded (the same
    rule a turn applies)."""
    _get_request_identity(request)
    loading = tool_loading_for(SimpleNamespace(active_tools=tuple(body.active_tools)))
    names = list(dict.fromkeys([*body.tool_names, *ALWAYS_LOADED - {"load_guidance"}]))
    definitions = [ToolDefinition(name=n, description="", parameters_json_schema={}) for n in names]
    deferred = deferred_names(definitions, loading)
    sent = sent_count(definitions, loading)
    return ToolLoadingPreview(
        core=list(CORE_TOOLS),
        sent=sent,
        total=len(names),
        deferred=sorted(deferred),
        warning_at=ACTIVE_TOOLS_WARNING,
        reason=describe_loading(sent, len(deferred)),
    )

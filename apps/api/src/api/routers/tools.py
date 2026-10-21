"""Tool catalog for the agent-configuration UI.

Exposes every tool the runtime can offer — built-in sandbox/repo tools, memory, knowledge,
web, MCP connectors and transport toolsets (e.g. Slack) — so an admin can see the real
toolset and pick which ones a custom agent may use. Previously the agent settings page
showed a stale hard-coded list, so most tools (memory, Slack, MCP, …) were neither visible
nor configurable.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Literal

from agent_core.tool_loading import ALWAYS_LOADED, MCP_PREFIX, deferred_names
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from api.auth.access import _get_request_identity
from api.services.tool_loading import describe_loading, tool_loading_for
from specifications.interfaces.llm import ToolDefinition

router = APIRouter(prefix="/v1", tags=["Tools"])

_SANDBOX_TOOLS = {
    "bash_exec",
    "file_read",
    "file_write",
    "file_edit",
    "apply_patch",
    "apply_diff_file",
    "list_dir",
    "execute_skill",
    "git_log",
    "git_status",
    "git_diff",
    "package_manager",
    "think",
}
_MEMORY_TOOLS = {"remember", "recall", "kb_search"}
_WEB_TOOLS = {"web_search", "web_fetch", "browser_inspect"}


class ToolCatalogEntry(BaseModel):
    name: str
    description: str
    category: str
    toolset: str | None = None


class ToolCatalogResponse(BaseModel):
    tools: list[ToolCatalogEntry]
    categories: list[str]


def _category(name: str, toolset: str | None) -> str:
    if toolset:
        return toolset.capitalize()
    if name in _MEMORY_TOOLS:
        return "Memory & Knowledge"
    if name in _SANDBOX_TOOLS:
        return "Sandbox & Repository"
    if name in _WEB_TOOLS:
        return "Web"
    if name.startswith("mcp"):
        return "Connectors"
    return "General"


@router.get("/tools", response_model=ToolCatalogResponse)
async def list_tools(request: Request) -> ToolCatalogResponse:
    """The full tool catalog (name, description, category, toolset)."""
    registry = getattr(request.app.state, "tool_registry", None)
    if registry is None:
        raise HTTPException(status_code=503, detail="Tool registry is not available")

    entries = [
        ToolCatalogEntry(
            name=definition.name,
            description=(definition.description or "").strip(),
            category=_category(definition.name, registry.toolset_of(definition.name)),
            toolset=registry.toolset_of(definition.name),
        )
        for definition in registry.catalog()
    ]
    entries.sort(key=lambda entry: (entry.category, entry.name))
    categories = sorted({entry.category for entry in entries})
    return ToolCatalogResponse(tools=entries, categories=categories)


class ToolLoadingPreviewRequest(BaseModel):
    tool_loading: Literal["all", "on_demand", "pinned", "auto"] | None = Field(
        default=None, description="The agent's own mode; empty inherits the organization's"
    )
    active_tools: list[str] = Field(
        default_factory=list, description="Always-loaded tool names or shell-style patterns"
    )
    tool_names: list[str] = Field(
        default_factory=list, max_length=5000, description="The tools the agent may use"
    )


class ToolLoadingPreview(BaseModel):
    mode: str = Field(..., description="The mode in force: all, on_demand, pinned or auto")
    source: Literal["agent", "organization"] = Field(
        ..., description="Whether the agent or the organization chose the mode"
    )
    threshold: int = Field(..., description="The organization's limit for the automatic mode")
    total: int = Field(..., description="Tools counted against the limit")
    deferred: list[str] = Field(..., description="Allowed tools listed by name, loaded on demand")
    reason: str = Field(..., description="What this means for the agent, in a sentence")


@router.post("/tools/loading-preview", response_model=ToolLoadingPreview)
async def preview_tool_loading(
    body: ToolLoadingPreviewRequest, request: Request
) -> ToolLoadingPreview:
    """Which of an agent's tools are sent with every step and which wait to be loaded, under its
    settings and the organization's current ones (the same rule a turn applies)."""
    org_id, _, _ = _get_request_identity(request)
    db_mgr = getattr(request.app.state, "db_manager", None)
    agent = SimpleNamespace(tool_loading=body.tool_loading, active_tools=tuple(body.active_tools))
    loading = await tool_loading_for(db_mgr, org_id, agent)
    names = list(dict.fromkeys([*body.tool_names, *ALWAYS_LOADED - {"load_guidance"}]))
    deferred = deferred_names(
        [ToolDefinition(name=n, description="", parameters_json_schema={}) for n in names],
        loading,
        lambda name: name.startswith(MCP_PREFIX),
    )
    return ToolLoadingPreview(
        mode=loading.mode,
        source="agent" if body.tool_loading else "organization",
        threshold=loading.threshold,
        total=len(names),
        deferred=sorted(deferred),
        reason=describe_loading(loading, len(names), len(deferred)),
    )

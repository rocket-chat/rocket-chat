"""Tool catalog for the agent-configuration UI.

Exposes every tool the runtime can offer — built-in sandbox/repo tools, memory, knowledge,
web, MCP connectors and transport toolsets (e.g. Slack) — so an admin can see the real
toolset and pick which ones a custom agent may use. Previously the agent settings page
showed a stale hard-coded list, so most tools (memory, Slack, MCP, …) were neither visible
nor configurable.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

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

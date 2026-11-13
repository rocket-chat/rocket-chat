"""The MCP endpoint: sandbox lifecycle tools and the proxied platform tools.

Served as streamable HTTP under ``SANDBOX_MCP_PATH``, behind the API's own authentication: the
caller is the member a personal API token (or any credential the API accepts) identifies, never
an argument. Stateless (``stateless_http``), so any replica answers any call and no sticky
routing is needed: the sandbox a call acts in is named by its ``sandbox_id`` argument.
"""

from __future__ import annotations

import copy
from collections.abc import Awaitable
from dataclasses import asdict
from typing import Any

from agent_core.tools.registry import ToolRegistry
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.server.dependencies import get_http_request
from fastmcp.server.http import StarletteWithLifespan
from fastmcp.tools import Tool
from fastmcp.tools.base import ToolResult
from mcp.types import TextContent
from pydantic import Field, PrivateAttr

from api.auth.access import _get_request_identity
from api.sandbox_mcp.service import SandboxMcpService
from specifications.interfaces.llm import ToolDefinition
from specifications.interfaces.sandbox_mcp import (
    SANDBOX_ID_ARGUMENT,
    SANDBOX_MCP_TOOLS,
    McpCaller,
    SandboxMcpError,
    SandboxMcpServiceProtocol,
)

INSTRUCTIONS = (
    "Remote development sandboxes from the organization's catalogue. Call sandbox_types, then "
    "sandbox_open, and pass the sandbox_id it returns to every other tool. A sandbox keeps its "
    "workspace between calls, pauses when idle and resumes on the next call. Clone with "
    "clone_repository and publish with git_push: a plain `git push` is refused in the sandbox. "
    "Call sandbox_close when the work is pushed; what was not pushed is lost."
)

_SANDBOX_ID_SCHEMA = {
    "type": "string",
    "description": "The sandbox to act in, as sandbox_open or sandbox_list returned it.",
}


def build_sandbox_mcp(service: SandboxMcpServiceProtocol) -> FastMCP:
    """The MCP server over ``service``; the proxied tools' schemas are the platform's own."""
    server = FastMCP("rocket-chat-sandboxes", instructions=INSTRUCTIONS)
    _add_lifecycle_tools(server, service)
    for definition in _proxied_definitions():
        server.add_tool(ProxiedTool.of(definition, service))
    return server


def sandbox_mcp_http_app(state: Any) -> StarletteWithLifespan:
    """The ASGI application the API mounts at ``SANDBOX_MCP_PATH`` (its lifespan must run)."""
    server = build_sandbox_mcp(SandboxMcpService(state))
    return server.http_app(path="/", stateless_http=True, json_response=True)


def current_caller() -> McpCaller:
    """The member the current MCP request is authenticated as."""
    org_id, user_id, roles = _get_request_identity(get_http_request())
    return McpCaller(org_id=org_id, user_id=user_id, roles=tuple(roles))


class ProxiedTool(Tool):
    """A platform tool served over MCP: its schema plus ``sandbox_id``, run in that sandbox."""

    _service: SandboxMcpServiceProtocol = PrivateAttr()

    @classmethod
    def of(cls, definition: ToolDefinition, service: SandboxMcpServiceProtocol) -> ProxiedTool:
        parameters = copy.deepcopy(definition.parameters_json_schema) or {"type": "object"}
        parameters.setdefault("properties", {})[SANDBOX_ID_ARGUMENT] = _SANDBOX_ID_SCHEMA
        parameters["required"] = [SANDBOX_ID_ARGUMENT, *parameters.get("required", [])]
        tool = cls(name=definition.name, description=definition.description, parameters=parameters)
        tool._service = service
        return tool

    async def run(self, arguments: dict[str, Any]) -> ToolResult:
        own = dict(arguments)
        sandbox_id = str(own.pop(SANDBOX_ID_ARGUMENT, "") or "")
        try:
            outcome = await self._service.call_tool(current_caller(), sandbox_id, self.name, own)
        except SandboxMcpError as err:
            raise ToolError(str(err)) from err
        return ToolResult(
            content=[TextContent(type="text", text=outcome.text)], is_error=outcome.is_error
        )


def _proxied_definitions() -> list[ToolDefinition]:
    """The definitions of ``SANDBOX_MCP_TOOLS``, in that order, from the platform registry."""
    by_name = {d.name: d for d in ToolRegistry().get_tool_definitions()}
    missing = [name for name in SANDBOX_MCP_TOOLS if name not in by_name]
    if missing:
        raise RuntimeError(f"Sandbox MCP tools missing from the registry: {missing}")
    return [by_name[name] for name in SANDBOX_MCP_TOOLS]


def _add_lifecycle_tools(server: FastMCP, service: SandboxMcpServiceProtocol) -> None:
    @server.tool
    async def sandbox_types() -> list[dict[str, Any]]:
        """List the sandbox types you may open: image, capabilities and the names of the
        variables every command gets (their values are never shown)."""
        return [asdict(t) for t in await _refusing(service.sandbox_types(current_caller()))]

    @server.tool
    async def sandbox_open(
        sandbox_type: str | None = Field(
            default=None, description="A type id from sandbox_types (default: the default type)."
        ),
        title: str | None = Field(
            default=None, description="A name to recognise the sandbox by in the cockpit."
        ),
    ) -> dict[str, Any]:
        """Open a sandbox of a type and return its sandbox_id. It starts on the first call that
        needs it, blank: clone what you need with clone_repository."""
        opened = await _refusing(service.open_sandbox(current_caller(), sandbox_type, title))
        return asdict(opened)

    @server.tool
    async def sandbox_list() -> list[dict[str, Any]]:
        """List your open sandboxes, newest first."""
        return [asdict(s) for s in await _refusing(service.list_sandboxes(current_caller()))]

    @server.tool
    async def sandbox_close(
        sandbox_id: str = Field(description="The sandbox to delete, with its workspace."),
    ) -> dict[str, Any]:
        """Delete a sandbox and its workspace. Push your work first: the rest is lost."""
        closed = await _refusing(service.close_sandbox(current_caller(), sandbox_id))
        return {"closed": closed, "sandbox_id": sandbox_id}


async def _refusing[T](pending: Awaitable[T]) -> T:
    """Await a service call, turning its refusal into the error the MCP client is shown."""
    try:
        return await pending
    except SandboxMcpError as err:
        raise ToolError(str(err)) from err

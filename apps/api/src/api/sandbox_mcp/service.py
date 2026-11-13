"""Sandboxes as sessions: the types a member may open, and their sandboxes' lifecycle.

A sandbox opened here is an ordinary session (``SessionSource.MCP``) owned by the caller: the
cockpit lists it, its workspace browser and terminal open it, and every session mechanism
applies (provisioning on the first call that needs the sandbox, idle hibernation, workspace
release). Only the MCP's own sessions take MCP tool calls, so an outside agent never works in
a session where the platform's agent works.
"""

from __future__ import annotations

import contextlib
import uuid
from collections.abc import Iterator, Mapping
from typing import Any

from api.auth.roles import can_write
from api.db.session import current_tenant_org_id
from api.sandbox_mcp.calls import forget_session, run_tool_call
from api.sandbox_mcp.platform import Platform
from api.services.sandbox_catalogue import SandboxCatalogue, SandboxEntry
from api.services.session_purge import purge_session
from api.services.settings import effective_settings
from api.services.teams import turn_teams
from specifications.interfaces.sandbox import SandboxStatus
from specifications.interfaces.sandbox_mcp import (
    McpCaller,
    McpSandbox,
    SandboxMcpError,
    SandboxMcpServiceProtocol,
    SandboxTypeSummary,
    ToolCallOutcome,
)
from specifications.interfaces.session import SessionRecord, SessionSource

# How many of a member's sessions are read to find their MCP sandboxes (newest first).
_SCAN_LIMIT = 1000


class SandboxMcpService(SandboxMcpServiceProtocol):
    """The platform side of the sandbox MCP. ``state`` is the API application's state."""

    def __init__(self, state: Any) -> None:
        self._state = state

    @property
    def _platform(self) -> Platform:
        # Read when used: the application's collaborators exist only once it has started.
        return Platform.of(self._state)

    async def sandbox_types(self, caller: McpCaller) -> list[SandboxTypeSummary]:
        """The catalogue's types the caller may open (their teams' included)."""
        with _tenant(caller):
            await self._settings(caller)
            entries = await SandboxCatalogue(self._platform.db_mgr).entries(
                caller.org_id, teams=await self._teams(caller)
            )
            return [_summary(entry) for entry in entries]

    async def open_sandbox(
        self, caller: McpCaller, sandbox_type_id: str | None, title: str | None
    ) -> McpSandbox:
        """Create the caller's sandbox session; its sandbox starts on the first call needing it."""
        with _tenant(caller):
            settings = await self._settings(caller)
            if not can_write(list(caller.roles)):
                raise SandboxMcpError("A viewer cannot open sandboxes.")
            limit = int(settings["mcp_max_open_sandboxes"])
            if limit and len(await self._own_sandboxes(caller)) >= limit:
                raise SandboxMcpError(
                    f"You already have {limit} sandboxes open through the MCP "
                    "(sandboxes.mcp_max_open_sandboxes); close one first."
                )
            entry = await self._sandbox_type(caller, sandbox_type_id)
            record = SessionRecord(
                session_id=f"session_{uuid.uuid4().hex[:10]}",
                tenant_org_id=caller.org_id,
                tenant_user_id=caller.user_id,
                title=(title or "").strip()[:100] or f"MCP sandbox ({entry.name})",
                source=SessionSource.MCP,
                sandbox_status=SandboxStatus.NON_EXISTENT,
                container_image=entry.image,
                metadata={"sandbox_id": entry.id, "is_shared": False, "collaborators": []},
            )
            await self._platform.store.create_session(record)
            return _view(record)

    async def list_sandboxes(self, caller: McpCaller) -> list[McpSandbox]:
        """The caller's open MCP sandboxes, newest first."""
        with _tenant(caller):
            await self._settings(caller)
            return [_view(record) for record in await self._own_sandboxes(caller)]

    async def close_sandbox(self, caller: McpCaller, sandbox_id: str) -> bool:
        """Delete the sandbox's session and its workspace (what was not pushed is lost)."""
        with _tenant(caller):
            await self._settings(caller)
            record = await self._sandbox(caller, sandbox_id)
            forget_session(record.session_id)
            return await purge_session(self._state, record)

    async def call_tool(
        self, caller: McpCaller, sandbox_id: str, name: str, arguments: dict[str, object]
    ) -> ToolCallOutcome:
        """Run one proxied tool in the caller's sandbox."""
        with _tenant(caller):
            await self._settings(caller)
            record = await self._sandbox(caller, sandbox_id)
            return await run_tool_call(
                self._platform, record, caller.user_id, name, dict(arguments)
            )

    async def _settings(self, caller: McpCaller) -> Mapping[str, Any]:
        settings = await effective_settings(self._platform.db_mgr, caller.org_id, "sandboxes")
        if not settings["mcp_enabled"]:
            raise SandboxMcpError(
                "The sandbox MCP is turned off for your organization; an administrator can turn "
                "it on (sandboxes.mcp_enabled)."
            )
        return settings

    async def _teams(self, caller: McpCaller) -> frozenset[str]:
        return await turn_teams(self._platform.db_mgr, caller.org_id, caller.user_id, None)

    async def _sandbox_type(self, caller: McpCaller, sandbox_type_id: str | None) -> SandboxEntry:
        catalogue = SandboxCatalogue(self._platform.db_mgr)
        teams = await self._teams(caller)
        if not sandbox_type_id:
            return await catalogue.default(caller.org_id)
        entry = await catalogue.resolve(caller.org_id, sandbox_id=sandbox_type_id, teams=teams)
        if entry is None:
            raise SandboxMcpError(
                f"There is no sandbox type '{sandbox_type_id}' you may use; "
                "list them with sandbox_types."
            )
        return entry

    async def _own_sandboxes(self, caller: McpCaller) -> list[SessionRecord]:
        records = await self._platform.store.list_sessions(
            caller.org_id, caller.user_id, limit=_SCAN_LIMIT
        )
        return [r for r in records if r.source == SessionSource.MCP]

    async def _sandbox(self, caller: McpCaller, sandbox_id: str) -> SessionRecord:
        """The caller's own MCP sandbox, or a refusal that does not say whether another exists.

        Owner only, administrators included: the sandbox's git runs with its owner's
        credentials, so nobody else's call may act in it.
        """
        record = await self._platform.store.get_session(sandbox_id, include_history=False)
        if (
            record is None
            or record.tenant_user_id != caller.user_id
            or record.tenant_org_id != caller.org_id
            or record.source != SessionSource.MCP
        ):
            raise SandboxMcpError(
                f"No sandbox '{sandbox_id}' of yours; list them with sandbox_list."
            )
        return record


@contextlib.contextmanager
def _tenant(caller: McpCaller) -> Iterator[None]:
    """Bind the caller's organization for row-level security while the MCP works for them.

    The MCP transport runs tools in its own tasks, so the binding the HTTP middleware made for
    the request does not reach them.
    """
    token = current_tenant_org_id.set(caller.org_id)
    try:
        yield
    finally:
        current_tenant_org_id.reset(token)


def _summary(entry: SandboxEntry) -> SandboxTypeSummary:
    return SandboxTypeSummary(
        id=entry.id,
        name=entry.name,
        image=entry.image,
        description=entry.description,
        is_default=entry.is_default,
        capabilities=tuple(entry.capabilities),
        variables=tuple(str(v["name"]) for v in entry.env),
    )


def _view(record: SessionRecord) -> McpSandbox:
    metadata = record.metadata if isinstance(record.metadata, dict) else {}
    status = record.sandbox_status
    return McpSandbox(
        sandbox_id=record.session_id,
        title=record.title,
        sandbox_type_id=str(metadata.get("sandbox_id") or ""),
        status=status.value if isinstance(status, SandboxStatus) else str(status),
        created_at=record.created_at,
        cockpit_path=f"/chat/{record.session_id}",
    )

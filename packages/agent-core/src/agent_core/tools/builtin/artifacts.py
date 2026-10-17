"""Durable artifact tools for creating and managing deliverables outside the sandbox."""

from __future__ import annotations

import logging
import uuid
from typing import TYPE_CHECKING, Annotated, Literal

from pydantic import Field

from agent_core.context import current_execution_ctx
from specifications.interfaces.session import SessionArtifact

if TYPE_CHECKING:
    from agent_core.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)


def register(registry: ToolRegistry) -> None:
    _register_artifact_tools(registry)


def _register_artifact_tools(registry: ToolRegistry) -> None:
    @registry._tool(
        description=(
            "Create a durable session artifact (specifications, architecture documents, migration "
            "plans, diff reports, diagrams, or JSON deliverables). Artifacts are stored in the "
            "session control plane, readable by the user in the Cockpit Artifacts drawer independently "
            "of sandbox pod lifecycles, and save LLM context window by replacing large outputs "
            "with lightweight receipts."
        )
    )
    async def create_artifact(
        title: Annotated[
            str, Field(description="Human-readable title (e.g. 'Database Migration Plan').")
        ],
        filename: Annotated[
            str,
            Field(
                description="Descriptive filename with extension (e.g. 'migration_plan.md', 'architecture.mermaid')."
            ),
        ],
        content: Annotated[
            str, Field(description="The complete text/code content of the artifact.")
        ],
        artifact_type: Annotated[
            Literal["markdown", "diff", "diagram", "html", "json"],
            Field(description="Type of the artifact (default: 'markdown')."),
        ] = "markdown",
    ) -> str:
        ctx = current_execution_ctx.get()
        session_id = ctx.session_id if ctx else (registry._session_id or "default")
        org_id = ctx.tenant_org_id if ctx else "default_org"

        create_cb = registry._turn_callback("create_artifact")
        artifact_id = f"art_{uuid.uuid4().hex[:12]}"
        artifact = SessionArtifact(
            id=artifact_id,
            session_id=session_id,
            tenant_org_id=org_id,
            title=title,
            filename=filename,
            artifact_type=artifact_type,
            content=content,
        )

        if create_cb:
            saved = await create_cb(artifact)
            if saved:
                artifact = saved

        return (
            f"Artifact '{title}' created successfully.\n"
            f"- Artifact ID: {artifact.id}\n"
            f"- Filename: {artifact.filename}\n"
            f"- Type: {artifact.artifact_type} (version {artifact.version})\n"
            "- Content length: " + f"{len(content)} characters\n"
            "(This deliverable is permanently stored in the session artifacts drawer and viewable outside the sandbox.)"
        )

    @registry._tool(
        description=(
            "Update an existing session artifact with new content, incrementing its version."
        )
    )
    async def update_artifact(
        artifact_id: Annotated[str, Field(description="The unique artifact ID to update.")],
        content: Annotated[str, Field(description="The updated text/code content.")],
    ) -> str:
        ctx = current_execution_ctx.get()
        session_id = ctx.session_id if ctx else (registry._session_id or "default")
        org_id = ctx.tenant_org_id if ctx else "default_org"

        update_cb = registry._turn_callback("update_artifact")
        if update_cb:
            updated = await update_cb(session_id, artifact_id, org_id, content)
            if updated:
                return (
                    f"Artifact '{updated.title}' updated successfully.\n"
                    f"- Artifact ID: {updated.id}\n"
                    f"- Version: {updated.version}\n"
                    f"- New length: {len(content)} characters"
                )
            return f"Error: Artifact '{artifact_id}' not found."
        return f"Artifact {artifact_id} updated."

    @registry._tool(
        description="Retrieve the content of a previously created session artifact by its ID."
    )
    async def read_artifact(
        artifact_id: Annotated[str, Field(description="The artifact ID to retrieve.")],
    ) -> str:
        ctx = current_execution_ctx.get()
        session_id = ctx.session_id if ctx else (registry._session_id or "default")
        org_id = ctx.tenant_org_id if ctx else "default_org"

        get_cb = registry._turn_callback("get_artifact")
        if get_cb:
            art = await get_cb(session_id, artifact_id, org_id)
            if art:
                return (
                    f"=== Artifact: {art.title} ({art.filename}, v{art.version}) ===\n\n"
                    f"{art.content}"
                )
            return f"Error: Artifact '{artifact_id}' not found."
        return f"Artifact {artifact_id} content unavailable."

    @registry._tool(description="List all durable artifacts created in the current session.")
    async def list_artifacts() -> str:
        ctx = current_execution_ctx.get()
        session_id = ctx.session_id if ctx else (registry._session_id or "default")
        org_id = ctx.tenant_org_id if ctx else "default_org"

        list_cb = registry._turn_callback("list_artifacts")
        if list_cb:
            artifacts = await list_cb(session_id, org_id)
            if not artifacts:
                return "No artifacts have been created in this session yet."
            lines = ["Session Artifacts:"]
            for art in artifacts:
                lines.append(
                    f"- [{art.artifact_type}] {art.title} (ID: {art.id}, file: {art.filename}, v{art.version})"
                )
            return "\n".join(lines)
        return "No artifacts."

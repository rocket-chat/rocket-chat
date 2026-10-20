"""Durable artifact tools for creating and managing deliverables outside the sandbox."""

from __future__ import annotations

import logging
import uuid
from typing import TYPE_CHECKING, Annotated, Literal

from pydantic import BaseModel, Field

from agent_core.context import current_execution_ctx
from specifications.interfaces.session import SessionArtifact

if TYPE_CHECKING:
    from agent_core.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)

NO_SESSION = "Error: artifacts belong to a session, and this call has none."
# A registry bound to a session but run outside a turn (an embedding, a test) has no
# organization of its own; inside a turn the execution context always names it.
EMBEDDED_ORG = "default_org"


class ArtifactEdit(BaseModel):
    """One exact replacement in an artifact's text."""

    old_text: str = Field(description="Text to replace; it must occur exactly once.")
    new_text: str = Field(description="What it becomes (empty deletes it).")


def register(registry: ToolRegistry) -> None:
    _register_artifact_tools(registry)


def _scope(registry: ToolRegistry) -> tuple[str, str] | None:
    """``(session_id, org_id)`` the artifact calls act on, or None outside a session."""
    ctx = current_execution_ctx.get()
    session_id = ctx.session_id if ctx else registry._session_id
    if not session_id:
        return None
    return session_id, ctx.tenant_org_id if ctx else EMBEDDED_ORG


def _cut_notice(sent: str, kept: str) -> str:
    if len(kept) >= len(sent):
        return ""
    return (
        f"\nNote: only the first {len(kept):,} of {len(sent):,} characters were kept. Split the "
        "rest into another artifact."
    )


def apply_edits(text: str, edits: list[ArtifactEdit]) -> str:
    """``text`` with every edit applied in order; a miss or an ambiguous match raises."""
    for number, edit in enumerate(edits, start=1):
        found = text.count(edit.old_text)
        if not edit.old_text or found != 1:
            problem = "was not found" if found == 0 else "matches more than once"
            raise ValueError(f"Edit {number}: the text {problem}; nothing was changed.")
        text = text.replace(edit.old_text, edit.new_text, 1)
    return text


async def _create(
    registry: ToolRegistry, title: str, filename: str, content: str, artifact_type: str
) -> str:
    scope = _scope(registry)
    if scope is None:
        return NO_SESSION
    session_id, org_id = scope
    create_cb = registry._turn_callback("create_artifact")
    artifact = SessionArtifact(
        id=f"art_{uuid.uuid4().hex[:12]}",
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
        f"- Content length: {len(artifact.content):,} characters"
        + _cut_notice(content, artifact.content)
    )


async def _new_text(
    registry: ToolRegistry,
    scope: tuple[str, str],
    artifact_id: str,
    content: str | None,
    edits: list[ArtifactEdit] | None,
) -> str:
    """The text an update saves: the content given, or the current text with the edits applied."""
    if (content is None) == (edits is None):
        raise ValueError("send either `content` or `edits`, not both and not neither.")
    if content is not None:
        return content
    get_cb = registry._turn_callback("get_artifact")
    current = await get_cb(scope[0], artifact_id, scope[1]) if get_cb else None
    if current is None:
        raise LookupError(f"Artifact '{artifact_id}' not found.")
    return apply_edits(current.content, edits or [])


async def _update(
    registry: ToolRegistry,
    artifact_id: str,
    content: str | None,
    edits: list[ArtifactEdit] | None,
) -> str:
    scope = _scope(registry)
    if scope is None:
        return NO_SESSION
    update_cb = registry._turn_callback("update_artifact")
    if not update_cb:
        return f"Artifact {artifact_id} could not be updated: no artifact store is connected."
    try:
        text = await _new_text(registry, scope, artifact_id, content, edits)
    except (ValueError, LookupError) as err:
        return f"Error: {err}"
    updated = await update_cb(scope[0], artifact_id, scope[1], text)
    if not updated:
        return f"Error: Artifact '{artifact_id}' not found."
    return (
        f"Artifact '{updated.title}' updated successfully.\n"
        f"- Artifact ID: {updated.id}\n"
        f"- Version: {updated.version}\n"
        f"- New length: {len(updated.content):,} characters" + _cut_notice(text, updated.content)
    )


async def _read(registry: ToolRegistry, artifact_id: str) -> str:
    scope = _scope(registry)
    if scope is None:
        return NO_SESSION
    get_cb = registry._turn_callback("get_artifact")
    if not get_cb:
        return f"Artifact {artifact_id} content unavailable."
    art = await get_cb(scope[0], artifact_id, scope[1])
    if not art:
        return f"Error: Artifact '{artifact_id}' not found."
    return f"=== Artifact: {art.title} ({art.filename}, v{art.version}) ===\n\n{art.content}"


async def _list(registry: ToolRegistry) -> str:
    scope = _scope(registry)
    if scope is None:
        return NO_SESSION
    list_cb = registry._turn_callback("list_artifacts")
    if not list_cb:
        return "No artifacts."
    artifacts = await list_cb(scope[0], scope[1])
    if not artifacts:
        return "No artifacts have been created in this session yet."
    lines = ["Session Artifacts:"]
    for art in artifacts:
        lines.append(
            f"- [{art.artifact_type}] {art.title} (ID: {art.id}, file: {art.filename}, v{art.version})"
        )
    return "\n".join(lines)


def _register_artifact_tools(registry: ToolRegistry) -> None:
    @registry._tool(
        description=(
            "Create a durable session artifact (specifications, architecture documents, migration "
            "plans, diff reports, diagrams, or JSON deliverables). Artifacts are stored with the "
            "session, readable by the user in the Cockpit Artifacts drawer independently of "
            "sandbox pod lifecycles, and every version is kept. The content you write travels in "
            "this call, so it stays in your context like any other tool argument."
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
            Field(
                description="How it is shown: markdown (default), diff (unified diff), diagram "
                "(Mermaid source), html (shown in a sandboxed frame) or json."
            ),
        ] = "markdown",
    ) -> str:
        return await _create(registry, title, filename, content, artifact_type)

    @registry._tool(
        description=(
            "Update an existing session artifact, saving a new version. Send either the whole "
            "new `content`, or `edits`: exact replacements applied to the current text (cheaper "
            "for a small change to a long document)."
        )
    )
    async def update_artifact(
        artifact_id: Annotated[str, Field(description="The unique artifact ID to update.")],
        content: Annotated[
            str | None, Field(description="The complete new text/code content.")
        ] = None,
        edits: Annotated[
            list[ArtifactEdit] | None,
            Field(description="Exact replacements to make instead of sending the whole text."),
        ] = None,
    ) -> str:
        return await _update(registry, artifact_id, content, edits)

    @registry._tool(
        description="Retrieve the content of a previously created session artifact by its ID."
    )
    async def read_artifact(
        artifact_id: Annotated[str, Field(description="The artifact ID to retrieve.")],
    ) -> str:
        return await _read(registry, artifact_id)

    @registry._tool(description="List all durable artifacts created in the current session.")
    async def list_artifacts() -> str:
        return await _list(registry)

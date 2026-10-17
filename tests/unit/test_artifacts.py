"""Tests for session deliverable artifacts."""

from __future__ import annotations

import pytest
from agent_core.tools.registry import ToolRegistry
from api.services.artifacts import InMemoryArtifactStore
from sandbox_driver.fake_driver import FakeSandboxDriver

from specifications.interfaces.session import SessionArtifact


@pytest.mark.asyncio
async def test_in_memory_artifact_store_crud_and_tenancy() -> None:
    store = InMemoryArtifactStore()

    art1 = SessionArtifact(
        id="art_1",
        session_id="sess_1",
        tenant_org_id="org_a",
        title="Migration Plan",
        filename="plan.md",
        artifact_type="markdown",
        content="# Plan",
    )
    saved = await store.create_artifact(art1)
    assert saved.id == "art_1"
    assert saved.version == 1

    # Read back
    read = await store.get_artifact("sess_1", "art_1", "org_a")
    assert read is not None
    assert read.title == "Migration Plan"
    assert read.content == "# Plan"

    # Tenant isolation
    other_tenant = await store.get_artifact("sess_1", "art_1", "org_b")
    assert other_tenant is None

    # Update artifact
    updated = await store.update_artifact("sess_1", "art_1", "org_a", "# Plan v2")
    assert updated is not None
    assert updated.version == 2
    assert updated.content == "# Plan v2"

    # List artifacts
    items = await store.list_artifacts("sess_1", "org_a")
    assert len(items) == 1
    assert items[0].id == "art_1"

    # Different session
    items_empty = await store.list_artifacts("sess_2", "org_a")
    assert len(items_empty) == 0


@pytest.mark.asyncio
async def test_artifact_tools_registered_and_callable() -> None:
    driver = FakeSandboxDriver()
    registry = ToolRegistry(driver=driver, session_id="sess_tools")
    tool_names = {t.name for t in registry.get_tool_definitions()}

    assert "create_artifact" in tool_names
    assert "update_artifact" in tool_names
    assert "read_artifact" in tool_names
    assert "list_artifacts" in tool_names

    # Mock callbacks
    created: list[SessionArtifact] = []

    async def _mock_create(art: SessionArtifact) -> SessionArtifact:
        created.append(art)
        return art

    registry._on_create_artifact_callback = _mock_create

    res = await registry.call_tool(
        "create_artifact",
        {
            "title": "Design Doc",
            "filename": "design.md",
            "content": "Architecture overview",
            "artifact_type": "markdown",
        },
    )
    assert "created successfully" in res
    assert len(created) == 1
    assert created[0].title == "Design Doc"

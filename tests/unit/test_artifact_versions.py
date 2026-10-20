"""Artifacts keep every version, report cuts honestly, and update by exact edits."""

from __future__ import annotations

from typing import Any

import pytest
from agent_core.tools.registry import ToolRegistry
from api.main import app
from api.services import artifacts as artifact_service
from api.services.artifacts import InMemoryArtifactStore
from fastapi.testclient import TestClient
from sandbox_driver.fake_driver import FakeSandboxDriver

from specifications.interfaces.session import SessionArtifact

OWNER = {"X-Tenant-Org-Id": "test_org", "X-Tenant-User-Id": "art_owner", "X-User-Role": "developer"}
STRANGER = {**OWNER, "X-Tenant-User-Id": "art_stranger"}


def _artifact(content: str = "v1") -> SessionArtifact:
    return SessionArtifact(
        id="art_1",
        session_id="s1",
        tenant_org_id="o1",
        title="Plan",
        filename="plan.md",
        content=content,
    )


async def test_every_version_is_kept_and_readable() -> None:
    store = InMemoryArtifactStore()
    await store.create_artifact(_artifact("first"))
    await store.update_artifact("s1", "art_1", "o1", "second")
    await store.update_artifact("s1", "art_1", "o1", "third")

    versions = await store.list_versions("s1", "art_1", "o1")
    assert [(v.version, v.size, v.content) for v in versions] == [
        (1, 5, None),
        (2, 6, None),
        (3, 5, None),
    ]
    first = await store.get_version("s1", "art_1", "o1", 1)
    assert first is not None and first.content == "first"
    assert await store.get_version("s1", "art_1", "o1", 9) is None
    assert await store.list_versions("s1", "art_1", "other-org") == []


async def test_deleting_a_sessions_artifacts_deletes_their_versions() -> None:
    store = InMemoryArtifactStore()
    await store.create_artifact(_artifact())

    await store.delete_artifacts("s1", "o1")

    assert await store.list_versions("s1", "art_1", "o1") == []


@pytest.fixture
def registry() -> tuple[ToolRegistry, InMemoryArtifactStore]:
    store = InMemoryArtifactStore()
    registry = ToolRegistry(driver=FakeSandboxDriver(), session_id="s1")

    async def create(artifact: SessionArtifact) -> SessionArtifact:
        return await store.create_artifact(artifact)

    async def update(session: str, artifact_id: str, org: str, content: str) -> Any:
        return await store.update_artifact(session, artifact_id, org, content)

    async def get(session: str, artifact_id: str, org: str) -> Any:
        return await store.get_artifact(session, artifact_id, org)

    registry._on_create_artifact_callback = create  # type: ignore[attr-defined]
    registry._on_update_artifact_callback = update  # type: ignore[attr-defined]
    registry._on_get_artifact_callback = get  # type: ignore[attr-defined]
    return registry, store


async def _create(registry: ToolRegistry, content: str = "alpha\nbeta\ngamma\n") -> str:
    result = await registry.call_tool(
        "create_artifact", {"title": "Plan", "filename": "plan.md", "content": content}
    )
    return next(line for line in result.splitlines() if "Artifact ID" in line).split(": ")[1]


async def test_an_update_can_be_exact_edits_instead_of_the_whole_text(
    registry: tuple[ToolRegistry, InMemoryArtifactStore],
) -> None:
    tools, store = registry
    artifact_id = await _create(tools)

    result = await tools.call_tool(
        "update_artifact",
        {"artifact_id": artifact_id, "edits": [{"old_text": "beta", "new_text": "BETA"}]},
    )

    assert "Version: 2" in result
    saved = await store.get_artifact("s1", artifact_id, "default_org")
    assert saved is not None and saved.content == "alpha\nBETA\ngamma\n"


@pytest.mark.parametrize(
    "old_text, problem",
    [("missing", "was not found"), ("a", "matches more than once")],
)
async def test_an_edit_that_does_not_match_exactly_once_changes_nothing(
    registry: tuple[ToolRegistry, InMemoryArtifactStore], old_text: str, problem: str
) -> None:
    tools, store = registry
    artifact_id = await _create(tools)

    result = await tools.call_tool(
        "update_artifact",
        {"artifact_id": artifact_id, "edits": [{"old_text": old_text, "new_text": "x"}]},
    )

    assert result.startswith("Error:") and problem in result
    saved = await store.get_artifact("s1", artifact_id, "default_org")
    assert saved is not None and saved.version == 1


async def test_an_update_needs_content_or_edits_but_not_both(
    registry: tuple[ToolRegistry, InMemoryArtifactStore],
) -> None:
    tools, _ = registry
    artifact_id = await _create(tools)

    neither = await tools.call_tool("update_artifact", {"artifact_id": artifact_id})
    both = await tools.call_tool(
        "update_artifact",
        {"artifact_id": artifact_id, "content": "x", "edits": [{"old_text": "a", "new_text": "b"}]},
    )

    assert neither.startswith("Error:") and both.startswith("Error:")


async def test_content_past_the_limit_is_cut_and_the_agent_is_told(
    registry: tuple[ToolRegistry, InMemoryArtifactStore], monkeypatch: pytest.MonkeyPatch
) -> None:
    tools, _ = registry
    monkeypatch.setattr(artifact_service, "MAX_ARTIFACT_CONTENT_CHARS", 100)

    result = await tools.call_tool(
        "create_artifact", {"title": "Big", "filename": "big.md", "content": "x" * 250}
    )

    assert "only the first 100 of 250 characters were kept" in result


async def test_a_call_outside_any_session_is_refused_not_filed_under_a_default() -> None:
    tools = ToolRegistry(driver=FakeSandboxDriver())

    result = await tools.call_tool(
        "create_artifact", {"title": "T", "filename": "t.md", "content": "c"}
    )

    assert result.startswith("Error:") and "session" in result


def test_the_versions_routes_list_versions_and_read_an_earlier_one(client: TestClient) -> None:
    import asyncio

    created = client.post("/v1/sessions", json={"title": "art", "is_shared": False}, headers=OWNER)
    session_id = created.json()["session_id"]
    store = artifact_service.get_artifact_store(getattr(app.state, "db_manager", None))

    async def seed() -> None:
        await store.create_artifact(
            SessionArtifact(
                id="art_route",
                session_id=session_id,
                tenant_org_id="test_org",
                title="Route",
                filename="r.md",
                content="old",
            )
        )
        await store.update_artifact(session_id, "art_route", "test_org", "new")

    asyncio.run(seed())

    versions = client.get(
        f"/v1/sessions/{session_id}/artifacts/art_route/versions", headers=OWNER
    ).json()
    current = client.get(f"/v1/sessions/{session_id}/artifacts/art_route", headers=OWNER).json()
    earlier = client.get(
        f"/v1/sessions/{session_id}/artifacts/art_route?version=1", headers=OWNER
    ).json()
    missing = client.get(f"/v1/sessions/{session_id}/artifacts/art_route?version=7", headers=OWNER)
    denied = client.get(f"/v1/sessions/{session_id}/artifacts/art_route/versions", headers=STRANGER)

    assert [v["version"] for v in versions] == [1, 2]
    assert (current["version"], current["content"]) == (2, "new")
    assert (earlier["version"], earlier["content"]) == (1, "old")
    assert missing.status_code == 404
    assert denied.status_code == 403

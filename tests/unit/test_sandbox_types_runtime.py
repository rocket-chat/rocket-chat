"""Sandbox types choose an operator profile and resources; idle workspaces are released."""

from __future__ import annotations

import json
import time
from typing import Any

import pytest
from api.main import app
from api.models import AgentPersonaModel
from api.services.personas import PersonaError, save_persona
from api.services.sandbox_catalogue import CatalogueError, SandboxCatalogue
from api.services.settings import save_org_config
from api.services.workspace_release import (
    DAY_SECONDS,
    RELEASED_AT,
    IdleWorkspace,
    delay_resolver,
    release_idle_workspaces,
)
from api.store import InMemorySessionStore
from fastapi.testclient import TestClient
from sandbox_driver.fake_driver import FakeSandboxDriver
from sandbox_driver.profiles import PROFILES_ENV
from turn_harness import run_turn

from specifications.interfaces.sandbox import SandboxStatus, WorkspaceSpec
from specifications.interfaces.session import SessionRecord

PROFILES = {"gpu": {"description": "GPU pool", "nodeSelector": {"pool": "gpu"}}}


@pytest.fixture(autouse=True)
def profiles(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(PROFILES_ENV, json.dumps(PROFILES))


async def _workspace(driver: FakeSandboxDriver, store: InMemorySessionStore, sid: str) -> None:
    await store.create_session(
        SessionRecord(session_id=sid, tenant_org_id="org_r", tenant_user_id="u", title=sid)
    )
    await driver.ensure_workspace(
        WorkspaceSpec(
            session_id=sid, tenant_org_id="org_r", tenant_user_id="u", container_image="i"
        )
    )


async def test_an_idle_workspace_is_released_and_a_busy_or_recent_one_kept() -> None:
    driver, store, now = FakeSandboxDriver(), InMemorySessionStore(), time.time()
    for sid in ("idle", "recent", "running", "forever"):
        await _workspace(driver, store, sid)
    await driver.start_sandbox("running")

    async def delay(workspace: IdleWorkspace) -> int:
        return 0 if workspace.session_id == "forever" else 7

    old = int(now - 8 * DAY_SECONDS)
    candidates = [
        IdleWorkspace("idle", "org_r", old, None, None),
        IdleWorkspace("recent", "org_r", int(now - DAY_SECONDS), None, None),
        IdleWorkspace("running", "org_r", old, None, None),
        IdleWorkspace("forever", "org_r", old, None, None),
    ]

    released = await release_idle_workspaces(candidates, driver, store, delay, now=now)

    assert released == ["idle"]
    assert await driver.get_status("idle") != SandboxStatus.HIBERNATED
    record = await store.get_session("idle")
    assert record is not None and record.sandbox_status == SandboxStatus.NON_EXISTENT
    assert record.metadata[RELEASED_AT] == int(now)
    assert await driver.get_status("running") == SandboxStatus.RUNNING


@pytest.mark.setting("sandboxes.workspace_release_days")
async def test_the_delay_is_the_types_own_else_the_organizations() -> None:
    org = "org_release_delay"
    await save_org_config(None, org, "sandboxes", {"workspace_release_days": 3}, [])
    catalogue = SandboxCatalogue(None)
    short = await catalogue.create(org, name="Short", image="img/short:1", release_after_days=1)
    await catalogue.create(org, name="Plain", image="img/plain:1")
    delay = delay_resolver(None)

    assert await delay(IdleWorkspace("a", org, 0, short.id, None)) == 1
    assert await delay(IdleWorkspace("b", org, 0, None, "img/plain:1")) == 3


async def test_the_next_turn_is_told_once_that_its_workspace_was_released() -> None:
    store = InMemorySessionStore()
    first = await run_turn(
        "org_released_note",
        store=store,
        metadata={RELEASED_AT: 1_700_000_000, "workspace_released_after_days": 7},
    )
    second = await run_turn("org_released_note", store=store)

    def dynamic(outcome: Any) -> str:
        request = next(r for r in outcome.model.requests if r.tools)
        # The volatile part rides in a reminder after the user's message, not the system prompt.
        return "\n".join(m.content or "" for m in request.messages)

    assert "workspace was released after 7 idle day(s)" in dynamic(first)
    assert "workspace was released" not in dynamic(second)


async def test_a_types_profile_and_resources_reach_the_sandbox() -> None:
    org = "org_type_runtime"
    entry = await SandboxCatalogue(None).create(
        org,
        name="GPU",
        image="img/gpu:1",
        profile="gpu",
        cpu_limit="6",
        memory_limit="24Gi",
        disk_size="80Gi",
    )
    driver = FakeSandboxDriver()

    await run_turn(
        org,
        metadata={"sandbox_id": entry.id},
        script=[("bash_exec", {"command": "nproc"})],
        driver=driver,
    )

    spec = driver._specs[f"session_{org}"]
    assert spec.profile == "gpu" and spec.container_image == "img/gpu:1"
    assert (spec.resources.cpu_cores, spec.resources.memory_limit) == (6.0, "24Gi")
    assert spec.resources.disk_size == "80Gi"


@pytest.mark.parametrize(
    ("fields", "message"),
    [
        ({"profile": "nope"}, "Unknown sandbox profile"),
        ({"cpu_limit": "lots"}, "CPU is a number"),
        ({"memory_limit": "4 GB"}, "Memory must be a size"),
        ({"release_after_days": 400}, "Release after"),
    ],
)
async def test_invalid_type_settings_are_refused(fields: dict[str, Any], message: str) -> None:
    with pytest.raises(CatalogueError, match=message):
        await SandboxCatalogue(None).create("org_type_bad", name="Bad", image="img/x:1", **fields)


def test_only_administrators_list_the_operators_profiles() -> None:
    with TestClient(app) as client:
        admin = client.get(
            "/v1/admin/sandboxes/profiles",
            headers={"X-Tenant-Org-Id": "o", "X-Tenant-User-Id": "a", "X-User-Role": "admin"},
        )
        member = client.get(
            "/v1/admin/sandboxes/profiles",
            headers={"X-Tenant-Org-Id": "o", "X-Tenant-User-Id": "m", "X-User-Role": "developer"},
        )

    assert admin.json() == [{"name": "gpu", "description": "GPU pool"}]
    assert member.status_code == 403


async def _agent_on(org: str, sandbox_id: str) -> str:
    persona = AgentPersonaModel(
        id=f"persona-{org}",
        name="Backend",
        role_title="Backend",
        system_prompt="You build backends.",
        model="m/model",
        whitelisted_tools=["bash_exec"],
        sandbox_id=sandbox_id,
    )
    return (await save_persona(None, org, "admin", True, persona)).id


async def _sandbox_of(org: str, agent_id: str, **metadata: Any) -> WorkspaceSpec:
    driver = FakeSandboxDriver()
    await run_turn(
        org,
        metadata={"agent_id": agent_id, **metadata},
        script=[("bash_exec", {"command": "nproc"})],
        driver=driver,
    )
    return driver._specs[f"session_{org}"]


async def test_an_agents_sandbox_type_is_used_when_the_session_names_none() -> None:
    org = "org_agent_type"
    catalogue = SandboxCatalogue(None)
    await catalogue.create(org, name="Default", image="img/base:1", is_default=True)
    fast = await catalogue.create(org, name="Fast", image="img/base:1", profile="gpu")

    spec = await _sandbox_of(org, await _agent_on(org, fast.id))

    # Same image as the default: the type comes from the agent, not from matching the image.
    assert (spec.container_image, spec.profile) == ("img/base:1", "gpu")


async def test_a_sandbox_picked_for_the_session_wins_over_the_agents() -> None:
    org = "org_agent_type_picked"
    catalogue = SandboxCatalogue(None)
    plain = await catalogue.create(org, name="Plain", image="img/plain:1", is_default=True)
    fast = await catalogue.create(org, name="Fast", image="img/fast:1", profile="gpu")

    spec = await _sandbox_of(org, await _agent_on(org, fast.id), sandbox_id=plain.id)

    assert (spec.container_image, spec.profile) == ("img/plain:1", "")


async def test_an_agent_cannot_name_a_type_outside_the_catalogue() -> None:
    with pytest.raises(PersonaError, match="not in the organization's catalogue"):
        await _agent_on("org_agent_type_bad", "sbx_missing")


async def test_an_agent_whose_type_was_deleted_uses_the_default() -> None:
    org = "org_agent_type_gone"
    catalogue = SandboxCatalogue(None)
    await catalogue.create(org, name="Default", image="img/base:1", is_default=True)
    gone = await catalogue.create(org, name="Gone", image="img/gone:1", profile="gpu")
    agent_id = await _agent_on(org, gone.id)
    await catalogue.delete(org, gone.id)

    spec = await _sandbox_of(org, agent_id)

    assert (spec.container_image, spec.profile) == ("img/base:1", "")

"""Per-organization running-sandbox quota."""

from __future__ import annotations

import pytest
from api.services.sandbox_quota import SandboxQuotaExceededError, enforce_running_sandbox_quota
from api.store import InMemorySessionStore
from sandbox_driver.fake_driver import FakeSandboxDriver

from specifications.interfaces.sandbox import SandboxStatus, WorkspaceSpec
from specifications.interfaces.session import SessionRecord


async def _running_session(
    store: InMemorySessionStore, driver: FakeSandboxDriver, session_id: str, org: str = "org-1"
) -> None:
    await store.create_session(
        SessionRecord(
            session_id=session_id,
            tenant_org_id=org,
            tenant_user_id="user-1",
            title=session_id,
            sandbox_status=SandboxStatus.RUNNING,
        )
    )
    await driver.ensure_workspace(
        WorkspaceSpec(
            session_id=session_id,
            tenant_org_id=org,
            tenant_user_id="user-1",
            container_image="fake:latest",
        )
    )
    await driver.start_sandbox(session_id)


@pytest.mark.asyncio
async def test_zero_means_unlimited() -> None:
    store, driver = InMemorySessionStore(), FakeSandboxDriver()
    await _running_session(store, driver, "a")
    await enforce_running_sandbox_quota(store, driver, org_id="org-1", session_id="b", limit=0)


@pytest.mark.asyncio
async def test_a_full_organization_is_refused() -> None:
    store, driver = InMemorySessionStore(), FakeSandboxDriver()
    await _running_session(store, driver, "a")
    await _running_session(store, driver, "b")

    with pytest.raises(SandboxQuotaExceededError, match="max_running_sandboxes"):
        await enforce_running_sandbox_quota(store, driver, org_id="org-1", session_id="c", limit=2)
    await enforce_running_sandbox_quota(store, driver, org_id="org-1", session_id="c", limit=3)


@pytest.mark.asyncio
async def test_the_session_being_started_and_other_orgs_do_not_count() -> None:
    store, driver = InMemorySessionStore(), FakeSandboxDriver()
    await _running_session(store, driver, "a")
    await _running_session(store, driver, "other", org="org-2")

    await enforce_running_sandbox_quota(store, driver, org_id="org-1", session_id="a", limit=1)


@pytest.mark.asyncio
async def test_a_hibernated_sandbox_with_a_stale_record_is_not_counted() -> None:
    store, driver = InMemorySessionStore(), FakeSandboxDriver()
    await _running_session(store, driver, "a")
    await driver.hibernate_sandbox("a")

    await enforce_running_sandbox_quota(store, driver, org_id="org-1", session_id="b", limit=1)

"""Durable session files: bounds, offloaded outputs without a sandbox, and the sandbox copy."""

from __future__ import annotations

import pytest
from agent_core.context import ExecutionContext, current_execution_ctx
from agent_core.tools.offload import get_cached_offload, offload_tool_output
from api.services.session_files import (
    BoundSessionFiles,
    InMemorySessionFileStore,
    SessionFileLimits,
    SessionFilePathError,
    materialise_session_files,
    normalise_session_path,
)
from sandbox_driver.fake_driver import FakeSandboxDriver

from specifications.interfaces.agent import TurnCallbacks
from specifications.interfaces.sandbox import SandboxStatus

pytestmark = pytest.mark.asyncio


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ("/workspace/.rocket/outputs/1_a.txt", ".rocket/outputs/1_a.txt"),
        ("workspace/notes/plan.md", "notes/plan.md"),
        ("./notes//plan.md", "notes/plan.md"),
        ("notes/plan.md", "notes/plan.md"),
    ],
)
async def test_paths_are_workspace_relative(given: str, expected: str) -> None:
    assert normalise_session_path(given) == expected


@pytest.mark.parametrize("bad", ["", "/", "../etc/passwd", "notes/../../x", "/workspace/"])
async def test_paths_that_leave_the_workspace_are_refused(bad: str) -> None:
    with pytest.raises(SessionFilePathError):
        normalise_session_path(bad)


async def test_one_large_file_is_cut_to_the_file_ceiling_keeping_head_and_tail() -> None:
    store = InMemorySessionFileStore(
        SessionFileLimits(max_session_bytes=10_000, max_file_bytes=1_000)
    )
    content = "HEAD" + "x" * 50_000 + "TAIL"

    written = await store.put("s", "org", "big.txt", content)

    kept = await store.get("s", "org", "big.txt")
    assert kept is not None and written.truncated
    assert kept.size <= 1_000 + 200  # the marker line is the only overhead
    assert kept.content.startswith("HEAD") and kept.content.endswith("TAIL")
    assert "characters cut" in kept.content


async def test_a_session_over_its_ceiling_evicts_its_oldest_files_never_the_new_one() -> None:
    store = InMemorySessionFileStore(
        SessionFileLimits(max_session_bytes=2_500, max_file_bytes=1_000)
    )
    await store.put("s", "org", "a", "y" * 1_000)
    await store.put("s", "org", "b", "y" * 1_000)
    assert (await store.put("s", "org", "c", "y" * 1_000)).evicted == ("a",)
    await store.put("other", "org", "kept", "z" * 1_000)

    written = await store.put("s", "org", "d", "w" * 1_000)

    assert written.evicted == ("b",)
    assert [f.path for f in await store.list_files("s", "org")] == ["d", "c"]
    assert await store.get("other", "org", "kept") is not None


async def test_another_organization_cannot_read_or_delete_a_file() -> None:
    store = InMemorySessionFileStore()
    await store.put("s", "org_a", "secret.txt", "a")

    assert await store.get("s", "org_b", "secret.txt") is None
    assert await store.list_files("s", "org_b") == []
    assert not await store.delete("s", "org_b", "secret.txt")
    assert await store.get("s", "org_a", "secret.txt") is not None


async def test_an_offloaded_output_goes_to_the_session_store_and_starts_no_sandbox() -> None:
    store = InMemorySessionFileStore()
    files = BoundSessionFiles(store, "s_store", "org")
    driver = FakeSandboxDriver()
    output = "\n".join(f"row {i}" for i in range(2_000))

    excerpt = await offload_tool_output(
        output, "s_store", "call_1", threshold=500, driver=driver, session_files=files
    )

    kept = await files.get("/workspace/.rocket/outputs/1_call_1.txt")
    assert kept is not None and kept.content == output
    assert "Full output saved to /workspace/.rocket/outputs/1_call_1.txt" in excerpt
    assert get_cached_offload("s_store", ".rocket/outputs/1_call_1.txt") is None
    assert await driver.get_status("s_store") == SandboxStatus.NON_EXISTENT


async def test_file_read_reads_a_stored_output_without_a_sandbox() -> None:
    from agent_core.tools.builtin.files import register
    from agent_core.tools.registry import ToolRegistry

    store = InMemorySessionFileStore()
    files = BoundSessionFiles(store, "s_read", "org")
    await files.put(".rocket/outputs/2_c.txt", "\n".join(f"item {i}" for i in range(300)))
    registry = ToolRegistry(driver=None, session_id="s_read")
    register(registry)
    ctx = ExecutionContext(
        session_id="s_read",
        event_queue=None,
        parent_model="m",
        callbacks=TurnCallbacks(session_files=files),
    )
    token = current_execution_ctx.set(ctx)
    try:
        content = await registry.call_tool(
            "file_read",
            {"path": "/workspace/.rocket/outputs/2_c.txt", "start_line": 10, "end_line": 12},
        )
    finally:
        current_execution_ctx.reset(token)

    assert "item 9" in content and "item 11" in content
    assert "Sandbox not initialized" not in content


async def test_a_sandbox_that_starts_later_receives_the_session_files() -> None:
    store = InMemorySessionFileStore()
    await store.put("s_late", "org", ".rocket/outputs/1_q.txt", "rows")
    await store.put("s_late", "org", "notes/plan.md", "# plan", kind="scratch")
    driver = FakeSandboxDriver()
    await driver.start_sandbox("s_late")
    await driver.write_file("s_late", "notes/plan.md", "# newer in the sandbox")

    written = await materialise_session_files(store, driver, "s_late", "org")

    assert written == [".rocket/outputs/1_q.txt"]
    assert await driver.read_file("s_late", ".rocket/outputs/1_q.txt") == "rows"
    assert await driver.read_file("s_late", "notes/plan.md") == "# newer in the sandbox"


@pytest.mark.setting("general.session_files_max_mb")
async def test_the_session_ceiling_follows_the_organization_setting() -> None:
    from api.services.session_files import turn_session_file_store
    from api.services.settings import save_org_config

    org = "wire_session_files"
    await save_org_config(None, org, "general", {"session_files_max_mb": 1}, [])

    store = await turn_session_file_store(None, org)
    for index in range(6):
        await store.put("s_cap", org, f"f{index}", "k" * 60_000)
    await store.put("s_cap", org, "last", "k" * 900_000)

    kept = await store.list_files("s_cap", org)
    assert sum(f.size for f in kept) <= 1024 * 1024
    assert kept[0].path == "last"
    # A file is at most a sixteenth of the session ceiling.
    assert kept[0].size <= (1024 * 1024) // 16 + 200

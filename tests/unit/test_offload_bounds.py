"""Offloaded tool output is bounded in memory, in the excerpt and on disk."""

from __future__ import annotations

import pytest
from agent_core.tools import offload
from sandbox_driver.fake_driver import FakeSandboxDriver


@pytest.fixture(autouse=True)
def _clean_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(offload, "_OFFLOAD_CACHE", offload.OrderedDict())
    monkeypatch.setattr(offload, "_cache_bytes", 0)
    monkeypatch.setattr(offload, "_cleaned_sessions", set())


def test_a_session_cannot_fill_the_cache_for_everyone(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(offload, "MAX_SESSION_CACHE_BYTES", 100)
    monkeypatch.setattr(offload, "MAX_CACHE_BYTES", 1_000)

    offload.store_cached_offload("quiet", "a.txt", "q" * 50)
    for index in range(5):
        offload.store_cached_offload("busy", f"{index}.txt", "b" * 40)

    assert offload.get_cached_offload("quiet", "a.txt") == "q" * 50
    held = [offload.get_cached_offload("busy", f"{i}.txt") for i in range(5)]
    assert sum(1 for entry in held if entry) == 2, "only what fits the session's budget"
    assert held[-1], "the newest is kept"


def test_the_whole_cache_is_bounded_in_bytes_least_recently_used_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(offload, "MAX_SESSION_CACHE_BYTES", 100)
    monkeypatch.setattr(offload, "MAX_CACHE_BYTES", 150)

    for name in ("a", "b", "c"):
        offload.store_cached_offload(name, "o.txt", name * 60)
        offload.get_cached_offload("a", "o.txt")  # "a" stays in use

    assert offload.get_cached_offload("a", "o.txt")
    assert offload.get_cached_offload("b", "o.txt") is None
    assert offload.get_cached_offload("c", "o.txt")
    assert offload._cache_bytes <= 150


def test_an_output_larger_than_a_sessions_budget_is_not_cached(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(offload, "MAX_SESSION_CACHE_BYTES", 10)

    offload.store_cached_offload("s", "big.txt", "x" * 11)

    assert offload.get_cached_offload("s", "big.txt") is None


def test_forgetting_a_session_releases_its_bytes() -> None:
    offload.store_cached_offload("gone", "a.txt", "x" * 20)
    offload.forget_session_offloads("gone")

    assert offload.get_cached_offload("gone", "a.txt") is None
    assert offload._cache_bytes == 0


async def test_very_long_lines_do_not_defeat_the_excerpt() -> None:
    blob = "".join(f"{'x' * 5_000}\n" for _ in range(80))

    shown = await offload.offload_tool_output(blob, "s", "c1", threshold=6_000)

    assert len(shown) <= 6_000 * offload.EXCERPT_SLACK + 500
    assert "Full output saved to /workspace/.rocket/outputs/1_c1.txt" in shown


async def test_a_normal_output_keeps_its_head_and_tail_lines() -> None:
    text = "".join(f"line {i:03d} {'.' * 60}\n" for i in range(300))

    shown = await offload.offload_tool_output(text, "s", "c2", threshold=6_000)

    assert "line 000" in shown and "line 299" in shown and "line 150" not in shown
    assert "lines of 300 omitted" in shown


async def test_old_outputs_are_removed_once_per_session() -> None:
    driver = FakeSandboxDriver()
    await driver.ensure_workspace(_spec())
    await driver.start_sandbox("s9")

    for call in ("a", "b"):
        await offload.offload_tool_output("y" * 7_000, "s9", call, threshold=1_000, driver=driver)

    cleanups = [c for _, c in driver.exec_calls if ".rocket/outputs" in c and "-delete" in c]
    assert len(cleanups) == 1


def _spec():  # type: ignore[no-untyped-def]
    from specifications.interfaces.sandbox import WorkspaceSpec

    return WorkspaceSpec(
        session_id="s9", tenant_org_id="o", tenant_user_id="u", container_image="img"
    )

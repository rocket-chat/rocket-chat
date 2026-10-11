"""Docker SDK calls run on their own bounded pool."""

from __future__ import annotations

import asyncio
import contextvars
import threading
import time

import pytest
from sandbox_driver import blocking

marker: contextvars.ContextVar[str] = contextvars.ContextVar("marker", default="unset")


@pytest.fixture(autouse=True)
def _small_pool(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ROCKET_DOCKER_THREADS", "2")
    monkeypatch.setattr(blocking, "_pool", None)


async def test_caller_context_variables_reach_the_worker_thread() -> None:
    marker.set("tenant-a")
    assert await blocking.run_blocking(marker.get) == "tenant-a"


async def test_the_pool_is_dedicated_and_bounded() -> None:
    names: set[str] = set()
    release = threading.Event()

    def hold() -> None:
        names.add(threading.current_thread().name)
        release.wait(5)

    tasks = [asyncio.create_task(blocking.run_blocking(hold)) for _ in range(6)]
    await asyncio.sleep(0.2)
    assert blocking.blocking_calls_in_flight() == 6
    release.set()
    await asyncio.gather(*tasks)

    assert len(names) <= 2
    assert all(name.startswith("docker-sdk") for name in names)
    assert blocking.blocking_calls_in_flight() == 0


async def test_a_saturated_docker_pool_does_not_starve_the_default_executor() -> None:
    release = threading.Event()
    hogs = [asyncio.create_task(blocking.run_blocking(release.wait, 5)) for _ in range(8)]
    await asyncio.sleep(0.1)

    started = time.monotonic()
    await asyncio.to_thread(lambda: None)  # what DNS lookups and file I/O elsewhere use
    assert time.monotonic() - started < 0.5

    release.set()
    await asyncio.gather(*hogs)


async def test_errors_propagate_and_release_the_slot() -> None:
    def boom() -> None:
        raise RuntimeError("docker said no")

    with pytest.raises(RuntimeError, match="docker said no"):
        await blocking.run_blocking(boom)
    assert blocking.blocking_calls_in_flight() == 0

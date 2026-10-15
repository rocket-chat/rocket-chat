"""A pod stays protected from eviction while any command runs or background processes hold it."""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from kubernetes_asyncio.stream import ws_client
from sandbox_driver.k8s_driver import K8sSandboxDriver


def _driver(hold: asyncio.Event | None = None) -> tuple[K8sSandboxDriver, list[bool]]:
    driver = K8sSandboxDriver(namespace="ns", core_v1_api=MagicMock(), ws_core_v1_api=MagicMock())
    writes: list[bool] = []

    async def record(session_id: str, protected: bool) -> None:
        writes.append(protected)

    class _Conn:
        async def __aenter__(self) -> _Conn:
            return self

        async def __aexit__(self, *exc: object) -> None:
            return None

        async def send_bytes(self, data: bytes) -> None:
            return None

        async def __aiter__(self) -> Any:
            if hold is not None:
                await hold.wait()
            status = json.dumps({"status": "Success"}).encode()
            yield MagicMock(data=bytes([ws_client.ERROR_CHANNEL]) + status)

    ws = MagicMock()
    ws.connect_get_namespaced_pod_exec = AsyncMock(side_effect=lambda **_: _Conn())
    driver._ensure_clients = AsyncMock(return_value=(MagicMock(), ws))  # type: ignore[method-assign]
    driver._assert_pod_running = AsyncMock()  # type: ignore[method-assign]
    driver.set_eviction_protection = record  # type: ignore[method-assign]
    return driver, writes


async def test_overlapping_commands_protect_once_and_release_once() -> None:
    release = asyncio.Event()
    driver, writes = _driver(release)

    first = asyncio.create_task(driver.exec_command("s1", "sleep a"))
    second = asyncio.create_task(driver.exec_command("s1", "sleep b"))
    await asyncio.sleep(0.05)
    assert writes == [True], "protected once while both run"
    release.set()
    await asyncio.gather(first, second)

    assert writes == [True, False], "released once, after the last one ended"


async def test_background_processes_keep_the_pod_protected_until_their_hold_ends() -> None:
    driver, writes = _driver()

    driver.hold_awake("s1", 60)
    await asyncio.sleep(0)
    await driver.exec_command("s1", "true")
    assert writes == [True], "held: a finished command does not release the pod"

    driver.hold_awake("s1", 0)
    await asyncio.sleep(0)
    assert writes == [True, False]

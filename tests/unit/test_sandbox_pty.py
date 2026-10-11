"""PTY channels of the real drivers, against in-process stand-ins for the shell's transport."""

from __future__ import annotations

import asyncio
import json
import socket
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from sandbox_driver.pty import DockerPty, K8sPty


@pytest.mark.asyncio
async def test_docker_pty_moves_bytes_both_ways_over_the_exec_socket() -> None:
    shell_side, driver_side = socket.socketpair()
    driver_side.setblocking(False)
    client = MagicMock()
    pty = DockerPty(client, "exec-1", driver_side)

    await pty.write(b"ls -la\r")
    assert shell_side.recv(100) == b"ls -la\r"

    shell_side.sendall(b"total 0\r\n")
    assert await pty.read() == b"total 0\r\n"

    await pty.resize(120, 40)
    client.api.exec_resize.assert_called_once_with("exec-1", height=40, width=120)

    shell_side.close()
    assert await pty.read() == b"", "the shell going away is end-of-stream"
    await pty.close()


def _frame(channel: int, body: bytes = b"") -> SimpleNamespace:
    return SimpleNamespace(type=SimpleNamespace(name="BINARY"), data=bytes([channel]) + body)


@pytest.mark.asyncio
async def test_k8s_pty_speaks_the_channel_protocol() -> None:
    ws = MagicMock()
    ws.send_bytes = AsyncMock()
    ws.close = AsyncMock()
    ws.receive = AsyncMock(
        side_effect=[
            _frame(1, b""),  # empty output is skipped
            _frame(1, b"$ "),
            _frame(2, b"warning\r\n"),  # stderr shares the TTY
            SimpleNamespace(type=SimpleNamespace(name="CLOSED"), data=None),
        ]
    )
    pty = K8sPty(ws)

    assert await pty.read() == b"$ "
    assert await pty.read() == b"warning\r\n"
    assert await pty.read() == b"", "a closed socket ends the stream"

    await pty.write(b"echo hi\r")
    ws.send_bytes.assert_awaited_with(b"\x00echo hi\r")

    await pty.resize(100, 30)
    channel_frame = ws.send_bytes.await_args.args[0]
    assert channel_frame[:1] == b"\x04"
    assert json.loads(channel_frame[1:]) == {"Width": 100, "Height": 30}

    await pty.close()
    ws.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_k8s_pty_treats_the_error_channel_as_the_end() -> None:
    ws = MagicMock()
    ws.receive = AsyncMock(side_effect=[_frame(3, b'{"status":"Success"}')])

    assert await K8sPty(ws).read() == b""


@pytest.mark.asyncio
async def test_docker_pty_reports_a_dead_socket_as_eof() -> None:
    shell_side, driver_side = socket.socketpair()
    driver_side.setblocking(False)
    pty = DockerPty(MagicMock(), "x", driver_side)
    await pty.close()
    shell_side.close()

    assert await asyncio.wait_for(pty.read(), 2) == b""


@pytest.mark.asyncio
async def test_k8s_open_pty_awaits_the_connection_context_manager() -> None:
    """A real cluster's client returns an awaitable ``ws_connect`` manager, not the socket."""
    from sandbox_driver.k8s_driver import K8sSandboxDriver

    from specifications.interfaces.sandbox import SandboxStatus

    socket_stub = MagicMock()
    socket_stub.send_bytes = AsyncMock()

    class _ConnectManager:
        def __await__(self):  # type: ignore[no-untyped-def]
            async def _open() -> MagicMock:
                return socket_stub

            return _open().__await__()

    ws_core = MagicMock()
    ws_core.connect_get_namespaced_pod_exec = AsyncMock(return_value=_ConnectManager())
    driver = K8sSandboxDriver(namespace="ns", core_v1_api=MagicMock(), ws_core_v1_api=ws_core)
    driver.get_status = AsyncMock(return_value=SandboxStatus.RUNNING)  # type: ignore[method-assign]

    pty = await driver.open_pty("s1")
    await pty.write(b"x")

    socket_stub.send_bytes.assert_awaited_with(b"\x00x")


@pytest.mark.asyncio
async def test_k8s_driver_prefers_in_cluster_config_without_awaiting_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from kubernetes_asyncio import config
    from sandbox_driver.k8s_driver import K8sSandboxDriver

    incluster = MagicMock(return_value=None)
    kubeconfig = AsyncMock()
    monkeypatch.setattr(config, "load_incluster_config", incluster)
    monkeypatch.setattr(config, "load_kube_config", kubeconfig)

    core, ws_core = await K8sSandboxDriver(namespace="ns")._ensure_clients()
    await core.api_client.close()
    await ws_core.api_client.close()

    incluster.assert_called_once()
    kubeconfig.assert_not_awaited()


@pytest.mark.asyncio
async def test_k8s_driver_falls_back_to_kubeconfig_outside_a_cluster(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from kubernetes_asyncio import config
    from sandbox_driver.k8s_driver import K8sSandboxDriver

    monkeypatch.setattr(
        config, "load_incluster_config", MagicMock(side_effect=config.ConfigException("no"))
    )
    kubeconfig = AsyncMock()
    monkeypatch.setattr(config, "load_kube_config", kubeconfig)

    core, ws_core = await K8sSandboxDriver(namespace="ns")._ensure_clients()
    await core.api_client.close()
    await ws_core.api_client.close()

    kubeconfig.assert_awaited_once()

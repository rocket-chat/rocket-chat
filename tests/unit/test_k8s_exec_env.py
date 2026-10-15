"""The Kubernetes driver never puts a command's environment on the exec command line.

Anything in the pod can read ``/proc/*/cmdline``, and the API server records exec requests
(command included) in its audit log, so per-session secrets such as the git gateway capability
travel on the exec's stdin instead.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from kubernetes_asyncio.stream import ws_client
from sandbox_driver.k8s_driver import K8sSandboxDriver

SECRET = "eyJhbGciOiJIUzI1NiJ9.secret-capability"


def _driver() -> K8sSandboxDriver:
    return K8sSandboxDriver(namespace="ns", core_v1_api=MagicMock(), ws_core_v1_api=MagicMock())


def test_the_argv_carries_no_environment_value() -> None:
    driver = _driver()
    driver.set_session_env("s1", {"GIT_CONFIG_VALUE_3": f"X-Rocket-Git-Capability: {SECRET}"})

    argv, stdin = driver._shell_command("s1", "git fetch", "/workspace/app", 60, {"EXTRA": "v"})

    assert SECRET not in " ".join(argv) and "EXTRA=v" not in " ".join(argv)
    assert stdin is not None and stdin.endswith(b"__ROCKET_ENV_END__\n")


def test_without_an_environment_nothing_is_sent_on_stdin() -> None:
    argv, stdin = _driver()._shell_command("s1", "ls", "/workspace", 60, None)

    assert stdin is None and argv[-1].startswith("cd /workspace && ")


async def test_the_script_exports_the_environment_and_the_command_cannot_read_stdin() -> None:
    driver = _driver()
    driver.set_session_env("s1", {"TOKEN": SECRET, "MULTI": "line one\nline two", "bad key": "x"})
    command = 'printf "%s|%s" "$TOKEN" "$MULTI"; cat; echo "|done"'
    argv, stdin = driver._shell_command("s1", command, "/", 20, None)

    process = await asyncio.create_subprocess_exec(
        *argv,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    # The socket stays open after the environment, as an exec stream's stdin does.
    assert process.stdin is not None and stdin is not None
    process.stdin.write(stdin + b"more input the command must not see\n")
    await process.stdin.drain()
    stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=10)

    assert stdout.decode() == f"{SECRET}|line one\nline two|done\n", stderr.decode()


async def test_the_environment_is_written_to_the_exec_stdin_channel() -> None:
    driver = _driver()
    driver.set_session_env("s1", {"TOKEN": SECRET})
    sent: list[bytes] = []

    class _Conn:
        async def __aenter__(self) -> _Conn:
            return self

        async def __aexit__(self, *exc: object) -> None:
            return None

        async def send_bytes(self, data: bytes) -> None:
            sent.append(data)

        async def __aiter__(self) -> Any:
            status = json.dumps({"status": "Success"}).encode()
            yield MagicMock(data=bytes([ws_client.ERROR_CHANNEL]) + status)

    ws = MagicMock()
    ws.connect_get_namespaced_pod_exec = AsyncMock(return_value=_Conn())
    driver._clients = (MagicMock(), ws)  # type: ignore[assignment]
    driver._ensure_clients = AsyncMock(return_value=(MagicMock(), ws))  # type: ignore[method-assign]
    driver._assert_pod_running = AsyncMock()  # type: ignore[method-assign]
    driver.set_eviction_protection = AsyncMock()  # type: ignore[method-assign]

    await driver.exec_command("s1", "true")

    kwargs = ws.connect_get_namespaced_pod_exec.call_args.kwargs
    assert kwargs["stdin"] is True and SECRET not in " ".join(kwargs["command"])
    assert len(sent) == 1 and sent[0][:1] == b"\x00" and b"__ROCKET_ENV_END__" in sent[0]


async def test_a_terminal_loads_the_environment_from_a_private_file_not_its_argv() -> None:
    from specifications.interfaces.sandbox import ExecResult, SandboxStatus

    driver = _driver()
    driver.set_session_env("s1", {"TOKEN": SECRET})
    commands: list[str] = []

    async def exec_command(session_id: str, command: str, **_: object) -> ExecResult:
        commands.append(command)
        return ExecResult(0, "", "", 1)

    socket = MagicMock()

    class _Connect:
        def __await__(self):  # type: ignore[no-untyped-def]
            async def _open() -> MagicMock:
                return socket

            return _open().__await__()

    ws = MagicMock()
    ws.connect_get_namespaced_pod_exec = AsyncMock(return_value=_Connect())
    driver._ensure_clients = AsyncMock(return_value=(MagicMock(), ws))  # type: ignore[method-assign]
    driver.get_status = AsyncMock(return_value=SandboxStatus.RUNNING)  # type: ignore[method-assign]
    driver.exec_command = exec_command  # type: ignore[assignment,method-assign]
    socket.send_bytes = AsyncMock()

    await driver.open_pty("s1")

    argv = ws.connect_get_namespaced_pod_exec.call_args.kwargs["command"]
    assert SECRET not in " ".join(argv)
    assert commands and commands[0].startswith("umask 077 && export -p > /tmp/.rocket-env-")
    path = commands[0].rsplit(" ", 1)[-1]
    assert f". {path}" in argv[-1] and f"rm -f {path}" in argv[-1]

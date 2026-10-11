"""Background process tools, run against a real local shell (no sandbox needed)."""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path
from typing import Any

import pytest
from agent_core.tools import processes

from specifications.interfaces.sandbox import ExecResult


class _LocalShell:
    """Runs the tools' scripts in a real ``sh``, in a throwaway directory."""

    def __init__(self, root: Path) -> None:
        self.root = root

    async def exec_command(
        self, session_id: str, command: str, workdir: str = "/workspace", **kwargs: Any
    ) -> ExecResult:
        cwd = self.root if workdir == "/workspace" else workdir
        completed = await asyncio.to_thread(
            subprocess.run,
            ["sh", "-c", command],
            capture_output=True,
            text=True,
            cwd=cwd,
            timeout=kwargs.get("timeout_seconds", 60),
            check=False,
        )
        return ExecResult(completed.returncode, completed.stdout, completed.stderr, 1)


@pytest.fixture
def shell(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> _LocalShell:
    monkeypatch.setattr(processes, "PROC_ROOT", str(tmp_path / "proc"))
    monkeypatch.setattr(processes, "STOP_GRACE_SECONDS", 0)
    work = tmp_path / "work"
    work.mkdir()
    return _LocalShell(work)


def _id_from(message: str) -> str:
    return next(word for word in message.split() if processes.is_valid_id(word))


async def test_a_started_process_keeps_running_and_its_output_can_be_read(
    shell: _LocalShell,
) -> None:
    message = await processes.start_process(
        shell, "s", "echo ready; sleep 30", name="dev server", port=3000
    )
    process_id = _id_from(message)
    try:
        listed = await processes.list_processes(shell, "s")
        assert [(p.id, p.state, p.port, p.name) for p in listed] == [
            (process_id, "running", 3000, "dev server")
        ]
        assert "ready" in await processes.read_output(shell, "s", process_id)
    finally:
        await processes.stop_process(shell, "s", process_id)


async def test_waiting_returns_the_exit_code_and_the_last_output(shell: _LocalShell) -> None:
    started = await processes.start_process(shell, "s", "echo building; sleep 1; exit 3")
    process_id = _id_from(started)
    result = await processes.wait_for_process(shell, "s", process_id, timeout_seconds=20)
    assert "exited with code 3" in result and "building" in result


async def test_a_command_that_dies_at_once_is_reported_with_its_output(shell: _LocalShell) -> None:
    message = await processes.start_process(shell, "s", "echo boom >&2; exit 1")
    assert "ended immediately" in message and "boom" in message


async def test_stop_kills_the_whole_process_group(shell: _LocalShell, tmp_path: Path) -> None:
    child_marker = tmp_path / "child-alive"
    command = f"(while true; do touch {child_marker}; sleep 0.2; done) & wait"
    process_id = _id_from(await processes.start_process(shell, "s", command))
    await asyncio.sleep(1)
    assert child_marker.exists()

    outcome = await processes.stop_process(shell, "s", process_id)
    assert "stopped" in outcome

    child_marker.unlink()
    await asyncio.sleep(1)
    assert not child_marker.exists(), "the child of the shell survived the stop"


async def test_the_running_limit_is_enforced(shell: _LocalShell) -> None:
    first = _id_from(await processes.start_process(shell, "s", "sleep 30", max_running=1))
    try:
        refused = await processes.start_process(shell, "s", "sleep 30", max_running=1)
        assert refused.startswith("Error:") and "limit 1" in refused and first in refused
    finally:
        await processes.stop_process(shell, "s", first)


async def test_ids_and_inputs_are_validated(shell: _LocalShell) -> None:
    assert (await processes.read_output(shell, "s", "../etc")).startswith("Error:")
    assert (await processes.stop_process(shell, "s", "p1; rm -rf /")).startswith("Error:")
    assert (await processes.start_process(shell, "s", "  ")).startswith("Error:")
    assert (await processes.start_process(shell, "s", "true", port=70000)).startswith("Error:")


def test_the_parser_ignores_noise_and_unknown_states() -> None:
    output = "login banner\nROCKET_PROC\tp0a1b2c\tweird\t\t\tname\tcmd\nROCKET_PROC\tshort\n"
    parsed = processes._parse(output)
    assert [(p.id, p.state) for p in parsed] == [("p0a1b2c", "lost")]

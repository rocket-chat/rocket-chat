"""File I/O commands must run on minimal images: POSIX tools only, no interpreter."""

from __future__ import annotations

import base64
import subprocess
from pathlib import Path

import pytest
from sandbox_driver.utils import (
    WRITE_CHUNK_CHARS,
    build_list_files_command,
    build_read_file_command,
    build_write_file_commands,
    parse_list_files_output,
)


def _sh(command: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["sh", "-c", command], capture_output=True, text=True, check=False)


# Sandboxes are Linux (GNU coreutils or busybox); BSD/macOS `stat` has no `-c`.
requires_gnu_stat = pytest.mark.skipif(
    subprocess.run(["stat", "-c", "%s", "/"], capture_output=True, check=False).returncode != 0,
    reason="host stat lacks -c",
)


def test_commands_do_not_need_python() -> None:
    commands = [
        build_list_files_command("/workspace", 3),
        build_read_file_command("/workspace/a.txt"),
        *build_write_file_commands("/workspace/a.txt", b"x"),
    ]
    assert not any("python" in command for command in commands)


def test_write_then_read_round_trips_large_unicode_content(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "dir" / "file with space.txt"
    content = ("héllo wörld ✓\n" * 20_000).encode()
    commands = build_write_file_commands(str(target), content)
    assert len(commands) > 2, "large content is split into several bounded commands"
    assert all(len(command) < WRITE_CHUNK_CHARS + 500 for command in commands)
    for command in commands:
        assert _sh(command).returncode == 0

    read = _sh(build_read_file_command(str(target)))
    assert read.returncode == 0
    assert base64.b64decode(read.stdout) == content


def test_write_empty_content_truncates_existing_file(tmp_path: Path) -> None:
    target = tmp_path / "f.txt"
    target.write_text("old")
    for command in build_write_file_commands(str(target), b""):
        assert _sh(command).returncode == 0
    assert target.read_bytes() == b""


def test_read_missing_file_exits_2(tmp_path: Path) -> None:
    assert _sh(build_read_file_command(str(tmp_path / "nope"))).returncode == 2


@requires_gnu_stat
def test_list_files_respects_depth_and_reports_types(tmp_path: Path) -> None:
    (tmp_path / "a" / "b" / "c").mkdir(parents=True)
    (tmp_path / "top.txt").write_text("12345")
    (tmp_path / "a" / "b" / "deep.txt").write_text("x")

    out = _sh(build_list_files_command(str(tmp_path), 2))
    assert out.returncode == 0
    entries = {entry.path: entry for entry in parse_list_files_output(out.stdout)}
    assert str(tmp_path / "a" / "b") in entries
    assert str(tmp_path / "a" / "b" / "c") not in entries, "beyond max_depth"
    assert entries[str(tmp_path / "a")].is_dir
    assert entries[str(tmp_path / "top.txt")].size_bytes == 5
    assert not entries[str(tmp_path / "top.txt")].is_dir


@requires_gnu_stat
def test_list_files_on_a_single_file_and_a_missing_path(tmp_path: Path) -> None:
    single = tmp_path / "one.txt"
    single.write_text("abc")
    listed = parse_list_files_output(_sh(build_list_files_command(str(single), 3)).stdout)
    assert [entry.path for entry in listed] == [str(single)]

    missing = _sh(build_list_files_command(str(tmp_path / "gone"), 3))
    assert missing.returncode == 0 and parse_list_files_output(missing.stdout) == []


def test_deadline_wrapper_kills_a_hung_command_inside_the_shell() -> None:
    import time

    from sandbox_driver.utils import (
        DEADLINE_EXIT_CODES,
        KILLED_EXIT_CODE,
        hit_deadline,
        wrap_with_deadline,
    )

    if subprocess.run(["sh", "-c", "command -v timeout"], capture_output=True).returncode != 0:
        pytest.skip("host has no timeout(1)")
    # The sandbox images ship GNU coreutils, whose timeout kills the command's whole process group.
    # The uutils timeout (Ubuntu 25.10+ hosts) kills only the shell and leaves its children running,
    # so on such a host this would test a timeout the sandboxes do not use.
    version = subprocess.run(["timeout", "--version"], capture_output=True, text=True).stdout
    if "GNU coreutils" not in version:
        pytest.skip("host timeout(1) is not GNU coreutils, which the sandbox images ship")
    started = time.monotonic()
    result = _sh(wrap_with_deadline("sleep 30; echo late", 1))
    elapsed = time.monotonic() - started
    # A container runtime reports a SIGKILLed process as 128+9; Python's subprocess reports the
    # signal as -9 because the wrapper `exec`s into timeout(1).
    code = KILLED_EXIT_CODE if result.returncode == -9 else result.returncode
    assert code in DEADLINE_EXIT_CODES and "late" not in result.stdout
    assert elapsed < 10
    assert hit_deadline(code, elapsed, 1)
    assert not hit_deadline(KILLED_EXIT_CODE, 0.1, 60), "an early 137 is the command's own failure"
    assert hit_deadline(124, 1.2, 1), "newer coreutils report the timeout as 124"
    assert not hit_deadline(124, 0.1, 60)


def test_deadline_wrapper_preserves_quoting_and_exit_status() -> None:
    from sandbox_driver.utils import wrap_with_deadline

    ok = _sh(wrap_with_deadline('echo "it\'s $((1+1))" && exit 3', 5))
    assert ok.stdout.strip() == "it's 2" and ok.returncode == 3


def test_bounded_output_keeps_both_ends() -> None:
    from sandbox_driver.utils import BoundedOutput

    out = BoundedOutput(limit=100)
    out.append("HEAD" + "x" * 500)
    out.append("y" * 500 + "TAIL")
    text = out.text()
    assert text.startswith("HEAD") and text.endswith("TAIL")
    assert "characters of output dropped" in text and len(text) < 300
    small = BoundedOutput(limit=100)
    small.append("abc")
    assert small.text() == "abc"


def test_commands_run_in_bash_when_the_image_has_it() -> None:
    """Models write bash; under dash `${PIPESTATUS[0]}` failed with "Bad substitution"."""
    from sandbox_driver.utils import wrap_with_deadline

    result = _sh(
        wrap_with_deadline('[[ 1 == 1 ]] && false | true; echo "pipe=${PIPESTATUS[0]}"', 5)
    )

    assert result.returncode == 0 and "pipe=1" in result.stdout

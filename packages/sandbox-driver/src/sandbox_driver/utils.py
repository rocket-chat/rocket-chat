"""Filesystem and resource conversion utilities for DockerSandboxDriver."""

import io
import os
import re
import shlex
import tarfile
import time

from specifications.interfaces.sandbox import FileStat


def normalize_workspace_path(path: str, default_root: str = "/workspace") -> str:
    """Canonicalize target path within the container workspace."""
    cleaned = path.strip()
    if not cleaned.startswith("/"):
        cleaned = os.path.join(default_root, cleaned)
    return os.path.normpath(cleaned)


def parse_memory_to_bytes(mem_str: str) -> int:
    """
    Parse human-readable memory string into integer byte count.

    Docker API requires IEC/SI single-letter suffixes ('m', 'g') or raw byte counts;
    standard Kubernetes style suffixes like 'Gi' or 'Mi' cause daemon parse errors.
    """
    cleaned = mem_str.strip()
    match = re.match(r"^(\d+(?:\.\d+)?)\s*([a-zA-Z]*)$", cleaned)
    if not match:
        return 4 * 1024 * 1024 * 1024

    val_str, unit_str = match.groups()
    val = float(val_str)
    units: dict[str, int] = {
        "b": 1,
        "k": 1024,
        "kb": 1000,
        "kib": 1024,
        "m": 1024 * 1024,
        "mb": 1000 * 1000,
        "mi": 1024 * 1024,
        "mib": 1024 * 1024,
        "g": 1024 * 1024 * 1024,
        "gb": 1000 * 1000 * 1000,
        "gi": 1024 * 1024 * 1024,
        "gib": 1024 * 1024 * 1024,
    }
    multiplier = units.get(unit_str.lower(), 1024 * 1024 * 1024)
    return int(val * multiplier)


def create_tar_archive(filename: str, content: bytes) -> bytes:
    """Pack file content into a tar archive bytes buffer for container put_archive."""
    tar_stream = io.BytesIO()
    with tarfile.open(fileobj=tar_stream, mode="w") as tar:
        tarinfo = tarfile.TarInfo(name=filename)
        tarinfo.size = len(content)
        tarinfo.mtime = int(time.time())
        tarinfo.mode = 0o644
        tar.addfile(tarinfo, io.BytesIO(content))
    return tar_stream.getvalue()


def extract_file_from_tar(tar_bytes: bytes, target_filename: str) -> str:
    """Extract and decode UTF-8 file content from a container get_archive tar archive."""
    with tarfile.open(fileobj=io.BytesIO(tar_bytes)) as tar:
        for member in tar.getmembers():
            if os.path.basename(member.name) == os.path.basename(target_filename):
                extracted = tar.extractfile(member)
                if extracted is not None:
                    return extracted.read().decode("utf-8", errors="replace")
    raise FileNotFoundError(f"Target file {target_filename} not found in archive")


# One base64 argument must stay well below the kernel's per-argument limit (128 KiB on Linux).
WRITE_CHUNK_CHARS = 64_000

SANDBOX_UID = 1000
SANDBOX_UID_GID = f"{SANDBOX_UID}:{SANDBOX_UID}"
_DEFAULT_DROPPED_CAPABILITIES = ["NET_RAW", "SYS_ADMIN", "SYS_PTRACE", "DAC_READ_SEARCH"]


def sandbox_dropped_capabilities(require_non_root: bool) -> list[str]:
    """Capabilities to drop: everything for unprivileged sandboxes, the risky set otherwise."""
    return ["ALL"] if require_non_root else list(_DEFAULT_DROPPED_CAPABILITIES)


_STAT_FORMAT = "%F|%s|%Y|%n"


def build_list_files_command(target_dir: str, max_depth: int) -> str:
    """List a tree with POSIX tools only (find + stat), so minimal images need no interpreter."""
    root = shlex.quote(target_dir)
    stat = f"stat -c '{_STAT_FORMAT}'"
    return (
        f"if [ -d {root} ]; then "
        f"find {root} -mindepth 1 -maxdepth {int(max_depth)} -exec {stat} {{}} +; "
        f"elif [ -e {root} ]; then {stat} {root}; fi"
    )


def parse_list_files_output(stdout: str) -> list[FileStat]:
    """Parse ``build_list_files_command`` output, sorted by path."""
    entries: list[FileStat] = []
    for line in stdout.splitlines():
        if not line:
            continue
        kind, size, mtime, path = line.split("|", 3)
        entries.append(
            FileStat(
                path=path,
                is_dir=kind == "directory",
                size_bytes=int(size),
                modified_at=int(mtime),
            )
        )
    return sorted(entries, key=lambda entry: entry.path)


def build_read_file_command(target_path: str) -> str:
    """Emit the file base64-encoded on one line; exit 2 when it does not exist."""
    quoted = shlex.quote(target_path)
    return f"test -f {quoted} || exit 2; base64 < {quoted} | tr -d '\\n'"


def build_write_file_commands(target_path: str, content: bytes) -> list[str]:
    """Commands that create ``target_path`` from ``content`` in bounded-size pieces."""
    import base64

    quoted = shlex.quote(target_path)
    parent = shlex.quote(os.path.dirname(target_path) or "/")
    commands = [f"mkdir -p {parent} && : > {quoted}"]
    encoded = base64.b64encode(content).decode("ascii")
    for start in range(0, len(encoded), WRITE_CHUNK_CHARS):
        chunk = encoded[start : start + WRITE_CHUNK_CHARS]
        commands.append(f"printf %s {chunk} | base64 -d >> {quoted}")
    return commands


# Exit status of a process killed by SIGKILL (128 + 9), which is what the deadline wrapper sends.
KILLED_EXIT_CODE = 137
# The client-side wait only backstops the in-sandbox deadline, so it must outlast it.
CLIENT_DEADLINE_MARGIN_SECONDS = 10
MAX_CAPTURED_CHARS = 1_000_000


def wrap_with_deadline(command: str, timeout_seconds: int) -> str:
    """Run ``command`` under ``timeout -s KILL`` so a hung process dies inside the sandbox.

    Cancelling only the client side would leave the process running (and holding the
    sandbox's CPU) after the caller was told it timed out. Images without ``timeout`` run
    the command unwrapped, which keeps the previous behaviour.
    """
    quoted = shlex.quote(command)
    return (
        "if command -v timeout >/dev/null 2>&1; "
        f"then exec timeout -s KILL {int(timeout_seconds)} sh -c {quoted}; "
        f"else exec sh -c {quoted}; fi"
    )


def hit_deadline(exit_code: int, elapsed_seconds: float, timeout_seconds: int) -> bool:
    """True when a command was killed by ``wrap_with_deadline`` rather than failing itself."""
    return exit_code == KILLED_EXIT_CODE and elapsed_seconds >= timeout_seconds - 1


class BoundedOutput:
    """Collects process output keeping the start and the end, so memory stays bounded.

    Startup noise and the final stack trace are the diagnostic parts; the middle of a runaway
    log is what gets dropped.
    """

    def __init__(self, limit: int = MAX_CAPTURED_CHARS) -> None:
        self._half = limit // 2
        self._head = ""
        self._tail = ""
        self._dropped = 0

    def append(self, chunk: str) -> None:
        room = self._half - len(self._head)
        if room > 0:
            self._head += chunk[:room]
            chunk = chunk[room:]
        if not chunk:
            return
        self._tail += chunk
        if len(self._tail) > self._half:
            self._dropped += len(self._tail) - self._half
            self._tail = self._tail[-self._half :]

    def text(self) -> str:
        if not self._dropped:
            return self._head + self._tail
        return f"{self._head}\n[... {self._dropped} characters of output dropped ...]\n{self._tail}"

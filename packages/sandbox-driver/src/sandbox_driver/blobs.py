"""Binary-safe file transfer out of a sandbox over plain ``exec``.

``read_file`` is text-only, and a single ``exec`` result is clamped at 1M characters, so bytes
travel as base64 in chunks small enough to survive the clamp. It needs only POSIX ``stat``,
``dd`` and ``base64`` in the image, so it works the same on the Docker and Kubernetes drivers.
"""

from __future__ import annotations

import base64
import shlex

from sandbox_driver.exceptions import SandboxFilesystemError
from specifications.interfaces.sandbox import SandboxDriverProtocol

CHUNK_BYTES = 384 * 1024  # 512K base64 characters: comfortably under the output clamp
DEFAULT_MAX_BYTES = 10 * 1024 * 1024


class BlobTooLargeError(SandboxFilesystemError):
    """The file is larger than the caller allowed."""


async def file_size(driver: SandboxDriverProtocol, session_id: str, path: str) -> int:
    result = await driver.exec_command(
        session_id, f"stat -c %s {shlex.quote(path)}", timeout_seconds=30
    )
    if result.exit_code != 0 or not result.stdout.strip().isdigit():
        raise FileNotFoundError(f"File not found in sandbox: {path}")
    return int(result.stdout.strip())


async def read_bytes(
    driver: SandboxDriverProtocol,
    session_id: str,
    path: str,
    *,
    max_bytes: int = DEFAULT_MAX_BYTES,
) -> bytes:
    size = await file_size(driver, session_id, path)
    if size > max_bytes:
        raise BlobTooLargeError(f"{path} is {size} bytes; the limit is {max_bytes}")
    quoted = shlex.quote(path)
    parts: list[bytes] = []
    for index in range(0, (size + CHUNK_BYTES - 1) // CHUNK_BYTES):
        chunk = await driver.exec_command(
            session_id,
            f"dd if={quoted} bs={CHUNK_BYTES} skip={index} count=1 2>/dev/null | base64 -w0",
            timeout_seconds=60,
        )
        if chunk.exit_code != 0:
            raise SandboxFilesystemError(f"Failed reading {path}: {chunk.stderr.strip()}")
        parts.append(base64.b64decode(chunk.stdout.strip()))
    data = b"".join(parts)
    if len(data) != size:
        raise SandboxFilesystemError(f"Short read of {path}: {len(data)} of {size} bytes")
    return data

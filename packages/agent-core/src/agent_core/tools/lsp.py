"""On-demand language probes and diagnostics for file modifications.

Runs fast post-edit diagnostics (syntax/type checks) when and only when the
corresponding tool exists in the sandbox. If the binary is missing or checks pass,
it is a zero-latency silent bypass.
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from specifications.interfaces.sandbox import SandboxDriverProtocol

logger = logging.getLogger(__name__)

# Map file extensions to check command template and required binary
DIAGNOSTIC_COMMANDS: dict[str, tuple[str, str]] = {
    ".py": ("ruff", "ruff check --select E,F,W --no-fix {file}"),
    ".go": ("go", "go vet {file}"),
    ".rs": ("cargo", "cargo check --quiet"),
    ".ts": ("tsc", "npx tsc --noEmit {file}"),
    ".tsx": ("tsc", "npx tsc --noEmit {file}"),
}

_BINARY_CACHE: dict[tuple[str, str], bool] = {}


async def _has_binary(
    driver: SandboxDriverProtocol,
    session_id: str,
    binary: str,
) -> bool:
    """Check if a tool binary exists in the sandbox, cached per session."""
    key = (session_id, binary)
    if key in _BINARY_CACHE:
        return _BINARY_CACHE[key]

    try:
        res = await driver.exec_command(
            session_id=session_id,
            command=f"which {binary} 2>/dev/null",
            timeout_seconds=3,
        )
        available = res.exit_code == 0 and bool(res.stdout.strip())
    except Exception:
        available = False

    _BINARY_CACHE[key] = available
    return available


async def run_diagnostics_on_file(
    driver: SandboxDriverProtocol | None,
    session_id: str | None,
    file_path: str,
) -> str | None:
    """Run fast syntax/lint diagnostics if the language tool is installed in sandbox.

    Returns diagnostic message string if errors were found, or None if clean/skipped.
    """
    if driver is None or not session_id:
        return None

    _, ext = os.path.splitext(file_path)
    ext = ext.lower()
    config = DIAGNOSTIC_COMMANDS.get(ext)
    if not config:
        return None

    binary, cmd_template = config
    if not await _has_binary(driver, session_id, binary):
        return None

    directory = os.path.dirname(file_path) or "/workspace"
    command = cmd_template.format(file=file_path, dir=directory)

    try:
        res = await driver.exec_command(
            session_id=session_id,
            command=command,
            workdir=directory,
            timeout_seconds=8,
        )
        if res.exit_code != 0:
            output = (res.stdout + "\n" + res.stderr).strip()
            if output:
                lines = output.splitlines()[:10]
                summary = "\n".join(lines)
                return f"\n\n[LSP Diagnostics - Syntax/Type Issue]\n{summary}"
    except Exception:
        logger.debug("Diagnostics check failed or timed out for %s", file_path)

    return None

"""On-demand language probes and diagnostics for file modifications.

Runs fast post-edit diagnostics (syntax/type checks) when and only when the
corresponding tool exists in the sandbox. If the binary is missing or checks pass,
it is a zero-latency silent bypass.
"""

from __future__ import annotations

import logging
import posixpath
import shlex
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from specifications.interfaces.sandbox import SandboxDriverProtocol

logger = logging.getLogger(__name__)

# Cache binary lookup per session with a TTL to avoid stale results while avoiding
# re-running 'which' on every single edit.
_BINARY_CACHE: dict[tuple[str, str], tuple[bool, float]] = {}
CACHE_TTL_SECONDS = 60.0


async def _resolve_binary(
    driver: SandboxDriverProtocol,
    session_id: str,
    binary: str,
    workdir: str,
) -> str | None:
    """Check if a tool binary exists in the sandbox, looking in local project paths first."""
    now = time.time()
    key = (session_id, binary)
    if key in _BINARY_CACHE:
        available, cached_at = _BINARY_CACHE[key]
        if (now - cached_at) < CACHE_TTL_SECONDS:
            return binary if available else None

    # Check project-local virtualenv and node_modules before global PATH
    candidates = [
        f"{workdir}/.venv/bin/{binary}",
        f"{workdir}/node_modules/.bin/{binary}",
        f"/workspace/.venv/bin/{binary}",
        f"/workspace/node_modules/.bin/{binary}",
    ]
    check_script = " || ".join(
        [f"[ -x {shlex.quote(c)} ]" for c in candidates]
        + [f"command -v {shlex.quote(binary)} >/dev/null 2>&1"]
    )

    try:
        res = await driver.exec_command(
            session_id=session_id,
            command=f"sh -c {shlex.quote(check_script)}",
            timeout_seconds=3,
        )
        available = res.exit_code == 0
    except Exception:
        available = False

    _BINARY_CACHE[key] = (available, now)
    return binary if available else None


def _build_diagnostic_command(ext: str, file_path: str, directory: str) -> tuple[str, str] | None:
    """Return (binary_name, safe_command_string) for a file extension."""
    q_file = shlex.quote(file_path)
    q_dir = shlex.quote(directory)

    if ext == ".py":
        return ("ruff", f"ruff check --select E,F,W --no-fix {q_file}")
    if ext == ".go":
        # Run package-aware vet in the file's directory
        return ("go", f"go vet {q_dir}")
    if ext == ".rs":
        return ("cargo", "cargo check --quiet")
    if ext in (".ts", ".tsx"):
        # Check if tsconfig exists near the file
        return ("tsc", f"npx tsc --noEmit {q_file}")
    return None


async def run_diagnostics_on_file(
    driver: SandboxDriverProtocol | None,
    session_id: str | None,
    file_path: str,
) -> str | None:
    """Run fast syntax/lint diagnostics if the language tool is installed in sandbox.

    Returns diagnostic message string if errors were found, or None if clean/skipped.
    """
    if driver is None or not session_id or not file_path:
        return None

    _, ext = posixpath.splitext(file_path)
    ext = ext.lower()
    directory = posixpath.dirname(file_path) or "/workspace"

    config = _build_diagnostic_command(ext, file_path, directory)
    if not config:
        return None

    binary, command = config
    resolved = await _resolve_binary(driver, session_id, binary, directory)
    if not resolved:
        return None

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

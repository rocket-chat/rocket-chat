"""Package-manager abstraction tool: uv / pnpm / npm / poetry.

The manager is selected from what the *workspace itself declares* — its lockfile and,
for Node projects, the ``packageManager`` field — rather than from whichever binary
happens to be installed, so an agent installs the way the project already does.
Installs are lockfile-aware (frozen/CI variants when a lockfile exists) and failures
are parsed into a concise cause string, because raw installer output is long and
repetitive while the actionable line is a single error code.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal, cast

from agent_core.tools.clamp import clamp_output
from agent_core.tools.live_output import live_chunk
from agent_core.tools.tier2 import _sh_quote
from specifications.interfaces.sandbox import ExecResult, SandboxDriverProtocol

PackageAction = Literal["install", "add", "remove"]

_ACTIONS: frozenset[str] = frozenset({"install", "add", "remove"})

# Files probed to learn what the workspace declares. Order is irrelevant here — the
# priority is encoded in :func:`detect_manager`.
_PROBE_FILES: tuple[str, ...] = (
    "pnpm-lock.yaml",
    "package-lock.json",
    "npm-shrinkwrap.json",
    "poetry.lock",
    "uv.lock",
    "pyproject.toml",
    "package.json",
)


@dataclass(frozen=True)
class _PackageManager:
    """A selectable package manager: declaration guard + lockfile-aware commands."""

    label: str
    binary: str
    install: str
    add: str
    remove: str
    dev_flag: str


_PACKAGE_MANAGERS: tuple[_PackageManager, ...] = (
    _PackageManager(
        label="pnpm",
        binary="pnpm",
        install=(
            "if [ -f pnpm-lock.yaml ]; then pnpm install --frozen-lockfile; else pnpm install; fi"
        ),
        add="pnpm add {packages}",
        remove="pnpm remove {packages}",
        dev_flag="-D",
    ),
    _PackageManager(
        label="npm",
        binary="npm",
        install=(
            "if [ -f package-lock.json ] || [ -f npm-shrinkwrap.json ]; then npm ci; "
            "else npm install; fi"
        ),
        add="npm install {packages}",
        remove="npm uninstall {packages}",
        dev_flag="--save-dev",
    ),
    _PackageManager(
        label="poetry",
        binary="poetry",
        install="poetry install",
        add="poetry add {packages}",
        remove="poetry remove {packages}",
        dev_flag="--group dev",
    ),
    _PackageManager(
        label="uv",
        binary="uv",
        install="if [ -f uv.lock ]; then uv sync --frozen; else uv sync; fi",
        add="uv add {packages}",
        remove="uv remove {packages}",
        dev_flag="--dev",
    ),
)

_MANAGER_BY_LABEL: dict[str, _PackageManager] = {pm.label: pm for pm in _PACKAGE_MANAGERS}

_FAILURE_PATTERNS: tuple[str, ...] = (
    r"ERR_PNPM_[^\n]*",
    r"npm ERR![^\n]*",
    r"error\[[A-Z0-9]+\][^\n]*",
    r"ERROR: [^\n]*",
    r"Could not find a version[^\n]*",
    r"No matching distribution[^\n]*",
    r"Error: [^\n]*",
)


def candidates() -> tuple[_PackageManager, ...]:
    """Expose the ordered package-manager candidates for inspection and tests."""
    return _PACKAGE_MANAGERS


def detect_manager(
    present: set[str],
    package_json_text: str = "",
    pyproject_text: str = "",
) -> str | None:
    """Select the package manager the workspace declares, in priority order.

    Lockfiles win over manifest fields, which win over a bare manifest: a project
    that committed ``pnpm-lock.yaml`` has already declared pnpm regardless of what
    else is installed. Python projects without a lockfile default to ``uv`` because
    that is the platform's declared tool.
    """
    if "pnpm-lock.yaml" in present:
        return "pnpm"
    if "package-lock.json" in present or "npm-shrinkwrap.json" in present:
        return "npm"
    if _declares_package_manager(package_json_text, "pnpm"):
        return "pnpm"
    if _declares_package_manager(package_json_text, "npm"):
        return "npm"
    if "poetry.lock" in present or "[tool.poetry]" in pyproject_text:
        return "poetry"
    if "uv.lock" in present or "[tool.uv]" in pyproject_text:
        return "uv"
    if "pyproject.toml" in present:
        return "uv"
    if "package.json" in present:
        return "npm"
    return None


def _declares_package_manager(package_json_text: str, name: str) -> bool:
    """Whether package.json pins the manager via its ``packageManager`` field."""
    if not package_json_text:
        return False
    return bool(re.search(rf'"packageManager"\s*:\s*"{name}(?:@|")', package_json_text))


def parse_failure(output: str) -> str:
    """Extract the single actionable error line from installer output."""
    for pattern in _FAILURE_PATTERNS:
        match = re.search(pattern, output)
        if match:
            return match.group(0).strip()
    return ""


def _probe_command() -> str:
    checks = " ".join(f'[ -f {name} ] && echo "__ROCKET_FILE__:{name}";' for name in _PROBE_FILES)
    return (
        f"{checks} echo '__ROCKET_PKGJSON__'; cat package.json 2>/dev/null || true; "
        "echo '__ROCKET_PYPROJECT__'; cat pyproject.toml 2>/dev/null || true"
    )


def _parse_probe(stdout: str) -> tuple[set[str], str, str]:
    present: set[str] = set()
    package_json: list[str] = []
    pyproject: list[str] = []
    section: str | None = None
    for line in stdout.splitlines():
        if line.startswith("__ROCKET_FILE__:"):
            present.add(line.split(":", 1)[1])
            section = None
        elif line == "__ROCKET_PKGJSON__":
            section = "package_json"
        elif line == "__ROCKET_PYPROJECT__":
            section = "pyproject"
        elif section == "package_json":
            package_json.append(line)
        elif section == "pyproject":
            pyproject.append(line)
    return present, "\n".join(package_json), "\n".join(pyproject)


def _build_command(
    manager: _PackageManager,
    action: PackageAction,
    packages: list[str],
    dev: bool,
) -> str:
    guard = (
        f"if ! command -v {manager.binary} >/dev/null 2>&1; then "
        f"echo 'ROCKET_PKG_ERROR: {manager.binary} is not installed in the sandbox'; "
        "exit 127; fi; "
    )
    if action == "install":
        command = manager.install
    else:
        quoted = " ".join(_sh_quote(package) for package in packages)
        template = manager.add if action == "add" else manager.remove
        command = template.format(packages=quoted)
        if dev and action == "add":
            command = f"{command} {manager.dev_flag}"
    return f"{guard}{command}"


def _format_result(manager: _PackageManager, action: PackageAction, result: ExecResult) -> str:
    raw = "\n".join(
        part for part in ((result.stdout or "").strip(), (result.stderr or "").strip()) if part
    )
    verdict = "PASSED" if result.exit_code == 0 else f"FAILED (exit {result.exit_code})"
    header = f"[package_manager/{manager.label}] {action.upper()} {verdict}"
    failure = parse_failure(raw)
    if failure and result.exit_code != 0:
        header += f" — {failure}"
    body = clamp_output(raw) if raw else "(no output)"
    return f"{header}\n\n{body}"


async def package_manager(
    driver: SandboxDriverProtocol,
    session_id: str,
    action: str = "install",
    packages: list[str] | None = None,
    manager: str | None = None,
    dev: bool = False,
    timeout_seconds: int = 600,
    workdir: str = "/workspace",
) -> str:
    """Manage project dependencies with the workspace's declared package manager."""
    if action not in _ACTIONS:
        return f"Error: unsupported package_manager action '{action}' (expected install/add/remove)"
    if action in ("add", "remove") and not packages:
        return f"Error: package_manager action '{action}' requires a non-empty 'packages' list"

    probe = await driver.exec_command(
        session_id=session_id, command=_probe_command(), workdir=workdir, timeout_seconds=30
    )
    present, package_json_text, pyproject_text = _parse_probe(probe.stdout or "")

    label = manager or detect_manager(present, package_json_text, pyproject_text)
    if label is None:
        return (
            "[package_manager/auto] FAILED — no uv/pnpm/npm/poetry project detected "
            "(no lockfile, pyproject.toml or package.json in the workspace)."
        )
    selected = _MANAGER_BY_LABEL.get(label)
    if selected is None:
        return f"Error: unknown package manager '{label}' (expected uv, pnpm, npm or poetry)"

    typed_action = cast(PackageAction, action)
    command = _build_command(selected, typed_action, packages or [], dev)
    result = await driver.exec_command(
        session_id=session_id,
        command=command,
        workdir=workdir,
        timeout_seconds=timeout_seconds,
        on_stdout_chunk=live_chunk(),
    )
    return _format_result(selected, typed_action, result)

"""Verification tool suite: lint, typecheck, and build with per-ecosystem detection.

Detection prefers *project-declared* entrypoints (npm scripts, Makefile targets)
over guessing commands, and always reports which runner was selected so the agent
can correct course. Wrapping native runners (ruff, mypy, tsc, clippy, go vet)
is intentional: they are the ecosystem standard.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from agent_core.tools.clamp import clamp_output
from agent_core.tools.live_output import live_chunk
from specifications.interfaces.sandbox import SandboxDriverProtocol

CheckKind = Literal["lint", "typecheck", "build"]


@dataclass(frozen=True)
class _Candidate:
    """A selectable verification runner: required binary + project-shape guard."""

    label: str
    binary: str
    guard: str
    command: str


def _npm_script_guard(script: str) -> str:
    """Shell guard asserting the project declares a given npm script."""
    return (
        "[ -f package.json ] && "
        f"node -e \"const s=(require('./package.json').scripts||{{}});"
        f"process.exit(s['{script}']?0:1)\" 2>/dev/null"
    )


_CANDIDATES: dict[CheckKind, list[_Candidate]] = {
    "lint": [
        _Candidate("npm-lint", "npm", _npm_script_guard("lint"), "npm run lint --silent"),
        _Candidate(
            "ruff",
            "ruff",
            "[ -f pyproject.toml ] || [ -f ruff.toml ] || [ -f .ruff.toml ]",
            "ruff check .",
        ),
        _Candidate("clippy", "cargo", "[ -f Cargo.toml ]", "cargo clippy --all-targets"),
        _Candidate("go-vet", "go", "[ -f go.mod ]", "go vet ./..."),
        _Candidate("make-lint", "make", "grep -qE '^lint:' Makefile 2>/dev/null", "make lint"),
    ],
    "typecheck": [
        _Candidate(
            "npm-typecheck", "npm", _npm_script_guard("type-check"), "npm run type-check --silent"
        ),
        _Candidate("mypy", "mypy", "[ -f pyproject.toml ] || [ -f mypy.ini ]", "mypy ."),
        _Candidate("tsc", "npx", "[ -f tsconfig.json ]", "npx --no-install tsc --noEmit"),
        _Candidate("cargo-check", "cargo", "[ -f Cargo.toml ]", "cargo check"),
        _Candidate("go-build", "go", "[ -f go.mod ]", "go build ./..."),
        _Candidate(
            "make-check",
            "make",
            "grep -qE '^(typecheck|check):' Makefile 2>/dev/null",
            "make check",
        ),
    ],
    "build": [
        _Candidate("npm-build", "npm", _npm_script_guard("build"), "npm run build --silent"),
        _Candidate(
            "python-build", "python", "[ -f pyproject.toml ] || [ -f setup.py ]", "python -m build"
        ),
        _Candidate("cargo-build", "cargo", "[ -f Cargo.toml ]", "cargo build"),
        _Candidate("go-build", "go", "[ -f go.mod ]", "go build ./..."),
        _Candidate("make-build", "make", "grep -qE '^build:' Makefile 2>/dev/null", "make build"),
    ],
}


def candidates_for(kind: CheckKind) -> list[_Candidate]:
    """Expose the ordered runner candidates for a verification kind."""
    return list(_CANDIDATES[kind])


def _detection_script(kind: CheckKind) -> str:
    """Build the shell snippet that selects and runs the first available runner."""
    branches: list[str] = []
    for index, candidate in enumerate(_CANDIDATES[kind]):
        keyword = "if" if index == 0 else "elif"
        branches.append(
            f"{keyword} {candidate.guard} && command -v {candidate.binary} >/dev/null 2>&1; then "
            f"echo '__ROCKET_TOOL__:{candidate.label}'; {candidate.command}"
        )
    joined = "; ".join(branches)
    return (
        f"{joined}; else echo '__ROCKET_TOOL__:none'; "
        f"echo 'No {kind} runner detected (checked npm scripts, ruff/mypy, tsc, cargo, go, make).'; exit 2; fi"
    )


def _extract_runner_label(output: str) -> str:
    match = re.search(r"__ROCKET_TOOL__:(\w[\w-]*)", output)
    return match.group(1) if match else ""


_SUMMARY_PATTERNS = (
    r"Found \d+ errors?[^\n]*",
    r"\d+ problems? \([^)]*\)",
    r"error\[[A-Z0-9]+\][^\n]*",
    r"Successfully compiled[^\n]*",
    r"Compiling [^\n]*",
    r"go: [^\n]*",
    r"makeself|[0-9]+ files? \(",
)


def summarize(output: str) -> str:
    """Extract a concise error/warning summary line from common checker output."""
    for pattern in _SUMMARY_PATTERNS:
        found = re.search(pattern, output)
        if found:
            return found.group(0).strip()
    return ""


async def run_check(
    driver: SandboxDriverProtocol,
    session_id: str,
    kind: CheckKind,
    command: str | None = None,
    timeout_seconds: int = 600,
    workdir: str = "/workspace",
) -> str:
    """Run a lint/typecheck/build check and return a compact, parsed verdict."""
    if command:
        check_command = command
        runner = "custom"
    else:
        check_command = _detection_script(kind)
        runner = "auto"

    result = await driver.exec_command(
        session_id=session_id,
        command=check_command,
        workdir=workdir,
        timeout_seconds=timeout_seconds,
        on_stdout_chunk=live_chunk(),
    )
    raw = "\n".join(
        part for part in ((result.stdout or "").strip(), (result.stderr or "").strip()) if part
    )
    runner = _extract_runner_label(raw) or runner
    verdict = "PASSED" if result.exit_code == 0 else f"FAILED (exit {result.exit_code})"
    header = f"[{kind}/{runner}] {verdict}"
    summary = summarize(raw)
    if summary:
        header += f" — {summary}"
    body = clamp_output(raw) if raw else "(no output)"
    return f"{header}\n\n{body}"

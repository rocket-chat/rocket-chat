"""Per-repository environment setup.

A repository can say how to prepare a workspace: ``.rocket/setup.sh``, or the
``onCreateCommand`` / ``postCreateCommand`` of a ``.devcontainer/devcontainer.json`` (the rest
of a dev container - image, features, mounts - is the platform's job, not the repository's).
The command runs once per workspace, right after the clone, as the sandbox user, with the
GitHub credentials removed from its environment: setup needs the network, not the token.
"""

from __future__ import annotations

import hashlib
import logging
import shlex
from dataclasses import dataclass
from typing import Any

import json5

from specifications.interfaces.sandbox import SandboxDriverProtocol

logger = logging.getLogger(__name__)

SETUP_SCRIPT = ".rocket/setup.sh"
DEVCONTAINER = ".devcontainer/devcontainer.json"
WORKSPACE = "/workspace"
MARKER_DIR = f"{WORKSPACE}/.rocket"
SETUP_TIMEOUT_SECONDS = 900
MAX_COMMAND_CHARS = 8000
_DEVCONTAINER_HOOKS = ("onCreateCommand", "postCreateCommand")
# Setup scripts are the repository's own code: they run without the gateway capability (and
# without any token an older sandbox may still carry), so they can only fetch public code.
_NO_CREDENTIALS = {
    "GITHUB_TOKEN": "",
    "GH_TOKEN": "",
    "GIT_ASKPASS": "",
    "GIT_CONFIG_COUNT": "0",
    **{f"GIT_CONFIG_{part}_{index}": "" for part in ("KEY", "VALUE") for index in range(8)},
}


@dataclass(frozen=True)
class SetupPlan:
    source: str
    command: str

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(f"{self.source}\0{self.command}".encode()).hexdigest()[:16]


def _flatten(hook: Any) -> str:
    """A devcontainer hook is a string, an argv list, or an object of named commands."""
    if isinstance(hook, str):
        return hook.strip()
    if isinstance(hook, list) and all(isinstance(part, str) for part in hook):
        return shlex.join(hook)
    if isinstance(hook, dict):
        parts = [_flatten(value) for value in hook.values()]
        return " && ".join(part for part in parts if part)
    return ""


def plan_from_devcontainer(text: str) -> SetupPlan | None:
    try:
        config = json5.loads(text)
    except ValueError:
        return None
    if not isinstance(config, dict):
        return None
    commands = [_flatten(config.get(hook)) for hook in _DEVCONTAINER_HOOKS]
    command = " && ".join(c for c in commands if c)
    if not command or len(command) > MAX_COMMAND_CHARS:
        return None
    return SetupPlan(source=DEVCONTAINER, command=command)


async def _read(driver: SandboxDriverProtocol, session_id: str, path: str) -> str | None:
    try:
        return await driver.read_file(session_id, path)
    except Exception:
        return None


def _marker_path(directory: str) -> str:
    """Per-repository marker, kept outside the repository so it never shows in ``git status``."""
    return f"{MARKER_DIR}/setup-{hashlib.sha1(directory.encode()).hexdigest()[:10]}"


async def find_setup_plan(
    driver: SandboxDriverProtocol, session_id: str, directory: str = WORKSPACE
) -> SetupPlan | None:
    """``.rocket/setup.sh`` wins; otherwise a dev container's create hooks."""
    script = await _read(driver, session_id, f"{directory}/{SETUP_SCRIPT}")
    if script is not None and script.strip():
        return SetupPlan(source=SETUP_SCRIPT, command=f"sh {shlex.quote(SETUP_SCRIPT)}")
    config = await _read(driver, session_id, f"{directory}/{DEVCONTAINER}")
    return plan_from_devcontainer(config) if config else None


async def run_setup(
    driver: SandboxDriverProtocol,
    session_id: str,
    plan: SetupPlan,
    directory: str = WORKSPACE,
) -> tuple[bool, str]:
    """Run ``plan`` in ``directory`` unless this exact setup already ran there; (ok, output tail)."""
    marker = _marker_path(directory)
    done = await driver.exec_command(session_id, f"cat {marker} 2>/dev/null", timeout_seconds=15)
    if done.exit_code == 0 and done.stdout.strip() == plan.fingerprint:
        return True, "already done"
    result = await driver.exec_command(
        session_id,
        plan.command,
        workdir=directory,
        timeout_seconds=SETUP_TIMEOUT_SECONDS,
        env=dict(_NO_CREDENTIALS),
    )
    output = (result.stdout + result.stderr).strip()[-1500:]
    if result.exit_code != 0:
        return False, output or f"exited {result.exit_code}"
    await driver.exec_command(
        session_id,
        f"mkdir -p {MARKER_DIR} && printf %s {shlex.quote(plan.fingerprint)} > {marker}",
        timeout_seconds=15,
    )
    return True, output

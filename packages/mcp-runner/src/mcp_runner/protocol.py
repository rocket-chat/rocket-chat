"""The wire protocol between the control plane and a runner, and the rules both enforce.

One WebSocket carries one MCP server process. Every frame is a JSON object with a ``t`` field:

* client -> runner ``start``: ``command``, ``env``, ``org_id``, ``server_id`` and optionally
  ``setup`` (first frame only)
* runner -> client ``status``: progress while a ``setup`` script installs the program (ignorable)
* runner -> client ``ready`` once the process runs, or ``error`` with a ``message``
* both ways ``msg``: ``line`` is one newline-delimited JSON-RPC message
* runner -> client ``exit``: ``code`` and the ``stderr`` tail, when the process ends

The socket is authenticated with ``Authorization: Bearer <token>`` (a secret shared with the
control plane); nothing else about the caller is trusted, so the control plane states the
organization and the runner only uses it to bound concurrency and to separate scratch space.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

MAX_COMMAND_CHARS = 4096
MAX_SETUP_CHARS = 8192
MAX_ENV_VARIABLES = 64
MAX_ENV_VALUE_CHARS = 16_384
MAX_LINE_BYTES = 8 * 1024 * 1024
START_TIMEOUT_SECONDS = 10.0

_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
_IDENTIFIER = re.compile(r"^[A-Za-z0-9_.:@-]{1,128}$")
# Variables the runner sets itself; a server's configuration cannot override them.
RESERVED_ENV = frozenset(
    {"PATH", "HOME", "TMPDIR", "XDG_CACHE_HOME", "npm_config_cache", "UV_CACHE_DIR"}
)


class InvalidStart(ValueError):
    """The ``start`` frame is not something the runner will run."""


def is_valid_env_name(name: str) -> bool:
    return bool(_ENV_NAME.match(name))


@dataclass(frozen=True)
class StartRequest:
    command: str
    org_id: str
    server_id: str
    env: dict[str, str] = field(default_factory=dict)
    # A script that installs the program the command runs; see ``mcp_runner.tools``.
    setup: str | None = None


def validate_start(frame: Mapping[str, Any]) -> StartRequest:
    """The request in a ``start`` frame, or :class:`InvalidStart`."""
    command, env = frame.get("command"), frame.get("env") or {}
    org_id, server_id, setup = frame.get("org_id"), frame.get("server_id"), frame.get("setup")
    if not isinstance(command, str) or not command.strip() or "\n" in command or "\r" in command:
        raise InvalidStart("The command must be a single line.")
    if len(command) > MAX_COMMAND_CHARS:
        raise InvalidStart("The command is too long.")
    if setup is not None and (not isinstance(setup, str) or len(setup) > MAX_SETUP_CHARS):
        raise InvalidStart("The install command must be text under 8 KB.")
    if not isinstance(env, dict) or len(env) > MAX_ENV_VARIABLES:
        raise InvalidStart("The environment must be an object of at most 64 variables.")
    for name, value in env.items():
        if not isinstance(name, str) or not is_valid_env_name(name):
            raise InvalidStart(f"'{name}' is not a valid environment variable name.")
        if name in RESERVED_ENV:
            raise InvalidStart(f"'{name}' is set by the runner and cannot be overridden.")
        if not isinstance(value, str) or len(value) > MAX_ENV_VALUE_CHARS:
            raise InvalidStart(f"The value of '{name}' must be a string under 16 KB.")
    for label, identifier in (("org_id", org_id), ("server_id", server_id)):
        if not isinstance(identifier, str) or not _IDENTIFIER.match(identifier):
            raise InvalidStart(f"{label} is missing or malformed.")
    return StartRequest(
        command=command.strip(),
        org_id=str(org_id),
        server_id=str(server_id),
        env=dict(env),
        setup=setup.strip() if isinstance(setup, str) and setup.strip() else None,
    )

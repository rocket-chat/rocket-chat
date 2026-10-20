"""A session's command environment: its sandbox type's variables and the ones the agent set.

Drivers keep both layers in memory only, so every turn hands them over again: a restart or
another API replica then runs the session's commands with the same variables.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from config_engine.crypto import CredentialCipher

from api.services.sandbox_catalogue import SandboxEntry, entry_environment
from specifications.interfaces.sandbox import (
    SESSION_ENV_AGENT,
    SESSION_ENV_SANDBOX_TYPE,
    SandboxDriverProtocol,
)
from specifications.interfaces.session import SessionRecord, SessionStoreProtocol

logger = logging.getLogger(__name__)

AGENT_ENV_KEY = "agent_env"


def agent_env_of(metadata: Mapping[str, Any]) -> dict[str, str]:
    """The variables the agent set with ``env_set`` in earlier turns."""
    stored = metadata.get(AGENT_ENV_KEY)
    if not isinstance(stored, dict):
        return {}
    return {str(name): str(value) for name, value in stored.items()}


def sandbox_type_env(entry: SandboxEntry | None) -> dict[str, str]:
    """The sandbox type's variables, secrets decrypted; none when they cannot be opened."""
    if entry is None or not entry.env:
        return {}
    try:
        return entry_environment(entry, CredentialCipher())
    except Exception:
        # A rotated master key without re-sealing: commands run without the type's variables
        # rather than the turn failing, and the operator sees why.
        logger.warning("Cannot decrypt the variables of sandbox type %s", entry.id)
        return {}


def apply_session_env(
    driver: SandboxDriverProtocol | None,
    session_id: str,
    entry: SandboxEntry | None,
    metadata: Mapping[str, Any],
) -> None:
    """Hand the session's two variable layers to the driver for this turn's commands."""
    if driver is None:
        return
    driver.set_session_env_layer(session_id, SESSION_ENV_SANDBOX_TYPE, sandbox_type_env(entry))
    driver.set_session_env_layer(session_id, SESSION_ENV_AGENT, agent_env_of(metadata))


async def remember_agent_env(
    store: SessionStoreProtocol, session_id: str, org_id: str, env: dict[str, str]
) -> None:
    """Record the agent's variables on the session so later turns' commands get them."""

    def record(session: SessionRecord) -> None:
        metadata = dict(session.metadata) if isinstance(session.metadata, dict) else {}
        if env:
            metadata[AGENT_ENV_KEY] = dict(env)
        else:
            metadata.pop(AGENT_ENV_KEY, None)
        session.metadata = metadata

    await store.mutate_session(session_id, org_id, record)

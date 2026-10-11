"""The built-in tools, one module per domain.

Each module's ``register(registry)`` declares its tools with ``registry._tool``; the handler's
signature defines the schema the model sees. Registration order is the order below.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from agent_core.tools.builtin import (
    background,
    commands,
    conversation,
    files,
    git,
    knowledge,
    repository,
    web,
)

if TYPE_CHECKING:
    from agent_core.tools.registry import ToolRegistry

_DOMAINS = (web, conversation, knowledge, repository, files, commands, background, git)


def register_all(registry: ToolRegistry) -> None:
    for domain in _DOMAINS:
        domain.register(registry)

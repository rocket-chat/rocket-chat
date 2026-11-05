"""The built-in tools, one module per domain.

Each module's ``register(registry)`` declares its tools with ``registry._tool``; the handler's
signature defines the schema the model sees. Registration order is the order below.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from agent_core.tools.builtin import (
    artifacts,
    background,
    commands,
    conversation,
    environment,
    files,
    git,
    github,
    knowledge,
    repository,
    wait,
    web,
    workspace,
)

if TYPE_CHECKING:
    from agent_core.tools.registry import ToolRegistry

_DOMAINS = (
    web,
    conversation,
    artifacts,
    knowledge,
    repository,
    workspace,
    github,
    files,
    commands,
    environment,
    background,
    git,
    wait,
)


# What the agent settings page calls each module's tools, so its groups follow the code. Keyed by
# module, since a domain may register another's tools (conversation registers delegation).
DOMAIN_TITLES: dict[str, str] = {
    "web": "Web",
    "conversation": "Conversation & planning",
    "delegation": "Subagents",
    "artifacts": "Artifacts",
    "knowledge": "Knowledge, memory & skills",
    "repository": "Repositories",
    "workspace": "Workspace",
    "github": "GitHub",
    "files": "Files",
    "commands": "Commands",
    "environment": "Environment",
    "background": "Background processes",
    "git": "Git",
    "wait": "Waits",
}


def domain_title(module: str) -> str:
    """The title of the domain whose module (``agent_core.tools.builtin.<domain>``) declared a tool."""
    domain = module.rsplit(".", 1)[-1]
    return DOMAIN_TITLES.get(domain, domain.replace("_", " ").capitalize())


def register_all(registry: ToolRegistry) -> None:
    for domain in _DOMAINS:
        domain.register(registry)

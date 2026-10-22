"""Which tools a request carries, and which the agent loads when it needs them.

Every tool definition is sent with every request, so each step of a long turn pays for every
tool the agent may use, and models choose worse among many. Requests carry the core set
(``CORE_TOOLS``) and what the agent keeps active; a deferred tool stays available: its name is
listed (grouped by server, in the cacheable part of the system prompt) and ``load_tools`` adds its
definition for the rest of the session.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from fnmatch import fnmatchcase

from specifications.interfaces.agent import CORE_TOOLS, ToolLoading
from specifications.interfaces.llm import ToolDefinition

# Never deferred: how the agent finds and loads the rest.
ALWAYS_LOADED = frozenset({"load_tools", "search_tools", "load_guidance"})
# The tools a platform gate sends the agent to when it refuses a call: an unknown or deferred tool
# (`search_tools`, `load_tools`), an MCP tool whose server has guidance (`load_guidance`), a
# change in plan mode (`exit_plan_mode`). No agent tool list, Slack allowlist or channel profile
# can take them away, or the refusal would be a dead end; a list written before one existed made
# every MCP tool with guidance unusable. Each is still offered only when it has work to do, and
# the platform itself denies one when it has none (`load_guidance` without guidance). Tools that
# merely improve a session (the checklist, skills) are not here: an administrator may leave them
# out of a narrow agent on purpose.
ESSENTIAL_TOOLS = ALWAYS_LOADED | frozenset({"exit_plan_mode"})
MCP_PREFIX = "mcp__"


def is_active(name: str, patterns: Iterable[str]) -> bool:
    return name in ALWAYS_LOADED or any(fnmatchcase(name, p) for p in patterns)


def deferred_names(
    definitions: Sequence[ToolDefinition],
    loading: ToolLoading,
    enabled_for_turn: Callable[[str], bool] = lambda _name: False,
) -> frozenset[str]:
    """The tools of ``definitions`` the agent must load before their definition is sent.

    Every allowed tool that is neither core, nor kept active by the agent, nor a loading tool,
    nor switched on for this turn by its session or transport (``enabled_for_turn``).
    """
    if loading.send_all:
        return frozenset()
    return frozenset(
        d.name
        for d in definitions
        if d.name not in CORE_TOOLS
        and not is_active(d.name, loading.active)
        and not enabled_for_turn(d.name)
    )


def sent_count(definitions: Sequence[ToolDefinition], loading: ToolLoading) -> int:
    """How many of ``definitions`` every request carries under ``loading``."""
    return len(definitions) - len(deferred_names(definitions, loading))


def server_of(name: str) -> str:
    """``mcp__<server>__<tool>`` -> ``<server>``; anything else is the platform's own."""
    if name.startswith(MCP_PREFIX):
        rest = name[len(MCP_PREFIX) :]
        return rest.split("__", 1)[0] if "__" in rest else rest
    return "platform"


def catalogue(names: Iterable[str]) -> str:
    """The deferred tools by name, one line per server, for the system prompt."""
    by_server: dict[str, list[str]] = {}
    for name in sorted(names):
        by_server.setdefault(server_of(name), []).append(name)
    if not by_server:
        return ""
    lines = [
        "\n[Tools to load before use]",
        "- These tools exist but their definitions are not loaded. Call `load_tools` with the "
        "names you need (several at once), then call them; they stay loaded for the rest of "
        "the session. `search_tools` searches their descriptions.",
    ]
    for server, tools in sorted(by_server.items()):
        lines.append(f"  - {server} ({len(tools)}): " + ", ".join(tools))
    return "\n".join(lines)

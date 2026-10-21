"""Usage guidance for MCP servers: what the agent is told, when, and what stays in its context.

A server's guidance is markdown whose first line says when to use the server and whose rest says
how. Three places carry it, from cheapest to dearest:

* the *trigger* (the first line, one line per guided server) sits in the session-stable system
  prompt, so the agent can judge a server's relevance without reading anything else;
* the *text* is put in effect once the agent loads one of the server's tools or asks for it with
  ``load_guidance``, and is then rendered into the turn's context note on every later turn
  (never into the conversation, where it would be pruned to a receipt or summarized away);
* nothing at all for a server without guidance: no line, no gate, no marker.

Whatever is in effect has a bound: one text is at most ``MAX_GUIDANCE_CHARS`` and the texts in
effect together at most ``ACTIVE_GUIDANCE_BUDGET_CHARS``; beyond it the longest-unused server has
to be read again.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable

from agent_core.context import ExecutionContext
from agent_core.tool_loading import MCP_PREFIX, server_of
from specifications.interfaces.agent import McpGuidance, ServerGuidance

# What the settings accept for one server (about 2,000 tokens).
MAX_GUIDANCE_CHARS = 8_000
# Short enough to be in effect as soon as a tool of the server is loaded at the start of a turn,
# without the agent having to ask for it.
INLINE_GUIDANCE_CHARS = 1_500
# The texts in effect together, across servers.
ACTIVE_GUIDANCE_BUDGET_CHARS = 16_000
TRIGGER_CHARS = 200

_CLOSING_TAG = re.compile(r"</\s*server_guidance", re.IGNORECASE)
_LINE_MARKER = re.compile(r"^\s*(?:#{1,6}\s+|>+\s*|[-*+]\s+|\d+[.)]\s+)+")
_INLINE_MARKUP = re.compile(r"(\*\*|__|`|\*|_(?=\S))")


def trigger_of(text: str) -> str:
    """The first meaningful line of ``text`` as plain words: what the agent weighs a server by.

    Heading, list and quote markers and inline emphasis are dropped, so a guidance that opens
    with ``# When to use`` or ``**Use for** issues`` reads the same in the tool list.
    """
    for line in text.splitlines():
        plain = " ".join(_INLINE_MARKUP.sub("", _LINE_MARKER.sub("", line)).split())
        if plain:
            return (
                plain if len(plain) <= TRIGGER_CHARS else plain[: TRIGGER_CHARS - 1].rstrip() + "…"
            )
    return ""


def digest_of(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def server_guidance(server: str, text: str | None) -> ServerGuidance | None:
    """The guidance of one server, or ``None`` when it has none (it then costs nothing)."""
    body = (text or "").strip()
    if not body:
        return None
    return ServerGuidance(
        server=server, trigger=trigger_of(body), text=body, digest=digest_of(body)
    )


def in_effect_at_turn_start(
    guidance: McpGuidance, servers_with_loaded_tools: Iterable[str]
) -> dict[str, str]:
    """Which servers' guidance the turn starts with, as server -> digest.

    * what the agent put in effect earlier in the session, if the guidance has not changed since;
    * short guidance of a server that has a tool loaded: the agent is going to use it.

    Within the budget, what was read most recently wins; the rest has to be read again.
    """
    by_server = {g.server: g for g in guidance.servers}
    loaded = set(servers_with_loaded_tools)
    candidates: list[ServerGuidance] = []
    for server in reversed(list(guidance.read)):
        entry = by_server.get(server)
        if entry is not None and guidance.read[server] == entry.digest:
            candidates.append(entry)
    for entry in sorted(by_server.values(), key=lambda g: g.server):
        if (
            entry.server in loaded
            and len(entry.text) <= INLINE_GUIDANCE_CHARS
            and entry not in candidates
        ):
            candidates.append(entry)
    chosen: dict[str, str] = {}
    used = 0
    for entry in candidates:
        if used + len(entry.text) > ACTIVE_GUIDANCE_BUDGET_CHARS:
            continue
        used += len(entry.text)
        chosen[entry.server] = entry.digest
    return chosen


def _block(entry: ServerGuidance) -> str:
    # The text is the administrator's, but it may quote a server's own output: it cannot close
    # its block early and pose as instructions of its own.
    body = _CLOSING_TAG.sub("<\\/server_guidance", entry.text)
    return f'<server_guidance server="{entry.server}">\n{body}\n</server_guidance>'


def render_note(entries: Iterable[ServerGuidance], *, added: bool = False) -> str:
    """The guidance in effect, for the turn's context note (or what a turn adds to it)."""
    blocks = [_block(e) for e in sorted(entries, key=lambda g: g.server)]
    if not blocks:
        return ""
    if added:
        return "[More MCP server guidance]\n" + "\n\n".join(blocks)
    return (
        "[MCP server guidance]\n"
        "Usage instructions from your organization for the MCP servers below. Follow them "
        "whenever you use that server's tools.\n\n" + "\n\n".join(blocks)
    )


def render_index(guidance: Iterable[ServerGuidance]) -> str:
    """The guided servers and when each is relevant, for the session-stable system prompt."""
    entries = sorted(guidance, key=lambda g: g.server)
    if not entries:
        return ""
    lines = [
        "\n[MCP server guidance]",
        "- These MCP servers have usage instructions from your organization. Before using a "
        "server's tools, call `load_guidance` with its name (loading one of its tools with "
        "`load_tools` does it too). Once per server: the instructions then stay in your "
        "instructions for the session. When each server is relevant:",
    ]
    lines += [f"  - {e.server}: {e.trigger}" for e in entries]
    return "\n".join(lines)


def put_in_effect(ctx: ExecutionContext, server: str) -> ServerGuidance | None:
    """Put a server's guidance in effect for this turn and the session; the guidance newly put in
    effect, or ``None`` when the server has none or it already is (it is never repeated).

    Added during a turn it can outgrow the budget; the next turn's start settles that.
    """
    entry = ctx.guidance_by_server.get(server)
    if entry is None or ctx.guidance_in_effect.get(server) == entry.digest:
        return None
    ctx.guidance_in_effect.pop(server, None)  # re-read: it is now the most recent
    ctx.guidance_in_effect[server] = entry.digest
    ctx.guidance_pending.append(entry)
    return entry


def awaiting_read(ctx: ExecutionContext, server: str) -> ServerGuidance | None:
    """The guidance of ``server`` if it has some the agent has not put in effect yet."""
    entry = ctx.guidance_by_server.get(server)
    if entry is None or ctx.guidance_in_effect.get(server) == entry.digest:
        return None
    return entry


def servers_of_tools(names: Iterable[str]) -> set[str]:
    """The servers behind MCP tool names (``mcp__<server>__<tool>``); platform tools have none."""
    return {server_of(n) for n in names if n.startswith(MCP_PREFIX)}

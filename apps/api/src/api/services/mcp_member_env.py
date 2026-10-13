"""Members' own values for an organization command server's environment variables.

An administrator registers a command server with variables (a base URL, a read-only API key) and
may mark some as overridable: each member can then run the server with their own value (their
own key) without seeing or changing anyone else's. A variable the administrator left empty is
required from the member; a member who has not set it does not get the server at all, rather
than a server running with someone else's credentials.

The member's values are encrypted by the caller and kept in the per-member token store.
"""

from __future__ import annotations

from typing import Any

from api.services.mcp_member_tokens import McpMemberTokenStore

ENV_KEY = "env"


def overridable_names(auth: dict[str, Any]) -> list[str]:
    return [h["name"] for h in auth.get("headers") or [] if h.get("member_override")]


async def member_values(
    store: McpMemberTokenStore, org_id: str, server_id: str, user_id: str
) -> dict[str, str]:
    """The member's encrypted overrides for one server."""
    stored = await store.get(org_id, server_id, user_id) or {}
    return dict(stored.get(ENV_KEY) or {})


async def save_member_values(
    store: McpMemberTokenStore,
    org_id: str,
    server_id: str,
    user_id: str,
    encrypted: dict[str, str],
) -> None:
    if encrypted:
        await store.put(org_id, server_id, user_id, {ENV_KEY: encrypted})
    else:
        await store.delete(org_id, server_id, user_id)


def apply_member_values(auth: dict[str, Any], encrypted: dict[str, str]) -> dict[str, Any] | None:
    """``auth`` with the member's values in place of the organization's, or ``None`` when the
    member lacks a value the administrator left for them to provide."""
    headers: list[dict[str, Any]] = []
    for header in auth.get("headers") or []:
        if not header.get("member_override"):
            headers.append(header)
        elif header["name"] in encrypted:
            headers.append({**header, "value": encrypted[header["name"]], "is_secret": True})
        elif header.get("value"):
            headers.append(header)
        else:
            return None
    return {**auth, "headers": headers}

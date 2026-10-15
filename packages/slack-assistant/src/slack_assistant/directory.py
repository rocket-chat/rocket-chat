"""Slack directory tools: user groups (teams), people, channel members and permalinks.

Reads only. People are described without their e-mail address, which may be used to find
someone but is never disclosed. Reading a conversation other than the one the agent was asked
in is limited to public channels and the organization's allowlisted channels: a private channel
or another person's DM the bot happens to be in is never readable from elsewhere.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from slack_assistant.state import SlackAssistantState

logger = logging.getLogger(__name__)

# Slack pages at most 1000 items per call; a few pages cover a large workspace.
PAGE_SIZE = 200
MAX_ITEMS = 2000
MAX_MATCHES = 5
MAX_MEMBERS_LISTED = 100

ToolSpec = tuple[str, str, dict[str, Any], Callable[..., Awaitable[str]]]


def _data(response: Any) -> dict[str, Any]:
    data = response.data if hasattr(response, "data") else response
    return data if isinstance(data, dict) else {}


def person_line(member: dict[str, Any]) -> str:
    profile = member.get("profile") or {}
    name = member.get("real_name") or profile.get("real_name") or member.get("name") or "?"
    handle = profile.get("display_name") or member.get("name") or ""
    details = [f"@{handle}" if handle else "", profile.get("title") or ""]
    if member.get("deleted"):
        details.append("deactivated")
    if member.get("is_bot"):
        details.append("bot")
    suffix = ", ".join(d for d in details if d)
    return f"{member.get('id')} — {name}" + (f" ({suffix})" if suffix else "")


class SlackDirectoryMixin(SlackAssistantState):
    """User groups, people, channel members and permalinks for the agent's Slack toolset."""

    def _slack_directory_specs(self) -> list[ToolSpec]:
        return [
            (
                "slack_list_user_groups",
                "List the workspace's Slack user groups (teams such as @backend or @oncall): "
                "their id, handle, name, member count and how to mention them in a message. "
                "Optionally filter by text in the handle, name or description.",
                {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Text to filter groups by."},
                        "include_disabled": {
                            "type": "boolean",
                            "description": "Include disabled groups (default: false).",
                        },
                    },
                },
                self._tool_list_user_groups,
            ),
            (
                "slack_get_user_group_members",
                "List the members of a Slack user group, by its id (S…) or handle (@backend).",
                {
                    "type": "object",
                    "properties": {
                        "group": {"type": "string", "description": "Group id or handle."},
                    },
                    "required": ["group"],
                },
                self._tool_get_user_group_members,
            ),
            (
                "slack_get_user",
                "Describe a Slack user: name, handle, title, timezone, status and the user "
                "groups they belong to. Accepts a user id, name, handle or email.",
                {
                    "type": "object",
                    "properties": {
                        "user": {"type": "string", "description": "User id, name or email."},
                    },
                    "required": ["user"],
                },
                self._tool_get_user,
            ),
            (
                "slack_list_channel_members",
                "List the members of a Slack channel (the current one by default). Readable "
                "channels: the current conversation, public channels and allowlisted channels.",
                {
                    "type": "object",
                    "properties": {
                        "channel": {"type": "string", "description": "Channel id (optional)."},
                    },
                },
                self._tool_list_channel_members,
            ),
            (
                "slack_get_permalink",
                "Get a shareable link to a Slack message.",
                {
                    "type": "object",
                    "properties": {
                        "channel": {"type": "string", "description": "Channel id."},
                        "ts": {"type": "string", "description": "Message timestamp."},
                    },
                    "required": ["channel", "ts"],
                },
                self._tool_get_permalink,
            ),
        ]

    async def _paged(self, method: str, key: str, **params: Any) -> list[Any]:
        """Every item of a cursor-paginated Slack listing, up to ``MAX_ITEMS``."""
        call = getattr(self.app.client, method)
        items: list[Any] = []
        cursor = ""
        while len(items) < MAX_ITEMS:
            data = _data(await call(limit=PAGE_SIZE, cursor=cursor or None, **params))
            items.extend(data.get(key) or [])
            cursor = str((data.get("response_metadata") or {}).get("next_cursor") or "")
            if not cursor:
                break
        return items[:MAX_ITEMS]

    async def _user_groups(
        self, include_disabled: bool = False, include_users: bool = False
    ) -> list[dict[str, Any]]:
        data = _data(
            await self.app.client.usergroups_list(
                include_count=True, include_disabled=include_disabled, include_users=include_users
            )
        )
        return [g for g in data.get("usergroups") or [] if isinstance(g, dict)]

    async def _find_users(self, query: str) -> list[dict[str, Any]]:
        """Users matching ``query``: an exact id, name, handle or e-mail first, then partial names."""
        needle = query.strip().lstrip("@").lower()
        exact: list[dict[str, Any]] = []
        partial: list[dict[str, Any]] = []
        for member in await self._paged("users_list", "members"):
            if not isinstance(member, dict):
                continue
            profile = member.get("profile") or {}
            keys = [
                str(member.get("id", "")),
                str(member.get("name", "")),
                str(member.get("real_name", "")),
                str(profile.get("display_name", "")),
                str(profile.get("email", "")),
            ]
            lowered = [k.lower() for k in keys if k]
            if needle in lowered:
                exact.append(member)
            elif any(needle in k for k in lowered[:4]):
                partial.append(member)
        return exact or partial

    async def _tool_list_user_groups(self, query: str = "", include_disabled: bool = False) -> str:
        if not self._slack_api_ready:
            return "Error: Slack API client is not connected."
        try:
            groups = await self._user_groups(include_disabled)
        except Exception as err:
            logger.warning("slack_list_user_groups failed: %s", err)
            return f"Error: unable to list user groups ({err}). The app needs usergroups:read."
        needle = query.strip().lstrip("@").lower()
        lines = [
            f"- {g.get('id')} @{g.get('handle')} — {g.get('name')} "
            f"({g.get('user_count', '?')} members; mention as <!subteam^{g.get('id')}>)"
            + (f": {g.get('description')}" if g.get("description") else "")
            for g in groups
            if not needle
            or any(
                needle in str(g.get(field) or "").lower()
                for field in ("handle", "name", "description")
            )
        ]
        if not lines:
            return f"No user group matched '{query}'." if needle else "No user groups found."
        return "User groups:\n" + "\n".join(lines)

    async def _tool_get_user_group_members(self, group: str) -> str:
        if not group.strip():
            return "Error: group is required."
        if not self._slack_api_ready:
            return "Error: Slack API client is not connected."
        try:
            wanted = group.strip().lstrip("@").lower()
            match = next(
                (
                    g
                    for g in await self._user_groups(include_disabled=True)
                    if wanted in (str(g.get("id", "")).lower(), str(g.get("handle", "")).lower())
                ),
                None,
            )
            if match is None:
                return f"No user group '{group}'. Use slack_list_user_groups to find it."
            data = _data(await self.app.client.usergroups_users_list(usergroup=match["id"]))
            names = [
                f"- {user_id} — {await self._resolve_slack_user_name(user_id)}"
                for user_id in (data.get("users") or [])[:MAX_MEMBERS_LISTED]
            ]
        except Exception as err:
            logger.warning("slack_get_user_group_members failed: %s", err)
            return f"Error: unable to list the group's members ({err})."
        header = (
            f"@{match.get('handle')} ({match.get('name')}), {len(data.get('users') or [])} members:"
        )
        return "\n".join([header, *names]) if names else f"@{match.get('handle')} has no members."

    async def _tool_get_user(self, user: str) -> str:
        if not user.strip():
            return "Error: user is required."
        if not self._slack_api_ready:
            return "Error: Slack API client is not connected."
        try:
            found = await self._find_users(user)
            if not found:
                return f"No Slack user matched '{user}'."
            if len(found) > 1:
                options = "\n".join(f"- {person_line(m)}" for m in found[:MAX_MATCHES])
                return f"Several users match '{user}':\n{options}"
            return await self._describe_user(found[0])
        except Exception as err:
            logger.warning("slack_get_user failed: %s", err)
            return f"Error: unable to look the user up ({err})."

    async def _describe_user(self, member: dict[str, Any]) -> str:
        profile = member.get("profile") or {}
        lines = [person_line(member)]
        if member.get("tz"):
            lines.append(f"Timezone: {member.get('tz_label') or member.get('tz')}")
        status = " ".join(
            s for s in (profile.get("status_emoji"), profile.get("status_text")) if s
        ).strip()
        if status:
            lines.append(f"Status: {status}")
        try:
            groups = [
                f"@{g.get('handle')}"
                for g in await self._user_groups(include_users=True)
                if member.get("id") in (g.get("users") or [])
            ]
        except Exception:
            # Membership needs usergroups:read; the profile alone is still useful.
            groups = []
        if groups:
            lines.append("User groups: " + ", ".join(groups))
        return "\n".join(lines)

    async def _readable_channel(self, channel: str) -> bool:
        """Whether the agent may read ``channel`` from the conversation it is working in."""
        current = self._current_slack_context()
        if current is not None and channel == current[0]:
            return True
        if self._outbound_channel_allowed(channel):
            return True
        info = (_data(await self.app.client.conversations_info(channel=channel))).get("channel")
        info = info if isinstance(info, dict) else {}
        return bool(info.get("is_channel")) and not info.get("is_private")

    async def _tool_list_channel_members(self, channel: str = "") -> str:
        if not self._slack_api_ready:
            return "Error: Slack API client is not connected."
        target = channel.strip() or (self._current_slack_context() or ("", ""))[0]
        if not target:
            return "Error: give the channel id."
        try:
            if not await self._readable_channel(target):
                return f"Blocked: {target} is private and not the conversation you are in."
            members = await self._paged("conversations_members", "members", channel=target)
            names = [
                f"- {user_id} — {await self._resolve_slack_user_name(user_id)}"
                for user_id in members[:MAX_MEMBERS_LISTED]
                if isinstance(user_id, str)
            ]
        except Exception as err:
            logger.warning("slack_list_channel_members failed: %s", err)
            return f"Error: unable to list the channel's members ({err})."
        more = len(members) - len(names)
        tail = f"\n… and {more} more." if more > 0 else ""
        return f"{len(members)} members of {target}:\n" + "\n".join(names) + tail

    async def _tool_get_permalink(self, channel: str, ts: str) -> str:
        if not self._slack_api_ready:
            return "Error: Slack API client is not connected."
        try:
            if not await self._readable_channel(channel):
                return f"Blocked: {channel} is private and not the conversation you are in."
            data = _data(await self.app.client.chat_getPermalink(channel=channel, message_ts=ts))
        except Exception as err:
            logger.warning("slack_get_permalink failed: %s", err)
            return f"Error: unable to get the link ({err})."
        return str(data.get("permalink") or "No link returned.")

"""The Slack toolset: thread/channel reads, discovery and policy-gated outbound actions."""

import functools
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from agent_core.context import current_execution_ctx

from slack_assistant.directory import MAX_MATCHES, SlackDirectoryMixin, person_line
from slack_assistant.thread_context import (
    build_channel_transcript,
    build_thread_transcript,
)
from specifications.interfaces.llm import ToolDefinition

logger = logging.getLogger(__name__)

# The live assistant of each bot, by its session key. The Slack tools are registered once on
# the process-wide tool registry, so a call must run on the assistant of the bot whose session
# is executing (its Slack client, policy and conversations), never on whichever registered
# first: bots restart when edited, and an organization can run several.
_LIVE_BOTS: dict[str, "SlackToolsetMixin"] = {}
NOT_CONNECTED = (
    "Slack is not connected for this session: no Slack bot of this organization is running."
)


class SlackToolsetMixin(SlackDirectoryMixin):
    """The Slack toolset: thread/channel reads, discovery and policy-gated outbound actions."""

    def _go_live(self) -> None:
        if self.bot is not None:
            _LIVE_BOTS[self.bot.session_key] = self

    def _go_offline(self) -> None:
        if self.bot is not None and _LIVE_BOTS.get(self.bot.session_key) is self:
            del _LIVE_BOTS[self.bot.session_key]

    def _session_owner(self) -> "SlackToolsetMixin | None":
        """The assistant whose bot owns the executing session; an assistant without a bot (a
        single embedded one, or tests) owns every session.

        A session no bot started (the web cockpit, a trigger) whose agent lists Slack tools
        uses its organization's bot, the first by id when it runs several, so the choice is the
        same on every call and every replica.
        """
        ctx = current_execution_ctx.get()
        session_id = ctx.session_id if ctx is not None else ""
        for key, assistant in _LIVE_BOTS.items():
            if session_id.startswith(f"slack_{key}_"):
                return assistant
        if self.bot is None:
            return self
        if ctx is None or session_id.startswith("slack_"):
            return None
        organization = [
            assistant
            for assistant in _LIVE_BOTS.values()
            if assistant.bot is not None and assistant.bot.org_id == ctx.tenant_org_id
        ]
        return min(organization, key=lambda a: a.bot.id if a.bot else "", default=None)

    def _dispatching(self, handler: Callable[..., Awaitable[str]]) -> Callable[..., Awaitable[str]]:
        """``handler`` run on the session's own assistant (same signature, for the schema)."""
        name = handler.__name__

        @functools.wraps(handler)
        async def call(*args: Any, **kwargs: Any) -> str:
            owner = self._session_owner()
            if owner is None:
                return NOT_CONNECTED
            result: str = await getattr(owner, name)(*args, **kwargs)
            return result

        return call

    def _register_slack_context_tools(self) -> None:
        """Register on-demand Slack thread/channel history tools on the shared registry.

        The tools resolve the active channel and thread from the execution
        context, allowing the agent to pull additional history beyond the
        bounded transcript that is auto-injected into its system prompt.
        """
        if not self.tool_registry:
            return
        registered = getattr(self.tool_registry, "_dynamic_tools", {})
        if "slack_read_thread" in registered:
            return

        self.tool_registry.register_dynamic_tool(
            ToolDefinition(
                name="slack_read_thread",
                description=(
                    "Fetch the full transcript of a Slack thread, including messages that did "
                    "not mention you: the current thread by default, or another one (channel + "
                    "thread_ts) in a public or allowlisted channel."
                ),
                parameters_json_schema={
                    "type": "object",
                    "properties": {
                        "channel": {
                            "type": "string",
                            "description": "Channel id of another thread (optional).",
                        },
                        "thread_ts": {
                            "type": "string",
                            "description": "Timestamp of that thread's first message.",
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Maximum number of messages to retrieve (default: 50).",
                        },
                    },
                },
            ),
            self._dispatching(self._tool_read_thread),
            toolset="slack",
        )
        self.tool_registry.register_dynamic_tool(
            ToolDefinition(
                name="slack_read_channel_history",
                description=(
                    "Fetch recent messages from a Slack channel (the current one by default, "
                    "or a public or allowlisted channel), optionally before a timestamp."
                ),
                parameters_json_schema={
                    "type": "object",
                    "properties": {
                        "channel": {
                            "type": "string",
                            "description": "Channel id to read instead of the current one.",
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Maximum number of messages to retrieve (default: 50).",
                        },
                        "before_ts": {
                            "type": "string",
                            "description": "Optional message timestamp upper bound (exclusive).",
                        },
                    },
                },
            ),
            self._dispatching(self._tool_read_channel_history),
            toolset="slack",
        )

        # Outbound and discovery tools, all part of the same session-scoped "slack"
        # toolset: a session that enables the toolset gets them for every turn (web
        # included), and they are policy-gated before any Slack API call.
        for name, description, schema, handler in self._slack_tool_specs():
            self.tool_registry.register_dynamic_tool(
                ToolDefinition(name=name, description=description, parameters_json_schema=schema),
                self._dispatching(handler),
                toolset="slack",
            )

    def _slack_tool_specs(self) -> list[tuple[str, str, dict[str, Any], Any]]:
        """The outbound/discovery half of the Slack toolset (name, doc, schema, handler)."""
        return [*self._slack_core_specs(), *self._slack_directory_specs()]

    def _slack_core_specs(self) -> list[tuple[str, str, dict[str, Any], Any]]:
        return [
            (
                "slack_list_conversations",
                "List Slack conversations (channels, DMs) visible to the bot. Use it to find "
                "where to read or post before acting.",
                {
                    "type": "object",
                    "properties": {
                        "types": {
                            "type": "string",
                            "description": (
                                "Comma-separated conversation types "
                                "(default: public_channel,private_channel,im,mpim)."
                            ),
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Max conversations (default: 50).",
                        },
                    },
                },
                self._tool_list_conversations,
            ),
            (
                "slack_resolve_user",
                "Resolve a Slack user's id from their name, handle, real name or email "
                "(partial names give the closest candidates). slack_get_user gives details.",
                {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Name, real name or email."},
                    },
                    "required": ["query"],
                },
                self._tool_resolve_user,
            ),
            (
                "slack_send_message",
                "Post a message to an allowlisted Slack channel or thread. Policy-gated.",
                {
                    "type": "object",
                    "properties": {
                        "channel": {"type": "string", "description": "Channel id to post to."},
                        "text": {"type": "string", "description": "Message text (mrkdwn)."},
                        "thread_ts": {
                            "type": "string",
                            "description": "Optional thread timestamp to reply in-thread.",
                        },
                    },
                    "required": ["channel", "text"],
                },
                self._tool_send_message,
            ),
            (
                "slack_send_dm",
                "Send a direct message to a Slack user. Restricted by the workspace DM policy "
                "(none / paired-only / all).",
                {
                    "type": "object",
                    "properties": {
                        "user": {"type": "string", "description": "Slack user id or email."},
                        "text": {"type": "string", "description": "Message text (mrkdwn)."},
                    },
                    "required": ["user", "text"],
                },
                self._tool_send_dm,
            ),
            (
                "slack_add_reaction",
                "Add an emoji reaction to a Slack message. Policy-gated.",
                {
                    "type": "object",
                    "properties": {
                        "channel": {"type": "string", "description": "Channel id."},
                        "ts": {"type": "string", "description": "Message timestamp."},
                        "emoji": {"type": "string", "description": "Emoji name, without colons."},
                    },
                    "required": ["channel", "ts", "emoji"],
                },
                self._tool_add_reaction,
            ),
        ]

    def _current_slack_context(self) -> tuple[str, str] | None:
        """Resolve the active (channel_id, thread_ts) pair for the executing session."""
        ctx = current_execution_ctx.get()
        session_id = ctx.session_id if ctx else None
        if session_id and session_id in self._session_meta:
            return self._session_meta[session_id]
        return None

    async def _fetch_slack_user(self, user_id: str) -> dict[str, Any] | None:
        """Fetch and cache a Slack user profile, returning ``None`` when unavailable."""
        if not user_id:
            return None
        cached = self._slack_user_cache.get(user_id)
        if cached is not None:
            return cached
        try:
            response = await self.app.client.users_info(user=user_id)
            data = response.data if hasattr(response, "data") else response
            user = data.get("user") if isinstance(data, dict) else None
            if isinstance(user, dict):
                self._slack_user_cache[user_id] = user
                return user
        except Exception as err:
            logger.debug("Failed fetching Slack user %s: %s", user_id, err)
        return None

    async def _resolve_slack_user_name(self, user_id: str) -> str:
        """Resolve a Slack user ID to a human-readable display name."""
        if not user_id:
            return "unknown"
        if self.bot_user_id and user_id == self.bot_user_id:
            return "Rocket"
        user = await self._fetch_slack_user(user_id)
        profile = user.get("profile") if isinstance(user, dict) else None
        if isinstance(profile, dict):
            for key in ("display_name", "real_name"):
                value = profile.get(key)
                if value:
                    return str(value)
        if isinstance(user, dict):
            for key in ("real_name", "name"):
                value = user.get(key)
                if value:
                    return str(value)
        return f"@{user_id}"

    async def _load_thread_context(self, channel_id: str, thread_ts: str) -> str:
        """Fetch a bounded transcript of the current thread for prompt injection."""
        if not self._slack_api_ready:
            return ""
        try:
            return await build_thread_transcript(
                self.app.client,
                channel_id,
                thread_ts,
                self._resolve_slack_user_name,
                limit=self.settings.slack_thread_context_limit,
                max_chars=self.settings.slack_thread_context_max_chars,
                bot_user_id=self.bot_user_id,
                include_own_replies=False,
            )
        except Exception as err:
            logger.debug("Failed loading Slack thread context: %s", err)
            return ""

    async def _read_target(
        self, channel: str | None, thread_ts: str | None
    ) -> tuple[str, str] | str:
        """The (channel, thread) to read, or why it may not be read."""
        current = self._current_slack_context()
        if not channel:
            return current if current else "Error: no active Slack thread context is available."
        if not await self._readable_channel(channel):
            return f"Blocked: {channel} is private and not the conversation you are in."
        return channel, thread_ts or ""

    async def _tool_read_thread(
        self, limit: int = 50, channel: str | None = None, thread_ts: str | None = None
    ) -> str:
        """Tool handler: fetch and render a thread transcript (the current one by default)."""
        if channel and not thread_ts:
            return "Error: give the thread_ts of the thread to read in that channel."
        if not self._slack_api_ready:
            return "Error: Slack API client is not connected."
        try:
            target = await self._read_target(channel, thread_ts)
            if isinstance(target, str):
                return target
            transcript = await build_thread_transcript(
                self.app.client,
                target[0],
                target[1],
                self._resolve_slack_user_name,
                limit=limit,
                max_chars=self.settings.slack_read_tool_max_chars,
                bot_user_id=self.bot_user_id,
            )
            return transcript or "The Slack thread is empty."
        except Exception as err:
            logger.warning("slack_read_thread failed: %s", err)
            return f"Error: unable to read Slack thread ({err})."

    async def _tool_read_channel_history(
        self, limit: int = 50, before_ts: str | None = None, channel: str | None = None
    ) -> str:
        """Tool handler: fetch and render recent history of a channel (the current one by default)."""
        if not self._slack_api_ready:
            return "Error: Slack API client is not connected."
        try:
            target = await self._read_target(channel, None)
            if isinstance(target, str):
                return target
            transcript = await build_channel_transcript(
                self.app.client,
                target[0],
                self._resolve_slack_user_name,
                limit=limit,
                latest=before_ts,
                max_chars=self.settings.slack_read_tool_max_chars,
                bot_user_id=self.bot_user_id,
            )
            return transcript or "No recent channel history was found."
        except Exception as err:
            logger.warning("slack_read_channel_history failed: %s", err)
            return f"Error: unable to read Slack channel history ({err})."

    def _dm_policy(self) -> str:
        ctx = current_execution_ctx.get()
        session = self._session_policy.get(ctx.session_id) if ctx is not None else None
        if session is not None and session[0]:
            return session[0]
        policy = (self.settings.slack_dm_policy or "paired").strip().lower()
        return policy if policy in ("none", "paired", "all") else "paired"

    def _outbound_channel_allowed(self, channel_id: str) -> bool:
        """Whether the agent may post or react in ``channel_id``.

        Default-deny: with no organization allowlist the agent may only act in the
        conversation it was invoked from, never in arbitrary channels.
        """
        ctx = current_execution_ctx.get()
        session = self._session_policy.get(ctx.session_id) if ctx is not None else None
        if session is not None:
            channels = session[1]
        else:
            channels = {
                item.strip()
                for item in (self.settings.slack_outbound_channels or "").split(",")
                if item.strip()
            }
        if channels:
            return channel_id in channels
        current = self._current_slack_context()
        return current is not None and channel_id == current[0]

    def _is_paired(self, target: str) -> bool:
        candidate = target.strip()
        return candidate in self._user_pairings or candidate in set(self._user_pairings.values())

    async def _audit(self, event_type: str, payload: dict[str, Any]) -> None:
        """Record an outbound Slack action (durable when the API layer wires an audit fn)."""
        if self._audit_fn is not None:
            try:
                await self._audit_fn(event_type, payload)
                return
            except Exception as err:  # never let auditing break the action's result
                logger.warning("Slack audit hook failed for %s: %s", event_type, err)
        logger.info("slack_audit %s %s", event_type, payload)

    async def _tool_list_conversations(
        self,
        types: str = "public_channel,private_channel,im,mpim",
        limit: int = 50,
    ) -> str:
        if not self._slack_api_ready:
            return "Error: Slack API client is not connected."
        try:
            # Direct-message listings expose who talks to whom; only list them when the
            # organization lets the agent DM everyone.
            requested = [t.strip() for t in types.split(",") if t.strip()]
            if self._dm_policy() != "all":
                requested = [t for t in requested if t not in ("im", "mpim")]
            raw = await self.app.client.conversations_list(
                types=",".join(requested) or "public_channel", limit=min(max(limit, 1), 200)
            )
            channels: list[Any] = list(raw.get("channels") or [])
            if not channels:
                return "No conversations returned."
            lines = [f"- {c.get('id')} #{c.get('name') or c.get('user') or 'dm'}" for c in channels]
            return "Conversations:\n" + "\n".join(lines)
        except Exception as err:
            logger.warning("slack_list_conversations failed: %s", err)
            return f"Error: unable to list conversations ({err})."

    async def _tool_resolve_user(self, query: str) -> str:
        if not query.strip():
            return "Error: query is required."
        if not self._slack_api_ready:
            return "Error: Slack API client is not connected."
        try:
            found = await self._find_users(query)
        except Exception as err:
            logger.warning("slack_resolve_user failed: %s", err)
            return f"Error: unable to resolve user ({err})."
        if not found:
            return f"No Slack user matched '{query}'."
        # The e-mail may be used to find someone but is not disclosed.
        lines = [person_line(member) for member in found[:MAX_MATCHES]]
        if len(lines) == 1:
            return lines[0]
        return f"Several users match '{query}':\n" + "\n".join(f"- {line}" for line in lines)

    async def _tool_send_message(
        self, channel: str, text: str, thread_ts: str | None = None
    ) -> str:
        if not text.strip():
            return "Error: text is required."
        if not self._outbound_channel_allowed(channel):
            return (
                f"Blocked: posting to channel {channel} is not permitted by the workspace "
                "policy (channel not in the outbound allowlist)."
            )
        if not self._slack_api_ready:
            return "Error: Slack API client is not connected."
        try:
            resp = await self.app.client.chat_postMessage(
                channel=channel, text=text, thread_ts=thread_ts
            )
            await self._audit("slack_send_message", {"channel": channel, "ts": resp.get("ts")})
            return f"Message posted to {channel} (ts={resp.get('ts')})."
        except Exception as err:
            logger.warning("slack_send_message failed: %s", err)
            return f"Error: unable to post message ({err})."

    async def _tool_send_dm(self, user: str, text: str) -> str:
        policy = self._dm_policy()
        if policy == "none":
            return "Blocked: direct messages are disabled by the workspace policy (dm=NO)."
        if not text.strip():
            return "Error: text is required."
        target = user.strip()
        if policy == "paired" and not self._is_paired(target):
            return (
                "Blocked: direct messages are limited to Slack users linked to a Rocket "
                "account (dm=PAIRED ONLY)."
            )
        if not self._slack_api_ready:
            return "Error: Slack API client is not connected."
        try:
            opened = await self.app.client.conversations_open(users=target)
            channel = (opened.get("channel") or {}).get("id")
            if not channel:
                return f"Error: could not open a DM with {target}."
            resp = await self.app.client.chat_postMessage(channel=channel, text=text)
            await self._audit(
                "slack_send_dm", {"user": target, "channel": channel, "ts": resp.get("ts")}
            )
            return f"DM sent to {target}."
        except Exception as err:
            logger.warning("slack_send_dm failed: %s", err)
            return f"Error: unable to send DM ({err})."

    async def _tool_add_reaction(self, channel: str, ts: str, emoji: str) -> str:
        if not self._outbound_channel_allowed(channel):
            return f"Blocked: reacting on {channel} is not permitted by the workspace policy."
        if not self._slack_api_ready:
            return "Error: Slack API client is not connected."
        try:
            name = emoji.strip().strip(":")
            await self.app.client.reactions_add(channel=channel, timestamp=ts, name=name)
            await self._audit("slack_add_reaction", {"channel": channel, "ts": ts, "emoji": name})
            return f"Reaction :{name}: added."
        except Exception as err:
            logger.warning("slack_add_reaction failed: %s", err)
            return f"Error: unable to add reaction ({err})."

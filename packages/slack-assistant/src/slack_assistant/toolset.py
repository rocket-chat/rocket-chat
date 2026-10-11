"""The Slack toolset: thread/channel reads, discovery and policy-gated outbound actions."""

import logging
from typing import Any

from agent_core.context import current_execution_ctx

from slack_assistant.state import SlackAssistantState
from slack_assistant.thread_context import (
    build_channel_transcript,
    build_thread_transcript,
)
from specifications.interfaces.llm import ToolDefinition

logger = logging.getLogger(__name__)


class SlackToolsetMixin(SlackAssistantState):
    """The Slack toolset: thread/channel reads, discovery and policy-gated outbound actions."""

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
                    "Fetch the full transcript of the current Slack thread, including "
                    "messages that did not mention you. Use it when earlier thread "
                    "context is needed to answer accurately."
                ),
                parameters_json_schema={
                    "type": "object",
                    "properties": {
                        "limit": {
                            "type": "integer",
                            "description": "Maximum number of messages to retrieve (default: 50).",
                        },
                    },
                },
            ),
            self._tool_read_thread,
            toolset="slack",
        )
        self.tool_registry.register_dynamic_tool(
            ToolDefinition(
                name="slack_read_channel_history",
                description=(
                    "Fetch recent messages from the current Slack channel, optionally "
                    "before a given timestamp. Use it to read preceding channel "
                    "messages outside the current thread."
                ),
                parameters_json_schema={
                    "type": "object",
                    "properties": {
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
            self._tool_read_channel_history,
            toolset="slack",
        )

        # Outbound and discovery tools, all part of the same session-scoped "slack"
        # toolset: a session that enables the toolset gets them for every turn (web
        # included), and they are policy-gated before any Slack API call.
        for name, description, schema, handler in self._slack_tool_specs():
            self.tool_registry.register_dynamic_tool(
                ToolDefinition(name=name, description=description, parameters_json_schema=schema),
                handler,
                toolset="slack",
            )

    def _slack_tool_specs(self) -> list[tuple[str, str, dict[str, Any], Any]]:
        """The outbound/discovery half of the Slack toolset (name, doc, schema, handler)."""
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
                "Resolve a Slack user's id from their name, real name or email.",
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
            )
        except Exception as err:
            logger.debug("Failed loading Slack thread context: %s", err)
            return ""

    async def _tool_read_thread(self, limit: int = 50) -> str:
        """Tool handler: fetch and render the current thread transcript."""
        context = self._current_slack_context()
        if not context:
            return "Error: no active Slack thread context is available."
        channel_id, thread_ts = context
        if not self._slack_api_ready:
            return "Error: Slack API client is not connected."
        try:
            transcript = await build_thread_transcript(
                self.app.client,
                channel_id,
                thread_ts,
                self._resolve_slack_user_name,
                limit=limit,
                max_chars=self.settings.slack_thread_context_max_chars,
                bot_user_id=self.bot_user_id,
            )
            return transcript or "The Slack thread is empty."
        except Exception as err:
            logger.warning("slack_read_thread failed: %s", err)
            return f"Error: unable to read Slack thread ({err})."

    async def _tool_read_channel_history(
        self, limit: int = 50, before_ts: str | None = None
    ) -> str:
        """Tool handler: fetch and render recent channel history."""
        context = self._current_slack_context()
        if not context:
            return "Error: no active Slack channel context is available."
        channel_id, _thread_ts = context
        if not self._slack_api_ready:
            return "Error: Slack API client is not connected."
        try:
            transcript = await build_channel_transcript(
                self.app.client,
                channel_id,
                self._resolve_slack_user_name,
                limit=limit,
                latest=before_ts,
                max_chars=self.settings.slack_thread_context_max_chars,
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
            raw = await self.app.client.users_list(limit=200)
            members: list[Any] = list(raw.get("members") or [])
            needle = query.strip().lower()
            for member in members:
                profile = member.get("profile") or {}
                email = str(profile.get("email", "")).lower()
                if needle in (
                    str(member.get("id", "")).lower(),
                    str(member.get("name", "")).lower(),
                    str(member.get("real_name", "")).lower(),
                    email,
                ):
                    # The e-mail may be used to find someone but is not disclosed.
                    return f"{member.get('id')} — {member.get('real_name') or member.get('name')}"
            return f"No Slack user matched '{query}'."
        except Exception as err:
            logger.warning("slack_resolve_user failed: %s", err)
            return f"Error: unable to resolve user ({err})."

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

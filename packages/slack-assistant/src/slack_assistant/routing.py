"""Which agent answers a message and how: the bot's router, and the private response mode."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from slack_assistant.artifacts import head_of
from slack_assistant.block_kit import build_private_blocks, markdown_to_slack_mrkdwn
from slack_assistant.bot import DISMISS, ClassifyRequest, SlackBot
from slack_assistant.policy import SlackOrgPolicy
from slack_assistant.state import SlackAssistantState
from slack_assistant.thread_context import fetch_thread_messages, render_transcript
from specifications.interfaces.session import SessionRecord

logger = logging.getLogger(__name__)

ROUTE_KEY = "slack_route"
PRIVATE_ANSWER_KEY = "slack_private_answer"
# What "Share in thread" may post: Slack refuses a section over 3,000 characters.
SHARED_ANSWER_CHARS = 2800


@dataclass(frozen=True)
class Routing:
    """The agent that answers (empty: the policy's default), how, and the route that chose it."""

    agent_id: str = ""
    response_mode: str = "thread"
    route: str = ""


class SlackRoutingMixin(SlackAssistantState):
    """Which agent answers a message and how: the bot's router, and the private response mode."""

    async def _route_request(
        self,
        *,
        org_id: str,
        text: str,
        channel_id: str,
        thread_ts: str,
        user_id: str | None,
        org_policy: SlackOrgPolicy,
    ) -> Routing | None:
        """The routing for a message, or ``None`` when the router dismissed it."""
        bot = self.bot
        if bot is None:
            return Routing()
        channel_mode = getattr(org_policy.profile_for(channel_id), "response_mode", "")
        default = Routing(bot.agent_id, channel_mode or bot.response_mode)
        router = bot.router
        if router is None:
            return default
        name = await self._sticky_route(channel_id, thread_ts, user_id) if router.sticky else ""
        if not name or router.route(name) is None:
            name = await self._classify(bot, org_id, text, channel_id, thread_ts, user_id)
        if name == DISMISS:
            return None
        route = router.route(name)
        if route is None:
            return default
        return Routing(route.agent_id, route.response_mode or default.response_mode, route.name)

    async def _classify(
        self,
        bot: SlackBot,
        org_id: str,
        text: str,
        channel_id: str,
        thread_ts: str,
        user_id: str | None,
    ) -> str:
        assert bot.router is not None
        decision = None
        if self._classify_fn is not None:
            request = ClassifyRequest(
                bot=bot,
                org_id=org_id,
                text=text,
                thread_excerpt=await self._thread_excerpt(
                    channel_id, thread_ts, bot.router.context_messages
                ),
                channel_id=channel_id,
                thread_ts=thread_ts,
                slack_user_id=user_id or "",
            )
            try:
                decision = await self._classify_fn(request)
            except Exception as err:
                logger.warning("Routing failed for bot %s, using the fallback: %s", bot.id, err)
        return bot.router.resolve(decision)

    async def _thread_excerpt(self, channel_id: str, thread_ts: str, count: int) -> str:
        """The thread's last ``count`` messages before this one, for the classifier."""
        if count <= 0 or not self._slack_api_ready:
            return ""
        try:
            messages = await fetch_thread_messages(
                self.app.client, channel_id, thread_ts, limit=200, bot_user_id=self.bot_user_id
            )
        except Exception as err:
            logger.debug("No thread excerpt for routing: %s", err)
            return ""
        # The newest message is the one being routed; the classifier gets it separately.
        return await render_transcript(
            messages[-count - 1 : -1], self._resolve_slack_user_name, max_chars=2000
        )

    async def _sticky_route(self, channel_id: str, thread_ts: str, user_id: str | None) -> str:
        """The route already chosen for this thread (the requester's private session first)."""
        if self.session_store is None:
            return ""
        candidates = [self.get_session_id(channel_id, thread_ts)]
        if user_id:
            candidates.insert(0, self.get_session_id(channel_id, thread_ts, private_for=user_id))
        for session_id in candidates:
            try:
                record = await self.session_store.get_session(session_id)
            except Exception as err:
                logger.debug("Sticky route lookup failed for %s: %s", session_id, err)
                continue
            metadata = record.metadata if record and isinstance(record.metadata, dict) else {}
            if metadata.get(ROUTE_KEY):
                return str(metadata[ROUTE_KEY])
        return ""

    # -- private response mode ---------------------------------------------------------------

    def _ephemeral_say(self, channel_id: str, user_id: str) -> Callable[..., Awaitable[Any]]:
        """A ``say`` that only the requester sees: questions, approvals and errors included."""
        client = self.app.client

        async def say(**kwargs: Any) -> Any:
            return await client.chat_postEphemeral(channel=channel_id, user=user_id, **kwargs)

        return say

    async def _acknowledge_privately(
        self,
        *,
        channel_id: str,
        message_ts: str | None,
        say: Callable[..., Awaitable[Any]],
        thread_ts: str,
        link_url: str | None,
        direct: bool = False,
    ) -> None:
        """Mark the request as taken with a reaction, and tell the requester where it runs."""
        bot = self.bot
        if bot is not None and bot.private_reaction and message_ts and self._slack_api_ready:
            try:
                await self.app.client.reactions_add(
                    channel=channel_id, timestamp=message_ts, name=bot.private_reaction
                )
            except Exception as err:
                logger.debug("Private-mode reaction skipped: %s", err)
        text = (
            ":lock: Working on it privately. I'll send my questions and the result to you directly."
            if direct
            else ":lock: Working on it privately. Only you see this."
        )
        try:
            await say(
                text=text, blocks=build_private_blocks(text, link_url=link_url), thread_ts=thread_ts
            )
        except Exception as err:
            logger.warning("Private-mode acknowledgement failed: %s", err)

    async def _post_private_result(
        self,
        *,
        session_id: str,
        org_id: str,
        answer: str,
        say: Callable[..., Awaitable[Any]],
        thread_ts: str,
        requester: str,
        link_url: str | None,
        sent_directly: bool = False,
    ) -> None:
        """The answer to its requester only, kept on the session for **Share in thread**."""
        text = answer.strip() or "Done."
        await self._keep_private_answer(session_id, org_id, text)
        summary = markdown_to_slack_mrkdwn(head_of(text, SHARED_ANSWER_CHARS))
        await say(
            text=summary,
            blocks=build_private_blocks(summary, link_url=link_url, share_value=session_id),
            thread_ts=thread_ts,
        )
        if (
            not sent_directly
            and self.bot is not None
            and self.bot.private_dm_result
            and self._slack_api_ready
        ):
            # The ephemeral reply disappears when Slack reloads; the direct message stays.
            try:
                await self.app.client.chat_postMessage(
                    channel=requester,
                    text=summary,
                    blocks=build_private_blocks(summary, link_url=link_url),
                )
            except Exception as err:
                logger.warning("Private-mode result DM failed: %s", err)

    async def _keep_private_answer(self, session_id: str, org_id: str, text: str) -> None:
        if self.session_store is None:
            return

        def keep(record: SessionRecord) -> None:
            known = record.metadata if isinstance(record.metadata, dict) else {}
            record.metadata = {**known, PRIVATE_ANSWER_KEY: head_of(text, SHARED_ANSWER_CHARS)}

        try:
            await self.session_store.mutate_session(session_id, org_id, keep)
        except Exception as err:
            logger.warning("Could not keep the private answer of %s: %s", session_id, err)

    async def _share_private_answer(self, body: dict[str, Any]) -> None:
        """Post a private answer publicly in its thread, when its requester asks."""
        actions = body.get("actions") or [{}]
        session_id = str(actions[0].get("value", ""))
        clicker = str((body.get("user") or {}).get("id", ""))
        team = str((body.get("team") or {}).get("id") or "")
        self._bind_tenant(await self._workspace_org(team or self.team_id or ""))
        record = await self.session_store.get_session(session_id) if self.session_store else None
        metadata = record.metadata if record and isinstance(record.metadata, dict) else {}
        answer = str(metadata.get(PRIVATE_ANSWER_KEY) or "")
        if not answer or metadata.get("slack_user_id") != clicker:
            return
        await self.app.client.chat_postMessage(
            channel=str(metadata.get("slack_channel_id", "")),
            thread_ts=str(metadata.get("slack_thread_ts", "")),
            text=f"<@{clicker}> shared an answer:\n{answer}"[:2950],
        )

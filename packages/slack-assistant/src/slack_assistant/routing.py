"""Which agent answers a message and how: the bot's router and its response modes."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from slack_assistant.bot import DISMISS, RESPOND, ClassifyRequest, FollowUpRequest, SlackBot
from slack_assistant.policy import SlackOrgPolicy
from slack_assistant.state import SlackAssistantState
from slack_assistant.thread_context import fetch_thread_messages, render_transcript

logger = logging.getLogger(__name__)

ROUTE_KEY = "slack_route"
# The thread messages the follow-up classifier sees before the reply: enough to hold the
# bot's last answer and what the others said since.
FOLLOW_UP_CONTEXT_MESSAGES = 8


@dataclass(frozen=True)
class Routing:
    """The agent that answers (empty: the policy's default), how, and the route that chose it."""

    agent_id: str = ""
    response_mode: str = "thread"
    route: str = ""


class SlackRoutingMixin(SlackAssistantState):
    """Which agent answers a message and how: the bot's router and its response modes."""

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

    async def _answers_follow_up(
        self,
        policy: SlackOrgPolicy,
        *,
        org_id: str,
        channel_id: str,
        thread_ts: str,
        user_id: str,
        text: str,
        channel_type: str | None,
    ) -> bool:
        """Whether a thread reply that does not mention the bot is for the bot to answer."""
        mode = policy.thread_follow_ups
        if mode == "auto":
            return self.should_respond_to_thread_message(text, channel_type, user_id)
        if mode != "classifier" or self.bot is None or self._follow_up_fn is None:
            return False
        request = FollowUpRequest(
            bot=self.bot,
            org_id=org_id,
            text=text,
            thread_excerpt=await self._thread_excerpt(
                channel_id, thread_ts, FOLLOW_UP_CONTEXT_MESSAGES
            ),
            channel_id=channel_id,
            thread_ts=thread_ts,
            slack_user_id=user_id,
            model=policy.follow_up_classifier_model,
        )
        try:
            decision = await self._follow_up_fn(request)
        except Exception as err:
            logger.warning("Follow-up classifier failed for %s: %s", channel_id, err)
            return False
        # Undecided is left to the people of the thread: an unwanted turn costs more than a
        # missed one, which a mention brings back.
        return decision is not None and decision.route == RESPOND

    def should_respond_to_thread_message(
        self, text: str, channel_type: str | None, user_id: str | None
    ) -> bool:
        raise NotImplementedError

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

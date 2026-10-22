"""Who may talk to the bot and how much: channel rules, rate limits, quotas, policy."""

import inspect
import logging
import re
from collections.abc import Callable
from typing import Any

from agent_core.tool_loading import ESSENTIAL_TOOLS

from slack_assistant.guardrails import (
    DEFAULT_BLOCKED_TOOLS,
    QuotaDecision,
    parse_tool_csv,
)
from slack_assistant.policy import SlackOrgPolicy
from slack_assistant.state import SlackAssistantState

logger = logging.getLogger(__name__)


class SlackAdmissionMixin(SlackAssistantState):
    """Who may talk to the bot and how much: channel rules, rate limits, quotas, policy."""

    def should_respond_to_thread_message(
        self,
        text: str,
        channel_type: str | None,
        user_id: str | None,
    ) -> bool:
        """Determines if an incoming message in a thread warrants a bot response."""
        # 1. Direct Messages (IM) always get a response
        if channel_type == "im":
            return True

        # 2. If the bot is explicitly mentioned, always respond
        if self.bot_user_id and f"<@{self.bot_user_id}>" in text:
            return True

        # 3. If the message starts with an @mention of someone else who is NOT the bot, ignore it
        mention_match = re.search(r"^<@([A-Z0-9]+)>", text.strip())
        if mention_match:
            target_id = mention_match.group(1)
            if self.bot_user_id and target_id != self.bot_user_id:
                return False

        # 4. Skip common conversational pleasantries / acknowledgments between users
        clean = text.strip().lower()
        pleasantries = {
            "thanks",
            "thank you",
            "thx",
            "ty",
            "ok",
            "okay",
            "k",
            "cool",
            "great",
            "nice",
            "awesome",
            "perfect",
            "got it",
            "understood",
            "lgtm",
            "looks good",
            "sounds good",
            "done",
            "+1",
            "agreed",
            "np",
            "no problem",
            "you're welcome",
            "welcome",
        }
        if clean in pleasantries or clean.rstrip("!.") in pleasantries:
            return False

        # 5. In public threads, if bot is not mentioned, only respond if message appears to be an instruction
        action_keywords = (
            "can you",
            "could you",
            "please",
            "help",
            "fix",
            "run",
            "why",
            "what",
            "how",
            "show",
            "check",
            "do ",
            "build",
            "deploy",
            "debug",
        )
        if any(kw in clean for kw in action_keywords):
            return True

        return False

    def _slack_tool_selection(
        self, org_allowed: tuple[str, ...] = ()
    ) -> tuple[frozenset[str] | None, frozenset[str]]:
        """(allowed tool names or None for every tool, blocked tool names) for Slack turns."""
        allowed_raw = ",".join(org_allowed) if org_allowed else self.settings.slack_allowed_tools
        allowed = parse_tool_csv(allowed_raw) or frozenset({"*"})
        blocked = parse_tool_csv(self.settings.slack_blocked_tools) or frozenset(
            DEFAULT_BLOCKED_TOOLS
        )
        # A blocklist cannot take away what a platform gate sends the agent to.
        return (None if "*" in allowed else allowed), blocked - ESSENTIAL_TOOLS

    def _slack_whitelisted_tools(
        self,
        base: list[str] | None = None,
        org_allowed: tuple[str, ...] = (),
        blocked: frozenset[str] | None = None,
    ) -> list[str] | None:
        """The tool allowlist for a Slack turn, ``None`` meaning every tool.

        ``SLACK_ALLOWED_TOOLS`` (or the organization's list) narrowed to the bound agent's
        whitelist when it declares one. A wildcard on both sides stays unrestricted rather than
        becoming a list of the tools registered now: MCP tools only exist once the turn starts,
        and a list would hide them. The blocked tools are enforced apart, as the overlay's
        ``denied_tools``; ``blocked`` is that turn's set (default: the deployment's list), so an
        agent's own allowlist never takes back what the turn was given.
        """
        allowed, default_blocked = self._slack_tool_selection(org_allowed)
        blocked = default_blocked if blocked is None else blocked
        agent_scope = None if base is None or "*" in base else frozenset(base)
        if allowed is None and agent_scope is None:
            return None
        if allowed is None:
            selected = agent_scope or frozenset()
        elif agent_scope is None:
            selected = allowed
        else:
            selected = allowed & agent_scope
        return sorted(selected - blocked)

    def _rate_limit_decision(
        self, team_id: str, user_id: str | None, channel_id: str, now: float
    ) -> QuotaDecision | None:
        """Token-bucket check per ``(team,user)`` then ``(team,channel)``; consumes on pass."""
        if user_id and self._user_rate_limiter is not None:
            verdict = self._user_rate_limiter.evaluate((f"user:{team_id}:{user_id}",), now)
            if not verdict.allowed:
                self._rate_counters["rate_limit_user_denied"] += 1
                return QuotaDecision(
                    False,
                    "rate_limit_user",
                    "🚦 *Rate limit reached.* You have sent requests faster than this "
                    f"workspace allows; please try again in about "
                    f"{verdict.retry_after_seconds} seconds.",
                    verdict.retry_after_seconds,
                )
        if self._channel_rate_limiter is not None:
            verdict = self._channel_rate_limiter.evaluate((f"channel:{team_id}:{channel_id}",), now)
            if not verdict.allowed:
                self._rate_counters["rate_limit_channel_denied"] += 1
                return QuotaDecision(
                    False,
                    "rate_limit_channel",
                    "🚦 *Channel rate limit reached.* This Slack channel is sending "
                    "requests faster than the workspace allows; please try again in about "
                    f"{verdict.retry_after_seconds} seconds.",
                    verdict.retry_after_seconds,
                )
        self._rate_counters["rate_limit_admitted"] += 1
        return None

    async def _pre_dispatch_guard(
        self,
        *,
        team_id: str,
        session_id: str,
        org_id: str,
        user_id: str | None,
        channel_id: str,
    ) -> QuotaDecision | None:
        """Everything that must be checked *before* an orchestrator turn is admitted.

        Returns ``None`` when the request may proceed, otherwise a denial carrying the
        message to surface to the user. Order: consecutive-failure breaker, token-bucket
        rate limits, then the per-workspace quota gate — cheapest and most-local first.
        """
        now = self._clock()
        scope_key = team_id or session_id
        if self._failures.is_blocked(scope_key, now):
            wait = self._failures.retry_after_seconds(scope_key, now)
            return QuotaDecision(
                False,
                "failure_breaker",
                "⚠️ *Temporarily paused.* This workspace hit repeated processing errors; "
                f"please retry in about {wait} seconds.",
                wait,
            )
        rate_denial = self._rate_limit_decision(team_id, user_id, channel_id, now)
        if rate_denial is not None:
            return rate_denial
        if self.quota_gate is not None:
            decision = await self.quota_gate.evaluate(
                team_id=team_id,
                org_id=org_id,
                user_id=user_id,
                channel_id=channel_id,
                active_sessions=len(self._active_turns),
                now=now,
            )
            # The gate returns a decision object whether it allows or denies — it does NOT
            # return None on allow. Returning it unconditionally would treat every allowed
            # turn as a denial and refuse it with the generic policy message.
            return None if decision.allowed else decision
        return None

    @staticmethod
    def _denial_text(denial: QuotaDecision) -> str:
        """The message to surface for a denial, always naming the reason."""
        if denial.message:
            return denial.message
        reason = denial.reason or "unspecified"
        return (
            f"Your request was not permitted by the workspace policy ({reason}). "
            "A workspace administrator can raise the Slack quota or rate limits in the "
            "settings; use the web cockpit where the full toolset is available meanwhile."
        )

    async def _send_ephemeral(
        self,
        *,
        text: str,
        channel_id: str,
        user_id: str | None,
        thread_ts: str,
        is_dm: bool,
        ephemeral_fn: Callable[..., Any] | None,
        fallback_say: Callable[..., Any],
    ) -> None:
        """Deliver a denial notice to the caller, ephemerally where Slack supports it.

        Ephemeral messages are not supported in 1:1 DMs, so those (and any failure to post
        ephemerally) fall back to the normal thread reply rather than dropping the notice
        — a denial that is never delivered is exactly the invisible failure invariant 10
        forbids.
        """
        if is_dm or not user_id:
            await fallback_say(text=text, thread_ts=thread_ts)
            return
        if ephemeral_fn is not None:
            outcome = ephemeral_fn(text=text, thread_ts=thread_ts, user=user_id)
            if inspect.isawaitable(outcome):
                await outcome
            return
        client = getattr(self.app, "client", None)
        poster = getattr(client, "chat_postEphemeral", None)
        if callable(poster):
            try:
                await poster(channel=channel_id, user=user_id, text=text, thread_ts=thread_ts)
                return
            except Exception as err:
                logger.warning("Ephemeral Slack reply failed, falling back to thread: %s", err)
        await fallback_say(text=text, thread_ts=thread_ts)

    async def _load_org_policy(self, org_id: str) -> SlackOrgPolicy:
        """The organization's Slack policy; permissive defaults when unavailable."""
        policy = SlackOrgPolicy()
        if self._resolve_slack_policy_fn is not None:
            try:
                policy = await self._resolve_slack_policy_fn(org_id)
            except Exception as err:
                logger.warning("Slack policy resolution failed for %s: %s", org_id, err)
        bot = getattr(self, "bot", None)
        return bot.apply_to(policy) if bot is not None else policy

    def quota_counters(self) -> dict[str, int]:
        """Rate-limit and quota counters for ``GET /v1/slack/status``."""
        counters = dict(self._rate_counters)
        counters["rate_limit_active_buckets"] = (
            self._user_rate_limiter.active_buckets if self._user_rate_limiter else 0
        ) + (self._channel_rate_limiter.active_buckets if self._channel_rate_limiter else 0)
        if self.quota_gate is not None:
            counters.update(self.quota_gate.counters())
        counters.update(self._health.as_counters(self._clock()))
        return counters

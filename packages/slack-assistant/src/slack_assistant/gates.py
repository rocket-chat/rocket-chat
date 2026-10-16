"""Interactive approvals and questions answered from Slack buttons."""

import asyncio
import logging
from typing import Any

from slack_bolt.context.say.async_say import AsyncSay

from slack_assistant.state import SlackAssistantState

logger = logging.getLogger(__name__)


class SlackGatesMixin(SlackAssistantState):
    """Interactive approvals and questions answered from Slack buttons."""

    def _gate_context(self, body: dict[str, Any]) -> tuple[str, str, str, str]:
        """(session_id, value, thread_ts, clicking Slack user) of an interactive-component click."""
        actions = body.get("actions", [])
        value = str(actions[0].get("value", "")) if actions else ""
        channel_id = str(body.get("channel", {}).get("id", ""))
        container = body.get("container", {})
        thread_ts = str(container.get("thread_ts", ""))
        clicker = str(body.get("user", {}).get("id", ""))
        # A private request's gates are ephemeral and only its requester sees them, so the
        # clicker names the private session.
        private_for = clicker if container.get("is_ephemeral") else None
        known = self._gate_owners.get(value)
        session_id = (
            known[0]
            if known is not None
            else self.get_session_id(channel_id, thread_ts, private_for=private_for)
            if thread_ts
            else ""
        )
        return session_id, value, thread_ts, clicker

    async def _durable_requester(self, session_id: str) -> str | None:
        """Who started the session's latest turn, from the stored session (any replica, any time)."""
        if self.session_store is None:
            return None
        try:
            record = await self.session_store.get_session(session_id)
        except Exception as err:
            logger.warning("Could not look up the requester of %s: %s", session_id, err)
            return None
        metadata = (
            record.metadata if record is not None and isinstance(record.metadata, dict) else {}
        )
        requester = metadata.get("slack_turn_requester")
        return str(requester) if requester else None

    async def _may_answer(self, gate_id: str, session_id: str, clicker: str) -> bool:
        """Only the person who started the turn may answer its questions and approvals.

        In a shared channel anyone can click a button, so the click is checked against the
        requester. The in-memory record is the fast path; after a restart, or on another
        replica, the requester stored on the session decides. The orchestrator still refuses
        a gate that is not actually pending.
        """
        owner = self._gate_owners.get(gate_id)
        if owner is not None:
            return owner[0] == session_id and owner[1] == clicker
        requester = await self._durable_requester(session_id)
        return requester is not None and requester == clicker

    async def _bind_click_tenant(self, body: dict[str, Any]) -> None:
        """Scope database access to the clicking workspace's organization (no event did it)."""
        team = str(
            (body.get("team") or {}).get("id") or (body.get("user") or {}).get("team_id") or ""
        )
        self._bind_tenant(await self._workspace_org(team or self.team_id or ""))

    async def _resolve_approval(
        self, body: dict[str, Any], say: AsyncSay, *, approved: bool
    ) -> None:
        await self._bind_click_tenant(body)
        session_id, action_id, thread_ts, clicker = self._gate_context(body)
        if not (
            session_id and action_id and await self._may_answer(action_id, session_id, clicker)
        ):
            await say(
                text=f"<@{clicker}> only the person who asked can answer this approval.",
                thread_ts=thread_ts,
            )
            return
        try:
            await self.orchestrator.submit_human_approval(
                session_id=session_id, action_id=action_id, approved=approved
            )
        except KeyError:
            await say(
                text="This approval has already been answered or expired.", thread_ts=thread_ts
            )
            return
        self._gate_owners.pop(action_id, None)
        verdict = "✅ *Approved*" if approved else "❌ *Rejected*"
        await say(text=f"{verdict} by <@{clicker}> (action `{action_id}`)", thread_ts=thread_ts)

    async def _resolve_question(self, body: dict[str, Any], say: AsyncSay) -> None:
        await self._bind_click_tenant(body)
        session_id, value, thread_ts, clicker = self._gate_context(body)
        question_id, _, option = value.partition(":")
        if not (session_id and option and await self._may_answer(question_id, session_id, clicker)):
            await say(
                text=f"<@{clicker}> only the person who asked can answer this question.",
                thread_ts=thread_ts,
            )
            return
        try:
            await self.orchestrator.submit_question_answer(
                session_id=session_id, question_id=question_id, selected_options=[option]
            )
        except KeyError:
            if await self._continue_after_restart(body, session_id, question_id, option, say):
                self._gate_owners.pop(question_id, None)
                await say(
                    text=f"Selected: *{option}* - picking the work back up.", thread_ts=thread_ts
                )
                return
            await say(
                text="This question has already been answered or expired.", thread_ts=thread_ts
            )
            return
        self._gate_owners.pop(question_id, None)
        await say(text=f"Selected: *{option}*", thread_ts=thread_ts)

    async def _continue_after_restart(
        self,
        body: dict[str, Any],
        session_id: str,
        question_id: str,
        option: str,
        say: AsyncSay,
    ) -> bool:
        """Answer a question whose turn died with the server by continuing the work with it."""
        if self._answer_stored_question_fn is None:
            return False
        try:
            prompt = await self._answer_stored_question_fn(session_id, question_id, [option], None)
        except Exception as err:
            logger.warning("Continuing from stored question %s failed: %s", question_id, err)
            return False
        if not prompt:
            return False
        channel_id = str((body.get("channel") or {}).get("id", ""))
        _, _, thread_ts, clicker = self._gate_context(body)
        task = asyncio.create_task(
            self.process_incoming_request(
                channel_id=channel_id,
                thread_ts=thread_ts,
                raw_text=prompt,
                say_fn=say,
                user_id=clicker,
                channel_type="im" if channel_id.startswith("D") else "channel",
                team_id=str((body.get("team") or {}).get("id", "")) or None,
            )
        )
        self._background.add(task)
        task.add_done_callback(self._background.discard)
        return True

    async def _stop_turn(self, body: dict[str, Any], say: AsyncSay) -> None:
        """The Stop button of the live progress message: only the requester may end the turn."""
        await self._bind_click_tenant(body)
        actions = body.get("actions", [])
        session_id = str(actions[0].get("value", "")) if actions else ""
        clicker = str(body.get("user", {}).get("id", ""))
        thread_ts = str(
            body.get("container", {}).get("thread_ts")
            or body.get("message", {}).get("thread_ts", "")
        )
        requester = await self._durable_requester(session_id) if session_id else None
        if not session_id or requester is None or requester != clicker:
            await say(
                text=f"<@{clicker}> only the person who asked can stop this.", thread_ts=thread_ts
            )
            return
        await self.orchestrator.cancel_turn(session_id)
        await say(text=f":stop_button: Stopped by <@{clicker}>.", thread_ts=thread_ts)

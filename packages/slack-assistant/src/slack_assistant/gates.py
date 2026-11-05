"""Interactive approvals and questions answered from Slack buttons."""

import asyncio
import json
import logging
from typing import Any

from slack_bolt.context.respond.async_respond import AsyncRespond
from slack_bolt.context.say.async_say import AsyncSay

from slack_assistant.block_kit import (
    QUESTION_CHECKBOXES_ACTION_ID,
    SESSION_BLOCK_PREFIX,
    build_decision_blocks,
    build_other_answer_modal,
    session_of_block,
)
from slack_assistant.shortcuts import is_stop_request
from slack_assistant.state import SlackAssistantState
from specifications.interfaces.events import EventType, SessionEvent

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
        # Buttons sent by direct message carry their session (no thread names it there). Who
        # may answer is still checked against the session's requester.
        block_id = str(actions[0].get("block_id", "")) if actions else ""
        session_id = (
            known[0]
            if known is not None
            else session_of_block(block_id)
            if block_id.startswith(SESSION_BLOCK_PREFIX)
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
        self, body: dict[str, Any], respond: AsyncRespond, *, approved: bool
    ) -> None:
        await self._bind_click_tenant(body)
        session_id, action_id, _, clicker = self._gate_context(body)
        if not (
            session_id and action_id and await self._may_answer(action_id, session_id, clicker)
        ):
            await _tell(respond, "Only the person who asked can answer this approval.")
            return
        try:
            await self.orchestrator.submit_human_approval(
                session_id=session_id, action_id=action_id, approved=approved
            )
        except KeyError:
            await _decided(respond, "This approval has already been answered or expired.")
            return
        self._gate_owners.pop(action_id, None)
        verdict = "✅ *Approved*" if approved else "❌ *Rejected*"
        await _decided(respond, f"{verdict} by <@{clicker}>: {_gate_title(body)}")

    async def _resolve_question(
        self, body: dict[str, Any], respond: AsyncRespond, say: AsyncSay
    ) -> None:
        session_id, value, _, _ = self._gate_context(body)
        question_id, _, option = value.partition(":")
        await self._answer_from_click(
            body, respond, say, session_id, question_id, [option] if option else []
        )

    async def _submit_checked_options(
        self, body: dict[str, Any], respond: AsyncRespond, say: AsyncSay
    ) -> None:
        session_id, question_id, _, _ = self._gate_context(body)
        await self._answer_from_click(
            body, respond, say, session_id, question_id, _checked_options(body)
        )

    async def _answer_from_click(
        self,
        body: dict[str, Any],
        respond: AsyncRespond,
        say: AsyncSay,
        session_id: str,
        question_id: str,
        options: list[str],
    ) -> None:
        await self._bind_click_tenant(body)
        clicker = str((body.get("user") or {}).get("id", ""))
        if not options:
            await _tell(respond, "Pick at least one option, or answer with *Other…*.")
            return
        if not (session_id and await self._may_answer(question_id, session_id, clicker)):
            await _tell(respond, "Only the person who asked can answer this question.")
            return
        chosen = ", ".join(f"*{option}*" for option in options)
        if await self._answer_question(session_id, question_id, options, None, body):
            await _decided(respond, f"Selected {chosen} (<@{clicker}>)")
        elif await self._continue_after_restart(body, session_id, question_id, options, say):
            await _decided(respond, f"Selected {chosen} — picking the work back up.")
        else:
            await _decided(respond, "This question has already been answered or expired.")

    async def _answer_question(
        self,
        session_id: str,
        question_id: str,
        options: list[str],
        custom_text: str | None,
        body: dict[str, Any],
    ) -> bool:
        """Give the running turn its answer; False when no turn waits on the question."""
        try:
            await self.orchestrator.submit_question_answer(
                session_id=session_id,
                question_id=question_id,
                selected_options=options,
                custom_text=custom_text,
            )
        except KeyError:
            return False
        self._gate_owners.pop(question_id, None)
        if self.event_bus is not None:
            await self.event_bus.publish(
                SessionEvent(
                    session_id=session_id,
                    event_type=EventType.DECISION_SUBMITTED,
                    payload={
                        "question_id": question_id,
                        "selected_options": options,
                        "custom_text": custom_text,
                    },
                )
            )
        if self._clear_stored_question_fn is not None:
            org_id = await self._workspace_org(_team_of(body) or self.team_id or "")
            await self._clear_stored_question_fn(session_id, org_id or "default_org")
        return True

    async def _open_other_answer(self, body: dict[str, Any]) -> None:
        """**Other…**: a form for a free answer, which the requester alone may send."""
        session_id, question_id, thread_ts, _ = self._gate_context(body)
        message = body.get("message") or {}
        question_text = next(
            (
                str((block.get("text") or {}).get("text") or "").strip("❓ *")
                for block in message.get("blocks") or []
                if block.get("type") == "section"
            ),
            "Your answer",
        )
        metadata = json.dumps(
            {
                "session_id": session_id,
                "question_id": question_id,
                "channel_id": str((body.get("channel") or {}).get("id", "")),
                "thread_ts": thread_ts,
                "message_ts": str(message.get("ts") or ""),
                "team_id": _team_of(body),
            }
        )
        try:
            await self.app.client.views_open(
                trigger_id=str(body.get("trigger_id", "")),
                view=build_other_answer_modal(question_text, metadata),
            )
        except Exception as err:
            logger.warning("Could not open the answer form: %s", err)

    async def _submit_other_answer(self, body: dict[str, Any], view: dict[str, Any]) -> None:
        try:
            meta = json.loads(str(view.get("private_metadata") or "{}"))
        except ValueError:
            return
        answer = str(
            (((view.get("state") or {}).get("values") or {}).get("answer") or {})
            .get("text", {})
            .get("value")
            or ""
        ).strip()
        clicker = str((body.get("user") or {}).get("id", ""))
        session_id, question_id = str(meta.get("session_id", "")), str(meta.get("question_id", ""))
        self._bind_tenant(await self._workspace_org(str(meta.get("team_id") or self.team_id or "")))
        if not (answer and session_id and await self._may_answer(question_id, session_id, clicker)):
            return
        if not await self._answer_question(
            session_id, question_id, [], answer, {"team": {"id": meta.get("team_id")}}
        ):
            return
        channel, message_ts = str(meta.get("channel_id", "")), str(meta.get("message_ts", ""))
        if channel and message_ts:
            try:
                await self.app.client.chat_update(
                    channel=channel,
                    ts=message_ts,
                    text="Answered",
                    blocks=build_decision_blocks(f"Answered by <@{clicker}>: {answer[:500]}"),
                )
            except Exception as err:
                logger.debug("Question message %s not updated: %s", message_ts, err)

    async def _continue_after_restart(
        self,
        body: dict[str, Any],
        session_id: str,
        question_id: str,
        options: list[str],
        say: AsyncSay,
    ) -> bool:
        """Answer a question whose turn died with the server by continuing the work with it."""
        if self._answer_stored_question_fn is None:
            return False
        try:
            prompt = await self._answer_stored_question_fn(session_id, question_id, options, None)
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

    async def _stop_agent_session(self, event: dict[str, Any], body: dict[str, Any]) -> None:
        """Slack's stop button on a working thread (``agent_session_stopped``).

        Only the person who asked stops the turn; the turn then ends saying who stopped it.
        Slack leaves the session "processing" until the app moves it, so with nothing to stop
        (or a stranger's click) the session is set back to what it really is. In a private DM
        thread the button stops the private session that thread runs.
        """
        channel, thread_ts = str(event.get("channel", "")), str(event.get("thread_ts", ""))
        clicker = str(event.get("user", ""))
        team = str(body.get("team_id") or self.team_id or "")
        private = await self._private_thread_at(channel, thread_ts, team)
        self._bind_tenant(await self._workspace_org(team))
        running = False
        for session_id in (
            *((private.session_id,) if private is not None else ()),
            self.get_session_id(channel, thread_ts, private_for=clicker),
            self.get_session_id(channel, thread_ts),
        ):
            if session_id not in self._active_turns:
                continue
            running = True
            if await self._durable_requester(session_id) == clicker:
                await self._stop_session(session_id, clicker)
                return
        await self._set_session_status(channel, thread_ts, "processing" if running else "active")

    async def _stop_from_message(self, event: dict[str, Any], *, addressed: bool) -> bool:
        """A message that only says stop (``stop``, ``arrête``...): whether it was taken as one.

        It stops its sender's turn in the thread, as Slack's stop button does, and never becomes
        a request, which would be queued behind the very turn it means to stop. Unaddressed (no
        mention, outside a DM) it counts only when its sender has a turn to stop: otherwise the
        word is for the people of the thread.
        """
        user = str(event.get("user") or "")
        channel = str(event.get("channel", ""))
        thread_ts = str(event.get("thread_ts") or event.get("ts") or "")
        if not (user and channel and thread_ts and is_stop_request(str(event.get("text", "")))):
            return False
        self._bind_tenant(await self._workspace_org(str(event.get("team") or self.team_id or "")))
        for session_id in self._stoppable_sessions(event, channel, thread_ts, user):
            if (
                session_id in self._active_turns
                and await self._durable_requester(session_id) == user
            ):
                await self._stop_session(session_id, user)
                return True
        if addressed:
            await self._tell_in_thread(
                channel, user, thread_ts, "Nothing of yours is running here."
            )
        return addressed

    def _stoppable_sessions(
        self, event: dict[str, Any], channel: str, thread_ts: str, user: str
    ) -> list[str]:
        """The sessions a stop in this thread may mean: the sender's private one, the shared
        one, and for a top-level direct message every session of that conversation (it
        continues an earlier thread)."""
        found = [
            self.get_session_id(channel, thread_ts, private_for=user),
            self.get_session_id(channel, thread_ts),
        ]
        if event.get("channel_type") == "im" and not event.get("thread_ts"):
            found += [
                s for s in self._active_turns if self._session_meta.get(s, ("",))[0] == channel
            ]
        return found

    async def _stop_session(self, session_id: str, stopped_by: str) -> None:
        """Stop the session's turn and what would start the work again: the messages queued
        behind it and its open waits. Those go first, as the ending turn starts whatever is
        still queued."""
        if self._stop_session_fn is not None:
            try:
                await self._stop_session_fn(session_id)
            except Exception as err:
                logger.warning("Could not clear what follows the turn of %s: %s", session_id, err)
        self._stopped_by[session_id] = stopped_by
        await self.orchestrator.cancel_turn(session_id)

    async def _tell_in_thread(self, channel: str, user: str, thread_ts: str, text: str) -> None:
        """A note only ``user`` sees, under the thread."""
        if not self._slack_api_ready:
            return
        try:
            await self.app.client.chat_postEphemeral(
                channel=channel, user=user, text=text, thread_ts=thread_ts
            )
        except Exception as err:
            logger.debug("Ephemeral note not sent in %s: %s", channel, err)

    async def _set_session_status(self, channel_id: str, thread_ts: str, status: str) -> None:
        """Slack's agent session state of a thread: "processing" shows its working indicator
        and stop button, "active" clears them."""
        if not (self._slack_api_ready and channel_id and thread_ts):
            return
        try:
            await self.app.client.agents_sessions_setStatus(
                channel_id=channel_id, thread_ts=thread_ts, status=status
            )
        except Exception as err:
            logger.debug("Agent session status not set (%s): %s", status, err)


async def _tell(respond: AsyncRespond, text: str) -> None:
    """A note to the clicker alone; the gate stays as it is."""
    await respond(text=text, response_type="ephemeral", replace_original=False)


async def _decided(respond: AsyncRespond, text: str) -> None:
    """The gate message becomes its decision, without buttons (no second click can land)."""
    await respond(text=text, blocks=build_decision_blocks(text), replace_original=True)


def _gate_title(body: dict[str, Any]) -> str:
    """The gate's first line ("Approval Required — git_push (high risk)"), for its decision."""
    for block in (body.get("message") or {}).get("blocks") or []:
        if block.get("type") == "section":
            text = str((block.get("text") or {}).get("text") or "")
            return text.split("\n", 1)[0].strip("⚠️ *")[:200]
    return "the requested action"


def _checked_options(body: dict[str, Any]) -> list[str]:
    """The options ticked in the message's checkboxes when **Submit** was clicked."""
    values = ((body.get("state") or {}).get("values") or {}).values()
    for block in values:
        for action_id, element in (block or {}).items():
            if action_id == QUESTION_CHECKBOXES_ACTION_ID:
                return [str(o.get("value")) for o in element.get("selected_options") or []]
    return []


def _team_of(body: dict[str, Any]) -> str:
    return str(
        (body.get("team") or {}).get("id")
        or (body.get("user") or {}).get("team_id")
        or body.get("team_id")
        or ""
    )

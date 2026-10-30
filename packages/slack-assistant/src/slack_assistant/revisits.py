"""Requests revisited: Retry, a suggested follow-up, an edited or deleted request, and a direct
message that continues the last conversation.

Each one ends in ``process_incoming_request`` like any message, so admission, routing, quotas
and the turn's access checks apply unchanged; only who may do it is decided here.
"""

from __future__ import annotations

import logging
from collections import OrderedDict
from typing import Any

from slack_assistant.state import SlackAssistantState
from specifications.interfaces.session import SessionRecord

logger = logging.getLogger(__name__)

# The latest request of a session (its text and Slack message), for Retry and for edits and
# deletions of it.
LAST_REQUEST_KEY = "slack_last_request"
LAST_REQUEST_CHARS = 4000
# Direct-message conversations remembered for continuation (channel and person -> thread).
MAX_DM_THREADS = 5000


class SlackRevisitsMixin(SlackAssistantState):
    """Retry, follow-ups, edited and deleted requests, continued direct messages."""

    _dm_threads: OrderedDict[tuple[str, str], tuple[str, float]]

    # Provided by the entry points.
    async def _run_in_thread(
        self,
        *,
        channel_id: str,
        thread_ts: str,
        prompt: str,
        user_id: str,
        team_id: str | None,
        message_ts: str | None = None,
    ) -> None:
        raise NotImplementedError

    async def _durable_requester(self, session_id: str) -> str | None:
        raise NotImplementedError

    # -- Retry and suggested follow-ups -----------------------------------------------------

    async def _retry_request(self, body: dict[str, Any]) -> None:
        """Run a failed turn's request again; only its requester may."""
        session_id = str(((body.get("actions") or [{}])[0]).get("value") or "")
        clicker = str((body.get("user") or {}).get("id", ""))
        self._bind_tenant(await self._workspace_org(_team_of(body) or self.team_id or ""))
        record = await self._session_record(session_id)
        metadata = _metadata(record)
        request = metadata.get(LAST_REQUEST_KEY)
        if not (
            isinstance(request, dict)
            and request.get("text")
            and metadata.get("slack_turn_requester") == clicker
        ):
            return
        await self._run_in_thread(
            channel_id=str(metadata.get("slack_channel_id") or ""),
            thread_ts=str(metadata.get("slack_thread_ts") or ""),
            prompt=str(request["text"]),
            user_id=clicker,
            team_id=_team_of(body) or None,
        )

    async def _run_followup(self, body: dict[str, Any]) -> None:
        """Send a suggested next step as the clicker's own message in the thread."""
        suggestion = str(((body.get("actions") or [{}])[0]).get("value") or "").strip()
        channel_id = str((body.get("channel") or {}).get("id", ""))
        container = body.get("container") or {}
        thread_ts = str(container.get("thread_ts") or container.get("message_ts") or "")
        if not (suggestion and channel_id and thread_ts):
            return
        await self._run_in_thread(
            channel_id=channel_id,
            thread_ts=thread_ts,
            prompt=suggestion,
            user_id=str((body.get("user") or {}).get("id", "")),
            team_id=_team_of(body) or None,
        )

    # -- edited and deleted requests --------------------------------------------------------

    async def _on_message_changed(self, event: dict[str, Any]) -> None:
        """An edited request runs again with its new text (a running turn is stopped first)."""
        message = event.get("message") or {}
        if message.get("bot_id") or not message.get("user"):
            return
        ts = str(message.get("ts") or "")
        found = await self._session_of_request(event, message, ts)
        if found is None:
            return
        session_id, channel_id, thread_ts = found
        user_id = str(message["user"])
        if session_id in self._active_turns:
            self._stop_notices[session_id] = "⏹️ Stopped: the request was edited; running it again."
            await self.orchestrator.cancel_turn(session_id)
        await self.process_incoming_request(
            channel_id=channel_id,
            thread_ts=thread_ts,
            raw_text=str(message.get("text") or ""),
            say_fn=self._thread_say(channel_id),
            user_id=user_id,
            channel_type=event.get("channel_type"),
            team_id=str(event.get("team") or message.get("team") or "") or None,
            message_ts=ts,
        )

    async def _on_message_deleted(self, event: dict[str, Any]) -> None:
        """Deleting the request of a running turn stops that turn."""
        previous = event.get("previous_message") or {}
        ts = str(event.get("deleted_ts") or previous.get("ts") or "")
        found = await self._session_of_request(event, previous, ts)
        if found is None or found[0] not in self._active_turns:
            return
        session_id = found[0]
        self._stop_notices[session_id] = "⏹️ Stopped: the request was deleted."
        await self.orchestrator.cancel_turn(session_id)

    async def _session_of_request(
        self, event: dict[str, Any], message: dict[str, Any], ts: str
    ) -> tuple[str, str, str] | None:
        """(session, channel, thread) whose latest request is the message ``ts``, when the
        person who wrote it is the one who started that session's latest turn."""
        channel_id = str(event.get("channel") or "")
        user_id = str(message.get("user") or "")
        thread_ts = str(message.get("thread_ts") or ts)
        if not (channel_id and user_id and ts and self.session_store is not None):
            return None
        self._bind_tenant(await self._workspace_org(str(event.get("team") or self.team_id or "")))
        for private_for in (user_id, None):
            session_id = self.get_session_id(channel_id, thread_ts, private_for=private_for)
            metadata = _metadata(await self._session_record(session_id))
            request = metadata.get(LAST_REQUEST_KEY)
            if (
                isinstance(request, dict)
                and request.get("ts") == ts
                and metadata.get("slack_turn_requester") == user_id
            ):
                return session_id, channel_id, thread_ts
        return None

    # -- direct messages that continue the last conversation --------------------------------

    def _continued_dm_thread(
        self, channel_id: str, user_id: str, ts: str, window_minutes: int
    ) -> str | None:
        """The thread whose session a top-level direct message continues, or None for a new
        one. Remembered in memory on the bot's leader, which receives every message: after a
        restart the next message starts a new session, which is what the setting's 0 does."""
        key = (channel_id, user_id)
        now = self._clock()
        known = self._dm_threads.pop(key, None)
        continued = (
            known[0]
            if known is not None and window_minutes and now - known[1] < window_minutes * 60
            else None
        )
        self._dm_threads[key] = (continued or ts, now)
        while len(self._dm_threads) > MAX_DM_THREADS:
            self._dm_threads.popitem(last=False)
        return continued

    # -- shared ------------------------------------------------------------------------------

    async def _session_record(self, session_id: str) -> SessionRecord | None:
        if self.session_store is None or not session_id:
            return None
        try:
            return await self.session_store.get_session(session_id, include_history=False)
        except Exception as err:
            logger.warning("Could not read Slack session %s: %s", session_id, err)
            return None

    def _thread_say(self, channel_id: str) -> Any:
        client = self.app.client

        async def say(**kwargs: Any) -> Any:
            return await client.chat_postMessage(channel=channel_id, **kwargs)

        return say


def remember_request(text: str, ts: str) -> dict[str, str]:
    """The ``slack_last_request`` value of a request."""
    return {"text": text[:LAST_REQUEST_CHARS], "ts": ts}


def _metadata(record: SessionRecord | None) -> dict[str, Any]:
    return record.metadata if record is not None and isinstance(record.metadata, dict) else {}


def _team_of(body: dict[str, Any]) -> str:
    return str(
        (body.get("team") or {}).get("id")
        or (body.get("user") or {}).get("team_id")
        or body.get("team_id")
        or ""
    )

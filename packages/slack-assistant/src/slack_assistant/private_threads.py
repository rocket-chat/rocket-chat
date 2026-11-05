"""Private response mode: the requester's own session, run in a thread of their direct messages.

A private request leaves only a reaction in its channel. The work happens in a thread the bot
opens in the requester's DMs, which behaves like any other thread: the step it is on, Slack's
status and stop button, questions and approvals, the answer with **Share in thread**, and
replies that continue the session. Ephemeral messages were the surface before: a reload removed
them, they notified nobody, and Slack's stop button belongs to a whole thread, so a channel
thread could not offer it without showing everyone that a private run was going on.

A click or a reply in the DM thread carries only its DM channel and thread: the
``PrivateThreadStore`` (a table in production) names the session; the session records its DM
thread in ``PRIVATE_THREAD_KEY`` for the next request of the same channel thread.
"""

from __future__ import annotations

import logging
import re
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol

from slack_bolt.context.set_status.async_set_status import AsyncSetStatus

from slack_assistant.artifacts import head_of
from slack_assistant.block_kit import with_session
from slack_assistant.shortcuts import is_stop_request
from slack_assistant.state import SlackAssistantState
from specifications.interfaces.session import SessionRecord

logger = logging.getLogger(__name__)

PRIVATE_ANSWER_KEY = "slack_private_answer"
PRIVATE_THREAD_KEY = "slack_private_thread"
# What "Share in thread" may post: Slack refuses a section over 3,000 characters.
SHARED_ANSWER_CHARS = 2800
# The request quoted at the head of its DM thread: enough to recognise it, never the whole.
PREVIEW_CHARS = 280
MAX_MEMORY_THREADS = 5000
UNAVAILABLE_NOTICE = "🔒 I can't send you direct messages, so I can't work on this privately."
_BROADCAST = re.compile(r"<!(channel|here|everyone)[^>]*>")


@dataclass(frozen=True)
class PrivateThread:
    """A DM thread and the private session it runs, asked in ``origin`` channel's thread."""

    dm_channel_id: str
    dm_thread_ts: str
    session_id: str
    slack_user_id: str
    origin_channel_id: str
    origin_thread_ts: str
    origin_channel_type: str = "channel"


class PrivateThreadStore(Protocol):
    """Where a DM thread finds its private session again, on any replica, after a restart."""

    async def remember(self, org_id: str, thread: PrivateThread) -> None: ...

    async def find(
        self, org_id: str, dm_channel_id: str, dm_thread_ts: str
    ) -> PrivateThread | None: ...


class InMemoryPrivateThreads:
    """A ``PrivateThreadStore`` for a deployment without a database (and for tests)."""

    def __init__(self) -> None:
        self._threads: OrderedDict[tuple[str, str, str], PrivateThread] = OrderedDict()

    async def remember(self, org_id: str, thread: PrivateThread) -> None:
        self._threads[(org_id, thread.dm_channel_id, thread.dm_thread_ts)] = thread
        while len(self._threads) > MAX_MEMORY_THREADS:
            self._threads.popitem(last=False)

    async def find(
        self, org_id: str, dm_channel_id: str, dm_thread_ts: str
    ) -> PrivateThread | None:
        return self._threads.get((org_id, dm_channel_id, dm_thread_ts))


def request_preview(text: str, permalink: str, channel_id: str, *, follow_up: bool) -> str:
    """The message that heads a DM thread (or announces a later request of the same thread):
    where it was asked, and its first words quoted, without its broadcasts (``@here``)."""
    request = f"<{permalink}|your request>" if permalink else "your request"
    lead = (
        f":lock: New request in <#{channel_id}>"
        if follow_up
        else (f":lock: Working on {request} in <#{channel_id}>")
    )
    if follow_up and permalink:
        lead += f" (<{permalink}|open>)"
    words = " ".join(_BROADCAST.sub(r"@\1", text).split())
    if not words:
        return lead
    quoted = words if len(words) <= PREVIEW_CHARS else words[: PREVIEW_CHARS - 1] + "…"
    return f"{lead}\n> {quoted}"


class SlackPrivateThreadsMixin(SlackAssistantState):
    """The private response mode: its DM thread, its reaction, its answer and its sharing."""

    _private_threads: PrivateThreadStore

    # Provided by the assistant and its other mixins.
    async def _thread_permalink(self, channel_id: str, thread_ts: str) -> str:
        raise NotImplementedError

    async def _session_record(self, session_id: str) -> SessionRecord | None:
        raise NotImplementedError

    async def _durable_requester(self, session_id: str) -> str | None:
        raise NotImplementedError

    async def _stop_session(self, session_id: str, stopped_by: str) -> None:
        raise NotImplementedError

    # -- the DM thread -----------------------------------------------------------------------

    async def _private_thread_for(
        self,
        *,
        session_id: str,
        org_id: str,
        origin_channel_id: str,
        origin_thread_ts: str,
        origin_channel_type: str,
        user_id: str,
        request_text: str,
        request_ts: str,
        announce: bool,
    ) -> PrivateThread | None:
        """The session's DM thread, opened with a preview of the request when it has none.

        ``announce`` posts a later request into an existing thread, which is the request's
        place in that thread as a person's message is in any other one. None when the
        requester cannot be messaged (the app's Messages tab is off, or Slack is down).
        """
        known = await self._known_private_thread(session_id)
        if known is not None and not announce:
            return known
        permalink = (
            await self._thread_permalink(origin_channel_id, request_ts or origin_thread_ts)
            if self._slack_api_ready
            else ""
        )
        text = request_preview(
            request_text, permalink, origin_channel_id, follow_up=known is not None
        )
        try:
            posted = await self.app.client.chat_postMessage(
                channel=known.dm_channel_id if known else user_id,
                thread_ts=known.dm_thread_ts if known else None,
                text=text,
                unfurl_links=False,
            )
        except Exception as err:
            logger.warning("Could not open the private thread of %s: %s", session_id, err)
            return None
        if known is not None:
            return known
        thread = PrivateThread(
            dm_channel_id=str(posted.get("channel") or ""),
            dm_thread_ts=str(posted.get("ts") or ""),
            session_id=session_id,
            slack_user_id=user_id,
            origin_channel_id=origin_channel_id,
            origin_thread_ts=origin_thread_ts,
            origin_channel_type=origin_channel_type,
        )
        if not (thread.dm_channel_id and thread.dm_thread_ts):
            return None
        await self._remember_private_thread(org_id, thread)
        return thread

    async def _known_private_thread(self, session_id: str) -> PrivateThread | None:
        """The DM thread a private session already runs in, from its record."""
        record = await self._session_record(session_id)
        metadata = record.metadata if record and isinstance(record.metadata, dict) else {}
        known = metadata.get(PRIVATE_THREAD_KEY)
        if not isinstance(known, dict) or not known.get("channel") or not known.get("ts"):
            return None
        return PrivateThread(
            dm_channel_id=str(known["channel"]),
            dm_thread_ts=str(known["ts"]),
            session_id=session_id,
            slack_user_id=str(metadata.get("slack_user_id") or ""),
            origin_channel_id=str(metadata.get("slack_channel_id") or ""),
            origin_thread_ts=str(metadata.get("slack_thread_ts") or ""),
            origin_channel_type=str(known.get("origin_channel_type") or "channel"),
        )

    async def _remember_private_thread(self, org_id: str, thread: PrivateThread) -> None:
        """Both directions, durably: the DM thread names its session, the session its thread."""
        try:
            await self._private_threads.remember(org_id, thread)
        except Exception as err:
            logger.warning("Could not record the private thread of %s: %s", thread.session_id, err)
        if self.session_store is None:
            return

        def remember(record: SessionRecord) -> None:
            known = record.metadata if isinstance(record.metadata, dict) else {}
            record.metadata = {
                **known,
                PRIVATE_THREAD_KEY: {
                    "channel": thread.dm_channel_id,
                    "ts": thread.dm_thread_ts,
                    "origin_channel_type": thread.origin_channel_type,
                },
            }

        try:
            await self.session_store.mutate_session(thread.session_id, org_id, remember)
        except Exception as err:
            logger.warning("Could not keep the private thread on %s: %s", thread.session_id, err)

    async def _private_thread_at(
        self, channel_id: str, thread_ts: str, team_id: str
    ) -> PrivateThread | None:
        """The private thread a direct-message thread is, if it is one."""
        if not (channel_id.startswith("D") and thread_ts):
            return None
        org_id = await self._workspace_org(team_id or self.team_id or "")
        self._bind_tenant(org_id)
        try:
            return await self._private_threads.find(org_id, channel_id, thread_ts)
        except Exception as err:
            logger.warning(
                "Could not look up the private thread %s/%s: %s", channel_id, thread_ts, err
            )
            return None

    def _private_thread_say(self, thread: PrivateThread) -> Callable[..., Awaitable[Any]]:
        """A ``say`` into the DM thread. Its buttons name their session: a click there cannot
        name it through its channel and thread."""
        client = self.app.client

        async def say(**kwargs: Any) -> Any:
            kwargs["thread_ts"] = thread.dm_thread_ts
            if kwargs.get("blocks"):
                kwargs["blocks"] = with_session(kwargs["blocks"], thread.session_id)
            return await client.chat_postMessage(channel=thread.dm_channel_id, **kwargs)

        return say

    def _private_thread_status(self, thread: PrivateThread) -> AsyncSetStatus:
        return AsyncSetStatus(
            client=self.app.client, channel_id=thread.dm_channel_id, thread_ts=thread.dm_thread_ts
        )

    # -- a message in the DM thread ----------------------------------------------------------

    async def _on_private_thread_message(self, event: dict[str, Any]) -> bool:
        """A message in a private DM thread: a stop, or the next request of its session.
        Whether it was one (a DM thread that is not private is left to the usual handling)."""
        thread = await self._private_thread_at(
            str(event.get("channel") or ""),
            str(event.get("thread_ts") or ""),
            str(event.get("team") or ""),
        )
        if thread is None:
            return False
        user_id = str(event.get("user") or "")
        if user_id != thread.slack_user_id:
            return True
        text = str(event.get("text") or "")
        if is_stop_request(text):
            await self._stop_private_thread(thread, user_id)
            return True
        say = self._private_thread_say(thread)

        async def tell(*, text: str, **_: Any) -> Any:
            # A notice (a denial, an unreadable file) the request would show under its channel
            # thread goes to the DM thread it was written in.
            return await say(text=text)

        await self.process_incoming_request(
            channel_id=thread.origin_channel_id,
            thread_ts=thread.origin_thread_ts,
            raw_text=text,
            say_fn=say,
            status_fn=self._private_thread_status(thread),
            ephemeral_fn=tell,
            user_id=user_id,
            channel_type=thread.origin_channel_type,
            team_id=str(event.get("team") or "") or None,
            files=event.get("files"),
            message_ts=str(event.get("ts") or "") or None,
            private_thread=thread,
        )
        return True

    async def _stop_private_thread(self, thread: PrivateThread, user_id: str) -> None:
        if (
            thread.session_id in self._active_turns
            and await self._durable_requester(thread.session_id) == user_id
        ):
            await self._stop_session(thread.session_id, user_id)
            return
        await self._private_thread_say(thread)(text="Nothing of yours is running here.")

    # -- in the channel ----------------------------------------------------------------------

    async def _mark_taken(self, channel_id: str, message_ts: str) -> None:
        """The request's only trace in its channel: the bot's reaction."""
        bot = self.bot
        if bot is None or not bot.private_reaction or not message_ts or not self._slack_api_ready:
            return
        try:
            await self.app.client.reactions_add(
                channel=channel_id, timestamp=message_ts, name=bot.private_reaction
            )
        except Exception as err:
            logger.debug("Private-mode reaction skipped: %s", err)

    async def _keep_private_answer(self, session_id: str, org_id: str, text: str) -> None:
        """The answer **Share in thread** posts, kept on the session for any replica."""
        if self.session_store is None or not text:
            return

        def keep(record: SessionRecord) -> None:
            known = record.metadata if isinstance(record.metadata, dict) else {}
            record.metadata = {**known, PRIVATE_ANSWER_KEY: head_of(text, SHARED_ANSWER_CHARS)}

        try:
            await self.session_store.mutate_session(session_id, org_id, keep)
        except Exception as err:
            logger.warning("Could not keep the private answer of %s: %s", session_id, err)

    async def _share_private_answer(self, body: dict[str, Any]) -> None:
        """Post a private answer publicly in its channel thread, when its requester asks."""
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

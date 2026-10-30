"""One renderer per Slack session: every turn of a Slack thread reaches it, whatever started it.

A turn used to be shown only while the Slack request that started it waited on it, so a wake
(CI, a review, a timer), a goal continuation, a turn a GitHub event started, a resumed turn or
a message sent from the web app on a Slack session never reached its thread. Here each Slack
session has one watcher on its event topic. A turn begins with the runner's ``turn_start``
status: a Slack request that started it registered how to show it (its ``say``, its streamed
reply, who asked) under the message id it sent, and gets the rendered outcome back to post;
any other turn is shown in the session's stored thread and posted here.

Watchers run where the bot's Socket Mode connection runs, the elected leader, so a turn is
posted once however many replicas run; the shared event bus brings them every replica's events.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from slack_assistant.cards import build_followup_blocks
from slack_assistant.policy import SlackOrgPolicy
from slack_assistant.refusals import failure_notice
from slack_assistant.turn_stream import SlackTurnStreamMixin, TurnOutcome, TurnView
from specifications.interfaces.agent import TurnOverlay
from specifications.interfaces.events import EventType, SessionEvent, organization_channel

logger = logging.getLogger(__name__)

# A watcher waiting for a registered turn that never begins gives up after this long.
IDLE_CLOSE_SECONDS = 600.0
# The runner returns right after publishing a turn's closing event; its watcher may still be
# rendering it.
FINAL_GRACE_SECONDS = 5.0
# How long after a turn its suggested follow-up is still posted (the runner asks a model for it
# once the answer is stored).
FOLLOWUP_WAIT_SECONDS = 30.0


@dataclass
class _Registration:
    """How a Slack request wants its own turn shown, and the signal that it was."""

    view: TurnView
    done: asyncio.Future[None]


class SlackDeliveryMixin(SlackTurnStreamMixin):
    """Session watchers that render every turn of a Slack session into its thread."""

    _watchers: dict[str, asyncio.Task[None]]
    _registrations: dict[str, _Registration]
    _org_watchers: dict[str, asyncio.Task[None]]

    def _init_delivery(self) -> None:
        self._watchers = {}
        self._registrations = {}
        self._org_watchers = {}

    # Provided by the assistant's other mixins.
    async def _load_org_policy(self, org_id: str) -> SlackOrgPolicy:
        raise NotImplementedError

    def _session_link(self, org_policy: SlackOrgPolicy, session_id: str) -> str | None:
        raise NotImplementedError

    def _ephemeral_say(self, channel_id: str, user_id: str) -> Callable[..., Awaitable[Any]]:
        raise NotImplementedError

    # -- a turn a Slack request starts -------------------------------------------------------

    async def _hold_if_busy(self, view: TurnView, prompt: str, author_user_id: str) -> bool:
        """Queue a message sent while its session works; whether it was (and the person told).

        It runs as the session's next turn, which the session's watcher shows like any other:
        no second request waits on the session, and a restart loses nothing.
        """
        if self._hold_message_fn is None:
            return False
        try:
            held = await self._hold_message_fn(
                session_id=view.session_id,
                prompt=prompt,
                author_user_id=author_user_id,
                message_id=view.message_id,
            )
        except Exception as err:
            logger.warning("Could not queue a Slack message for %s: %s", view.session_id, err)
            return False
        if not held:
            return False
        await self._safely_update_status(view.status_fn, "")
        await view.say_fn(text="Queued — I'll take this next.", thread_ts=view.thread_ts)
        return True

    async def _run_shared_turn(
        self,
        view: TurnView,
        *,
        prompt: str,
        overlay: TurnOverlay,
        author_user_id: str,
        reasoning_effort: str | None = None,
        agent_id: str | None = None,
    ) -> TurnOutcome:
        """Start the shared turn and wait until the session's watcher has rendered it.

        ``reasoning_effort`` and ``agent_id`` come from shortcuts; the platform resolves and
        checks them like a choice made in the web app."""
        if self._run_turn_fn is None or self.event_bus is None:
            raise RuntimeError("Slack is not wired to the shared turn pipeline")
        done: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        self._registrations[view.message_id] = _Registration(view, done)
        self._ensure_watcher(view.session_id, view.org_id)
        run_turn = self._run_turn_fn

        async def _run() -> None:
            choices: dict[str, str] = {}
            if reasoning_effort:
                choices["reasoning_effort"] = reasoning_effort
            if agent_id:
                choices["agent_id"] = agent_id
            await run_turn(
                session_id=view.session_id,
                prompt=prompt,
                overlay=overlay,
                author_user_id=author_user_id,
                message_id=view.message_id,
                **choices,
            )

        runner: asyncio.Task[None] = asyncio.create_task(_run())
        runner.add_done_callback(self._log_runner_failure)
        self._background.add(runner)
        runner.add_done_callback(self._background.discard)
        try:
            await asyncio.wait({done, runner}, return_when=asyncio.FIRST_COMPLETED)
            if not done.done():
                await asyncio.wait({done}, timeout=FINAL_GRACE_SECONDS)
        finally:
            self._registrations.pop(view.message_id, None)
        failure = runner.exception() if runner.done() and not runner.cancelled() else None
        if failure is not None:
            raise failure
        if not done.done():
            # The turn ended without this view seeing it (refused before it began, or its
            # events were dropped): the stored answer is still this turn's.
            await self._read_stored_answer(view)
        return view.outcome

    @staticmethod
    def _log_runner_failure(task: asyncio.Task[Any]) -> None:
        if not task.cancelled() and task.exception() is not None:
            logger.error("Shared turn task failed: %s", task.exception(), exc_info=task.exception())

    # -- session watchers --------------------------------------------------------------------

    def _ensure_watcher(self, session_id: str, org_id: str, *, running: bool = False) -> None:
        """Watch ``session_id`` unless already watched; ``running`` shows a turn under way now."""
        if self.event_bus is None or session_id in self._watchers:
            return
        # The queue is open before this returns, so a turn started next misses nothing.
        queue, close = self.event_bus.open_queue(session_id)
        task = asyncio.create_task(self._watch_session(session_id, org_id, queue, close, running))
        self._watchers[session_id] = task
        self._background.add(task)
        task.add_done_callback(self._background.discard)

    async def _watch_session(
        self,
        session_id: str,
        org_id: str,
        queue: asyncio.Queue[SessionEvent],
        close: Callable[[], None],
        running: bool,
    ) -> None:
        view = await self._thread_view(session_id, org_id) if running else None
        # A turn just shown, while its suggested follow-up may still come.
        lingering: tuple[TurnView, float] | None = None
        try:
            # A watcher lives while it shows a turn or a request waits for one; a later turn
            # opens another (the organization follower sees the session start working).
            while view is not None or lingering is not None or self._awaited(session_id):
                try:
                    event = await asyncio.wait_for(
                        queue.get(), timeout=self._watch_timeout(view, lingering)
                    )
                except TimeoutError:
                    lingering = None
                    continue
                if lingering is not None and event.event_type == EventType.FOLLOWUP_SUGGESTION:
                    await self._post_followup(lingering[0], event.payload)
                    lingering = None
                    continue
                shown = view
                view = await self._deliver(event, view, session_id, org_id)
                lingering = self._linger_after(shown, view, event, lingering)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Slack delivery for session %s stopped", session_id)
        finally:
            # No await between the loop's last check and here: a request registering
            # meanwhile finds no watcher and opens its own.
            close()
            self._watchers.pop(session_id, None)

    def _watch_timeout(
        self, view: TurnView | None, lingering: tuple[TurnView, float] | None
    ) -> float | None:
        if view is not None:
            return None
        if lingering is not None:
            return max(0.0, lingering[1] - self._clock())
        return IDLE_CLOSE_SECONDS

    def _linger_after(
        self,
        shown: TurnView | None,
        view: TurnView | None,
        event: SessionEvent,
        lingering: tuple[TurnView, float] | None,
    ) -> tuple[TurnView, float] | None:
        """Keep listening for a follow-up after a completed turn shown in a shared thread (the
        runner suggests one after storing the answer); a new turn ends the wait."""
        if view is not None:
            return None
        if shown is None or not self._is_terminal(event):
            return lingering
        outcome = shown.outcome
        if shown.private or outcome.final_status != "completed" or outcome.error or outcome.refusal:
            return None
        return shown, self._clock() + FOLLOWUP_WAIT_SECONDS

    async def _post_followup(self, view: TurnView, payload: dict[str, Any]) -> None:
        suggestion = " ".join(str(payload.get("suggestion") or "").split())
        if not suggestion:
            return
        try:
            await view.say_fn(
                blocks=build_followup_blocks(suggestion, view.session_id),
                text=f"Suggested next step: {suggestion}",
                thread_ts=view.thread_ts,
            )
        except Exception as err:
            logger.info("Follow-up not posted in %s: %s", view.session_id, err)

    def _awaited(self, session_id: str) -> bool:
        """Whether a Slack request waits for a turn of ``session_id`` to be shown."""
        return any(r.view.session_id == session_id for r in self._registrations.values())

    async def _deliver(
        self, event: SessionEvent, view: TurnView | None, session_id: str, org_id: str
    ) -> TurnView | None:
        """Render one event; return the view of the turn still being shown, if any."""
        if event.event_type == EventType.STATUS_CHANGED and event.payload.get("turn_start"):
            view = await self._begin_view(
                str(event.payload.get("message_id") or ""), session_id, org_id
            )
        if view is None:
            return None
        await self._render_event(event, view)
        if not self._is_terminal(event):
            return view
        await self._read_stored_answer(view)
        registration = self._registrations.get(view.message_id)
        if registration is not None and registration.view is view:
            # Removed here, not by the request when it resumes: until then this watcher
            # would think a turn was still awaited.
            del self._registrations[view.message_id]
            if not registration.done.done():
                registration.done.set_result(None)
        else:
            await self._post_unrequested(view)
        return None

    async def _begin_view(self, message_id: str, session_id: str, org_id: str) -> TurnView | None:
        registration = self._registrations.get(message_id) if message_id else None
        if registration is not None:
            registration.view.own_turn_started = True
            await self._own_turn_started(registration.view)
            return registration.view
        return await self._thread_view(session_id, org_id)

    # -- turns no Slack request is waiting on ------------------------------------------------

    async def _thread_view(self, session_id: str, org_id: str) -> TurnView | None:
        """A view posting into the session's stored thread (privately for a private request)."""
        if self.session_store is None or not self._slack_api_ready:
            return None
        try:
            record = await self.session_store.get_session(
                session_id, org_id or None, include_history=False
            )
        except Exception as err:
            logger.warning("Could not read Slack session %s: %s", session_id, err)
            return None
        metadata = record.metadata if record and isinstance(record.metadata, dict) else {}
        channel_id = str(metadata.get("slack_channel_id") or "")
        thread_ts = str(metadata.get("slack_thread_ts") or "")
        if not channel_id or not thread_ts:
            return None
        requester = (
            str(metadata.get("slack_turn_requester") or metadata.get("slack_user_id") or "") or None
        )
        private = bool(metadata.get("slack_private")) and requester is not None
        client = self.app.client

        async def in_thread(**kwargs: Any) -> Any:
            return await client.chat_postMessage(channel=channel_id, **kwargs)

        return TurnView(
            session_id=session_id,
            channel_id=channel_id,
            thread_ts=thread_ts,
            requester_slack_id=requester,
            say_fn=self._ephemeral_say(channel_id, requester or "") if private else in_thread,
            status_fn=None,
            outcome=TurnOutcome(),
            private=private,
            org_id=org_id or (record.tenant_org_id if record else ""),
            own_turn_started=True,
        )

    async def _post_unrequested(self, view: TurnView) -> None:
        """Post the answer of a turn no Slack request waited on: a wake, a web message, a resume."""
        policy = await self._load_org_policy(view.org_id)
        view.link_url = self._session_link(policy, view.session_id)
        if view.outcome.refusal or view.outcome.error:
            await view.say_fn(
                text=view.outcome.refusal
                or failure_notice(view.outcome.error, shared=not view.private),
                thread_ts=view.thread_ts,
            )
            return
        await self._post_result(view, policy)

    # -- turns started anywhere -------------------------------------------------------------

    def _start_delivery(self, org_ids: set[str]) -> None:
        """Follow the organizations' session updates, to watch a Slack session once it works."""
        if self.event_bus is None:
            return
        for org_id in org_ids:
            if org_id and org_id not in self._org_watchers:
                queue, close = self.event_bus.open_queue(organization_channel(org_id))
                self._org_watchers[org_id] = asyncio.create_task(
                    self._watch_organization(org_id, queue, close)
                )

    def _stop_delivery(self) -> None:
        for task in [*self._org_watchers.values(), *self._watchers.values()]:
            task.cancel()
        self._org_watchers.clear()
        self._watchers.clear()

    async def _watch_organization(
        self, org_id: str, queue: asyncio.Queue[SessionEvent], close: Callable[[], None]
    ) -> None:
        try:
            while True:
                event = await queue.get()
                payload = event.payload
                if event.event_type != EventType.SESSION_UPDATED or not payload.get(
                    "is_processing"
                ):
                    continue
                session_id = str(payload.get("session_id") or "")
                if (
                    session_id
                    and session_id not in self._watchers
                    and await self._is_ours(session_id, org_id)
                ):
                    self._ensure_watcher(session_id, org_id, running=True)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Slack delivery stopped following organization %s", org_id)
        finally:
            close()
            self._org_watchers.pop(org_id, None)

    async def _is_ours(self, session_id: str, org_id: str) -> bool:
        """Whether this bot owns ``session_id``: a Slack thread it started."""
        if self.session_store is None:
            return False
        try:
            record = await self.session_store.get_session(session_id, org_id, include_history=False)
        except Exception:
            return False
        metadata = record.metadata if record and isinstance(record.metadata, dict) else {}
        if not metadata.get("slack_channel_id"):
            return False
        bot = getattr(self, "bot", None)
        return str(metadata.get("slack_bot_id") or "") == (bot.id if bot is not None else "")

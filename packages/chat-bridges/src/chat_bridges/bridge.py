"""One bridge for every chat platform: a message addressed to the bot becomes a turn of the
thread's session, run by the shared turn pipeline, and the turn is rendered back as messages.

The bridge holds a placeholder message per turn and edits it with the step the turn is on (at
most every ``PROGRESS_INTERVAL_SECONDS``), then with the answer; a question or an approval is a
message the requester answers in the thread ("approve", "reject", an option's number). The
platform calls back into the API only through ``BridgeCallbacks``, so this package never imports
it. People are not linked to members here: every turn runs as the platform user's guest member,
with the publishing tools denied, like an unlinked Slack user.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import Awaitable, Callable, Coroutine
from dataclasses import dataclass, field
from typing import Any

from specifications.interfaces.agent import TurnOverlay
from specifications.interfaces.chat import ChatPlatformProtocol, IncomingChatMessage
from specifications.interfaces.events import EventBusProtocol, EventType, SessionEvent

logger = logging.getLogger(__name__)

PROGRESS_INTERVAL_SECONDS = 3.0
# A turn that sends no event for this long is reported as still running and no longer watched.
WATCH_TIMEOUT_SECONDS = 3600.0
# Turns a person may start per minute (a bucket per platform user).
TURNS_PER_MINUTE = 6
MAX_PROMPT_CHARS = 20_000
# Publishing leaves the sandbox under someone's identity: never for an unlinked chat user.
DENIED_TOOLS = frozenset({"git_push", "git_open_pr", "git_revert"})
_APPROVE = re.compile(r"^\s*(approve|yes|ok)\b", re.I)
_REJECT = re.compile(r"^\s*(reject|no|deny)\b", re.I)


@dataclass(frozen=True)
class BridgeCallbacks:
    """What the platform does for the bridge (wired by ``api/bootstrap/chat.py``)."""

    # (platform, tenant, user id, user name) -> the guest member that owns what they start.
    guest: Callable[[str, str, str, str], Awaitable[str]]
    # (session id, owner, title, metadata) -> creates the session when it does not exist.
    ensure_session: Callable[[str, str, str, dict[str, Any]], Awaitable[None]]
    # Keyword arguments of the shared pipeline (session_id, prompt, overlay, author_user_id,
    # message_id).
    run_turn: Callable[..., Coroutine[Any, Any, None]]
    # (session id, prompt, author, message id) -> whether a running turn's queue kept it.
    hold_message: Callable[..., Awaitable[bool]]
    submit_approval: Callable[[str, str, bool], Awaitable[None]]
    submit_answer: Callable[[str, str, list[str]], Awaitable[None]]
    stored_answer: Callable[[str], Awaitable[str | None]]
    event_bus: EventBusProtocol
    org_id: str
    web_base_url: str = ""


@dataclass
class _Gate:
    kind: str  # approval or question
    gate_id: str
    requester: str
    options: list[str] = field(default_factory=list)


@dataclass
class _Turn:
    message: IncomingChatMessage
    session_id: str
    placeholder: str
    chunks: list[str] = field(default_factory=list)
    last_progress: float = 0.0
    error: str = ""


class ChatBridge:
    def __init__(
        self,
        platform: ChatPlatformProtocol,
        callbacks: BridgeCallbacks,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.platform = platform
        self.callbacks = callbacks
        self._clock = clock
        self._gates: dict[str, _Gate] = {}
        self._buckets: dict[str, tuple[float, float]] = {}
        self._background: set[asyncio.Task[None]] = set()

    def session_id_of(self, message: IncomingChatMessage) -> str:
        """The thread's session (Docker-name safe, at most 64 characters)."""
        raw = f"{message.platform}_{message.conversation_id}_{message.thread_id}"
        return re.sub(r"[^A-Za-z0-9_.-]+", "_", raw)[:64]

    async def handle(self, message: IncomingChatMessage) -> None:
        """Answer one message addressed to the bot."""
        session_id = self.session_id_of(message)
        gate = self._gates.get(session_id)
        if (
            gate is not None
            and gate.requester == message.user_id
            and await self._answer_gate(gate, session_id, message)
        ):
            return
        if not self._admit(message.user_id):
            await self.platform.reply(
                message.conversation_id,
                message.thread_id,
                "Too many requests: try again in a minute.",
            )
            return
        owner = await self.callbacks.guest(
            message.platform, message.tenant_id, message.user_id, message.user_name
        )
        await self.callbacks.ensure_session(
            session_id,
            owner,
            f"{message.platform.capitalize()}: {message.text[:40]}",
            {
                "chat_platform": message.platform,
                "chat_conversation_id": message.conversation_id,
                "chat_thread_id": message.thread_id,
                "chat_permalink": self.platform.permalink(message) or "",
                "chat_direct": message.direct,
                "toolsets": [],
            },
        )
        prompt = f"[Speaker: {message.user_name}] {message.text[:MAX_PROMPT_CHARS]}"
        if await self.callbacks.hold_message(
            session_id=session_id,
            prompt=prompt,
            author_user_id=owner,
            message_id=f"chat-{message.message_id}",
        ):
            await self.platform.reply(
                message.conversation_id, message.thread_id, "Queued: I'll take this next."
            )
            return
        await self._run(message, session_id, prompt, owner)

    def _admit(self, user_id: str) -> bool:
        """A token bucket per user: ``TURNS_PER_MINUTE`` refilled over a minute."""
        now = self._clock()
        tokens, at = self._buckets.get(user_id, (float(TURNS_PER_MINUTE), now))
        tokens = min(float(TURNS_PER_MINUTE), tokens + (now - at) * TURNS_PER_MINUTE / 60.0)
        if tokens < 1:
            self._buckets[user_id] = (tokens, now)
            return False
        self._buckets[user_id] = (tokens - 1, now)
        if len(self._buckets) > 10_000:
            self._buckets.pop(next(iter(self._buckets)))
        return True

    def _overlay(self) -> TurnOverlay:
        return TurnOverlay(
            stable_suffix=(
                f"You are answering in a {self.platform.name.capitalize()} thread. Each message is "
                "prefixed '[Speaker: <name>]'. Your final answer is posted in the thread by the "
                "platform: write it in Markdown, concise. Publishing (push, pull requests) is not "
                "available from this chat; say what to publish and from where instead."
            ),
            denied_tools=DENIED_TOOLS,
        )

    async def _run(
        self, message: IncomingChatMessage, session_id: str, prompt: str, owner: str
    ) -> None:
        placeholder = await self.platform.reply(
            message.conversation_id, message.thread_id, "Working on it…"
        )
        turn = _Turn(message, session_id, placeholder)
        # Opened before the turn starts, so none of its events is missed.
        queue, close = self.callbacks.event_bus.open_queue(session_id)
        runner: asyncio.Task[None] = asyncio.create_task(
            self.callbacks.run_turn(
                session_id=session_id,
                prompt=prompt,
                overlay=self._overlay(),
                author_user_id=owner,
                message_id=f"chat-{message.message_id}",
            )
        )
        try:
            await self._watch(turn, queue, runner)
        finally:
            close()
        await self._finish(turn)

    async def _watch(
        self, turn: _Turn, queue: asyncio.Queue[SessionEvent], runner: asyncio.Task[None]
    ) -> None:
        while True:
            getter = asyncio.ensure_future(queue.get())
            done, _ = await asyncio.wait(
                {getter, runner}, timeout=WATCH_TIMEOUT_SECONDS, return_when=asyncio.FIRST_COMPLETED
            )
            if getter not in done:
                getter.cancel()
                if runner in done and runner.exception() is not None:
                    turn.error = str(runner.exception())
                if runner in done or not done:
                    return
                continue
            event = getter.result()
            if await self._render(turn, event):
                return

    async def _render(self, turn: _Turn, event: SessionEvent) -> bool:
        """Render one event; True once the turn's closing status arrived."""
        payload = event.payload
        kind = event.event_type
        if kind == EventType.TOKEN:
            turn.chunks.append(str(payload.get("delta") or ""))
        elif kind == EventType.TOOL_STARTED and not payload.get("subagent_id"):
            turn.chunks.clear()
            await self._progress(turn, str(payload.get("name") or "a tool"))
        elif kind == EventType.ERROR:
            turn.error = str(payload.get("error") or "the turn failed")
        elif kind == EventType.DECISION_REQUIRED:
            await self._ask(turn, payload)
        elif (
            kind == EventType.APPROVAL_REQUIRED
            and payload.get("policy_decision", "require_human") == "require_human"
        ):
            await self._ask_approval(turn, payload)
        elif kind == EventType.STATUS_CHANGED and payload.get("final"):
            return True
        return False

    async def _progress(self, turn: _Turn, tool: str) -> None:
        now = self._clock()
        if now - turn.last_progress < PROGRESS_INTERVAL_SECONDS:
            return
        turn.last_progress = now
        step = tool.replace("_", " ")
        await self._safely_edit(turn, f"Working on it… ({step})")

    async def _ask(self, turn: _Turn, payload: dict[str, Any]) -> None:
        options = [str(o) for o in payload.get("options") or []]
        self._gates[turn.session_id] = _Gate(
            "question", str(payload.get("question_id") or ""), turn.message.user_id, options
        )
        listed = "\n".join(f"{i}. {o}" for i, o in enumerate(options, 1))
        text = (
            f"**{payload.get('question_text')}**\n{listed}\n\nReply with the number of your choice."
        )
        await self.platform.reply(turn.message.conversation_id, turn.message.thread_id, text)

    async def _ask_approval(self, turn: _Turn, payload: dict[str, Any]) -> None:
        self._gates[turn.session_id] = _Gate(
            "approval", str(payload.get("action_id") or ""), turn.message.user_id
        )
        what = str(payload.get("command_or_path") or payload.get("action_type") or "an action")
        text = f"**Approval needed** ({payload.get('risk_level', 'medium')} risk): `{what[:500]}`\n\nReply **approve** or **reject**."
        await self.platform.reply(turn.message.conversation_id, turn.message.thread_id, text)

    async def _answer_gate(
        self, gate: _Gate, session_id: str, message: IncomingChatMessage
    ) -> bool:
        """Whether the message answered the thread's pending question or approval."""
        text = message.text.strip()
        try:
            if gate.kind == "approval" and (_APPROVE.match(text) or _REJECT.match(text)):
                await self.callbacks.submit_approval(
                    session_id, gate.gate_id, bool(_APPROVE.match(text))
                )
            elif gate.kind == "question" and text.isdigit() and 1 <= int(text) <= len(gate.options):
                await self.callbacks.submit_answer(
                    session_id, gate.gate_id, [gate.options[int(text) - 1]]
                )
            else:
                return False
        except KeyError:
            await self.platform.reply(
                message.conversation_id, message.thread_id, "That was already answered or expired."
            )
        self._gates.pop(session_id, None)
        return True

    async def _finish(self, turn: _Turn) -> None:
        self._gates.pop(turn.session_id, None)
        if turn.error:
            text = "Something went wrong while I was working on this. Ask again, or open the session for the details."
        else:
            text = (
                await self.callbacks.stored_answer(turn.session_id) or "".join(turn.chunks)
            ).strip() or "Done."
        base = self.callbacks.web_base_url.rstrip("/")
        if base:
            text += f"\n\n[Open in Rocket Chat]({base}/chat/{turn.session_id})"
        first, *rest = _split(text, self.platform.max_message_chars)
        await self._safely_edit(turn, first)
        for part in rest:
            await self.platform.reply(turn.message.conversation_id, turn.message.thread_id, part)

    async def _safely_edit(self, turn: _Turn, text: str) -> None:
        try:
            await self.platform.edit(turn.message.conversation_id, turn.placeholder, text)
        except Exception as err:
            logger.info(
                "Could not edit the %s message; posting instead: %s", self.platform.name, err
            )
            await self.platform.reply(turn.message.conversation_id, turn.message.thread_id, text)


def _split(text: str, limit: int) -> list[str]:
    """``text`` in messages of at most ``limit`` characters, cut at line ends when possible."""
    parts: list[str] = []
    while len(text) > limit:
        cut = text.rfind("\n", 0, limit)
        cut = cut if cut > limit // 2 else limit
        parts.append(text[:cut])
        text = text[cut:].lstrip("\n")
    return [*parts, text]

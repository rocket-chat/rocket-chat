"""Microsoft Teams and Mattermost through ``chat_bridges`` (Slack has its own integration).

Each platform is on when its operator variables are set. Mattermost's socket runs on the elected
leader (one reply per message whatever the number of replicas); Teams posts each activity to
``POST /v1/chat/teams/messages`` on any replica. Turns run through the same guarded pipeline as
the web app and Slack.
"""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import suppress
from typing import Any

from chat_bridges import BridgeCallbacks, ChatBridge
from chat_bridges.mattermost import MattermostPlatform
from chat_bridges.teams import TeamsPlatform
from fastapi import FastAPI

from api.bootstrap.core import CoreServices
from api.db.session import current_tenant_org_id
from api.services.cluster import LeaderElection
from api.services.queued_messages import hold_for_running_turn
from api.services.slack_link import ensure_chat_guest
from api.turns.tasks import run_guarded_turn
from specifications.interfaces.agent import TurnOverlay
from specifications.interfaces.events import EventType, SessionEvent
from specifications.interfaces.session import SessionRecord, SessionSource

logger = logging.getLogger(__name__)


class _Callbacks:
    """What the bridges need from the platform, bound to one organization."""

    def __init__(self, core: CoreServices, org_id: str) -> None:
        self._core = core
        self.org_id = org_id

    def _bind(self) -> None:
        current_tenant_org_id.set(self.org_id)

    async def guest(self, platform: str, tenant: str, user_id: str, name: str) -> str:
        self._bind()
        return await ensure_chat_guest(
            self._core.db_manager, self.org_id, platform, tenant, user_id, name
        )

    async def ensure_session(
        self, session_id: str, owner: str, title: str, metadata: dict[str, Any]
    ) -> None:
        self._bind()
        store = self._core.session_store
        if await store.get_session(session_id, include_history=False) is not None:
            return
        await store.create_session(
            SessionRecord(
                session_id=session_id,
                tenant_org_id=self.org_id,
                tenant_user_id=owner,
                title=title,
                source=SessionSource.CHAT,
                is_shared=True,
                metadata={**metadata, "title_source": "auto"},
            )
        )

    async def run_turn(
        self,
        *,
        session_id: str,
        prompt: str,
        overlay: TurnOverlay,
        author_user_id: str,
        message_id: str,
    ) -> None:
        self._bind()
        core = self._core
        await run_guarded_turn(
            session_id=session_id,
            prompt=prompt,
            message_id=message_id,
            orchestrator=core.orchestrator,
            event_bus=core.event_bus,
            driver=core.sandbox_driver,
            registry=core.tool_registry,
            store=core.session_store,
            git_engine=core.git_engine,
            db_mgr=core.db_manager,
            author_user_id=author_user_id,
            attachment_service=core.attachment_service,
            concurrency_gate=core.concurrency_gate,
            session_locks=core.session_locks,
            overlay=overlay,
        )

    async def hold_message(
        self, *, session_id: str, prompt: str, author_user_id: str, message_id: str
    ) -> bool:
        self._bind()
        core = self._core
        return await hold_for_running_turn(
            session_store=core.session_store,
            orchestrator=core.orchestrator,
            event_bus=core.event_bus,
            db_mgr=core.db_manager,
            org_id=self.org_id,
            session_id=session_id,
            prompt=prompt,
            delivery="queue",
            message_id=message_id,
            author_user_id=author_user_id,
        )

    async def submit_approval(self, session_id: str, gate_id: str, approved: bool) -> None:
        await self._core.orchestrator.submit_human_approval(
            session_id=session_id, action_id=gate_id, approved=approved
        )

    async def submit_answer(self, session_id: str, question_id: str, options: list[str]) -> None:
        await self._core.orchestrator.submit_question_answer(
            session_id=session_id, question_id=question_id, selected_options=options
        )
        await self._core.event_bus.publish(
            SessionEvent(
                session_id=session_id,
                event_type=EventType.DECISION_SUBMITTED,
                payload={
                    "question_id": question_id,
                    "selected_options": options,
                    "custom_text": None,
                },
            )
        )

    async def stored_answer(self, session_id: str) -> str | None:
        self._bind()
        record = await self._core.session_store.get_session(session_id)
        for message in reversed(record.conversation_history if record else []):
            if message.role == "user":
                return None
            if message.role == "assistant" and message.content and not message.tool_calls:
                return message.content
        return None

    def bridge_callbacks(self) -> BridgeCallbacks:
        return BridgeCallbacks(
            guest=self.guest,
            ensure_session=self.ensure_session,
            run_turn=self.run_turn,
            hold_message=self.hold_message,
            submit_approval=self.submit_approval,
            submit_answer=self.submit_answer,
            stored_answer=self.stored_answer,
            event_bus=self._core.event_bus,
            org_id=self.org_id,
            web_base_url=os.getenv("ROCKET_PUBLIC_URL", ""),
        )


class ChatBridges:
    """The configured chat platforms; ``stop`` ends the Mattermost listener."""

    def __init__(self) -> None:
        self.teams: ChatBridge | None = None
        self.mattermost: ChatBridge | None = None
        self._tasks: list[asyncio.Task[None]] = []

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task


def start_chat_bridges(app: FastAPI, core: CoreServices) -> ChatBridges:
    bridges = ChatBridges()
    org_id = os.getenv("CHAT_BRIDGE_ORG_ID") or os.getenv("ROCKET_WEBHOOK_ORG_ID") or "default_org"
    callbacks = _Callbacks(core, org_id).bridge_callbacks()
    if (app_id := os.getenv("TEAMS_APP_ID")) and (password := os.getenv("TEAMS_APP_PASSWORD")):
        bridges.teams = ChatBridge(TeamsPlatform(app_id, password), callbacks)
    if (url := os.getenv("MATTERMOST_URL")) and (token := os.getenv("MATTERMOST_BOT_TOKEN")):
        platform = MattermostPlatform(url, token)
        bridge = ChatBridge(platform, callbacks)
        bridges.mattermost = bridge
        bridges._tasks.append(asyncio.create_task(_listen_mattermost(core, platform, bridge)))
    app.state.chat_bridges = bridges
    return bridges


async def _listen_mattermost(
    core: CoreServices, platform: MattermostPlatform, bridge: ChatBridge
) -> None:
    known: set[str] = set()

    def works_in(channel: str, root: str) -> bool:
        return f"mattermost_{channel}_{root}" in known

    async def on_message(message: Any) -> None:
        known.add(bridge.session_id_of(message))
        try:
            await bridge.handle(message)
        except Exception:
            logger.exception("A Mattermost message could not be answered")

    async def listen() -> None:
        await platform.listen(on_message, works_in)

    async def nothing() -> None:
        return None

    if core.cluster_dsn:
        await LeaderElection(core.cluster_dsn, "chat-mattermost").run(listen, nothing)
    else:
        await listen()

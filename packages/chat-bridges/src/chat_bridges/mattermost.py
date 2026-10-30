"""Mattermost: a bot account's WebSocket events in, REST replies out.

The bot answers direct messages, posts that mention it, and replies in a thread where it already
works (``works_in``). Configured by ``MATTERMOST_URL`` and ``MATTERMOST_BOT_TOKEN`` (a bot
account's access token); every request carries a timeout and the socket reconnects with backoff.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable, Coroutine
from typing import Any

import httpx
import websockets

from specifications.interfaces.chat import IncomingChatMessage

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT_SECONDS = 15.0
MAX_BACKOFF_SECONDS = 60.0
# Mattermost's default ``MaxPostSize`` is 16,383 characters; kept below it.
MAX_MESSAGE_CHARS = 15_000


class MattermostPlatform:
    name = "mattermost"
    max_message_chars = MAX_MESSAGE_CHARS

    def __init__(self, url: str, token: str, client: httpx.AsyncClient | None = None) -> None:
        self.url = url.rstrip("/")
        self._token = token
        self._client = client or httpx.AsyncClient(
            base_url=f"{self.url}/api/v4",
            timeout=REQUEST_TIMEOUT_SECONDS,
            headers={"Authorization": f"Bearer {token}"},
        )
        self.bot_user_id = ""
        self.bot_username = ""
        # Messages being answered: kept so a task is not collected before it ends.
        self._handling: set[asyncio.Task[None]] = set()

    async def identify(self) -> None:
        me = (await self._client.get("/users/me")).raise_for_status().json()
        self.bot_user_id, self.bot_username = str(me["id"]), str(me.get("username") or "")

    async def reply(self, conversation_id: str, thread_id: str, text: str) -> str:
        body = {"channel_id": conversation_id, "message": text, "root_id": thread_id}
        made = (await self._client.post("/posts", json=body)).raise_for_status().json()
        return str(made["id"])

    async def edit(self, conversation_id: str, message_id: str, text: str) -> None:
        (
            await self._client.put(f"/posts/{message_id}/patch", json={"message": text})
        ).raise_for_status()

    def message_of(
        self, event: dict[str, Any], works_in: Callable[[str, str], bool]
    ) -> IncomingChatMessage | None:
        """The message a ``posted`` event addresses to the bot, or None."""
        if event.get("event") != "posted":
            return None
        data = event.get("data") or {}
        try:
            post = json.loads(str(data.get("post") or "{}"))
        except ValueError:
            return None
        if not post or post.get("user_id") == self.bot_user_id or post.get("type"):
            return None
        text = str(post.get("message") or "")
        channel, root = str(post.get("channel_id") or ""), str(post.get("root_id") or "")
        direct = data.get("channel_type") == "D"
        mentioned = bool(self.bot_username) and f"@{self.bot_username}" in text
        if not (direct or mentioned or (root and works_in(channel, root))):
            return None
        return IncomingChatMessage(
            platform=self.name,
            conversation_id=channel,
            thread_id=root or str(post.get("id")),
            message_id=str(post.get("id")),
            user_id=str(post.get("user_id")),
            user_name=str(data.get("sender_name") or post.get("user_id")).lstrip("@"),
            text=text.replace(f"@{self.bot_username}", "").strip() if self.bot_username else text,
            tenant_id=str(data.get("team_id") or ""),
            direct=direct,
        )

    async def listen(
        self,
        on_message: Callable[[IncomingChatMessage], Coroutine[Any, Any, None]],
        works_in: Callable[[str, str], bool],
    ) -> None:
        """Follow the bot's WebSocket events forever, reconnecting after a failure."""
        backoff = 1.0
        socket_url = (
            self.url.replace("https://", "wss://").replace("http://", "ws://") + "/api/v4/websocket"
        )
        while True:
            try:
                if not self.bot_user_id:
                    await self.identify()
                async with websockets.connect(
                    socket_url,
                    additional_headers={"Authorization": f"Bearer {self._token}"},
                    open_timeout=REQUEST_TIMEOUT_SECONDS,
                ) as socket:
                    backoff = 1.0
                    async for raw in socket:
                        message = self.message_of(json.loads(raw), works_in)
                        if message is not None:
                            task: asyncio.Task[None] = asyncio.create_task(on_message(message))
                            self._handling.add(task)
                            task.add_done_callback(self._handling.discard)
            except asyncio.CancelledError:
                raise
            except Exception as err:
                logger.warning("Mattermost connection lost (%s); retrying in %.0fs", err, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, MAX_BACKOFF_SECONDS)

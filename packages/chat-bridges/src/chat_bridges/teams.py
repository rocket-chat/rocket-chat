"""Microsoft Teams: Bot Framework activities in (a signed JWT on each request), Bot Connector
replies out.

Every inbound request is checked before it is read: an RS256 token from the Bot Framework's
published keys, for this bot's app id, from the Bot Framework issuer. Replies go only to a
``serviceUrl`` on Microsoft's Bot Framework hosts, so a request cannot point the platform's
credentialed calls elsewhere. Configured by ``TEAMS_APP_ID`` and ``TEAMS_APP_PASSWORD``.
"""

from __future__ import annotations

import time
from typing import Any
from urllib.parse import urlparse

import httpx
import jwt

from specifications.interfaces.chat import IncomingChatMessage

REQUEST_TIMEOUT_SECONDS = 15.0
OPENID_CONFIG = "https://login.botframework.com/v1/.well-known/openidconfiguration"
ISSUER = "https://api.botframework.com"
TOKEN_URL = "https://login.microsoftonline.com/botframework.com/oauth2/v2.0/token"
TOKEN_SCOPE = "https://api.botframework.com/.default"
# Teams accepts about 28 KB per message; kept well below it.
MAX_MESSAGE_CHARS = 12_000
_SERVICE_HOSTS = (".botframework.com", ".trafficmanager.net", ".teams.microsoft.com")
KEYS_TTL_SECONDS = 24 * 3600


class TeamsAuthError(ValueError):
    """An inbound activity whose token is missing or not the Bot Framework's for this bot."""


def service_url_allowed(url: str) -> bool:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    return parsed.scheme == "https" and any(host.endswith(suffix) for suffix in _SERVICE_HOSTS)


class TeamsPlatform:
    name = "teams"
    max_message_chars = MAX_MESSAGE_CHARS

    def __init__(
        self, app_id: str, app_password: str, client: httpx.AsyncClient | None = None
    ) -> None:
        self.app_id = app_id
        self._password = app_password
        self._client = client or httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS)
        self._token: tuple[str, float] = ("", 0.0)
        self._keys: tuple[jwt.PyJWKSet | None, float] = (None, 0.0)
        # conversation id -> the service URL its activities came from (where replies go).
        self._service_urls: dict[str, str] = {}

    # -- inbound -------------------------------------------------------------------------------

    async def _key_set(self) -> jwt.PyJWKSet:
        keys, fetched = self._keys
        if keys is None or time.time() - fetched > KEYS_TTL_SECONDS:
            config = (await self._client.get(OPENID_CONFIG)).raise_for_status().json()
            document = (await self._client.get(str(config["jwks_uri"]))).raise_for_status().json()
            keys = jwt.PyJWKSet.from_dict(document)
            self._keys = (keys, time.time())
        return keys

    async def verify(self, authorization: str | None) -> dict[str, Any]:
        """The token's claims, or ``TeamsAuthError``."""
        if not authorization or not authorization.startswith("Bearer "):
            raise TeamsAuthError("Missing bearer token")
        token = authorization.removeprefix("Bearer ")
        try:
            kid = jwt.get_unverified_header(token).get("kid")
            key = next(k for k in (await self._key_set()).keys if k.key_id == kid)
            claims: dict[str, Any] = jwt.decode(
                token, key.key, algorithms=["RS256"], audience=self.app_id, issuer=ISSUER
            )
        except (StopIteration, jwt.PyJWTError) as err:
            raise TeamsAuthError(f"Invalid Bot Framework token: {err}") from err
        return claims

    def message_of(self, activity: dict[str, Any]) -> IncomingChatMessage | None:
        """The message an activity addresses to the bot (Teams only sends those), or None."""
        if activity.get("type") != "message" or not service_url_allowed(
            str(activity.get("serviceUrl") or "")
        ):
            return None
        conversation = activity.get("conversation") or {}
        conversation_id = str(conversation.get("id") or "")
        self._service_urls[conversation_id] = str(activity["serviceUrl"]).rstrip("/")
        sender = activity.get("from") or {}
        text = _without_mentions(str(activity.get("text") or ""), activity)
        return IncomingChatMessage(
            platform=self.name,
            conversation_id=conversation_id,
            thread_id=str(activity.get("replyToId") or activity.get("id") or ""),
            message_id=str(activity.get("id") or ""),
            user_id=str(sender.get("aadObjectId") or sender.get("id") or ""),
            user_name=str(sender.get("name") or "someone"),
            text=text,
            tenant_id=str(
                conversation.get("tenantId")
                or (activity.get("channelData") or {}).get("tenant", {}).get("id")
                or ""
            ),
            direct=conversation.get("conversationType") == "personal",
        )

    # -- outbound ------------------------------------------------------------------------------

    async def _bearer(self) -> str:
        token, expires = self._token
        if token and time.time() < expires - 60:
            return token
        response = await self._client.post(
            TOKEN_URL,
            data={
                "grant_type": "client_credentials",
                "client_id": self.app_id,
                "client_secret": self._password,
                "scope": TOKEN_SCOPE,
            },
        )
        body = response.raise_for_status().json()
        self._token = (
            str(body["access_token"]),
            time.time() + float(body.get("expires_in") or 3600),
        )
        return self._token[0]

    def _service(self, conversation_id: str) -> str:
        url = self._service_urls.get(conversation_id)
        if not url:
            raise TeamsAuthError(
                "No activity of this conversation was received; nothing to reply to"
            )
        return url

    async def reply(self, conversation_id: str, thread_id: str, text: str) -> str:
        url = f"{self._service(conversation_id)}/v3/conversations/{conversation_id}/activities/{thread_id}"
        body = {"type": "message", "text": text, "textFormat": "markdown", "replyToId": thread_id}
        made = await self._client.post(
            url, json=body, headers={"Authorization": f"Bearer {await self._bearer()}"}
        )
        return str(made.raise_for_status().json().get("id") or "")

    def permalink(self, message: IncomingChatMessage) -> str | None:
        # A Teams deep link needs the team and channel ids of a channel message, which a
        # personal chat lacks: the thread is opened from Teams itself.
        return None

    async def edit(self, conversation_id: str, message_id: str, text: str) -> None:
        url = f"{self._service(conversation_id)}/v3/conversations/{conversation_id}/activities/{message_id}"
        body = {"type": "message", "text": text, "textFormat": "markdown"}
        response = await self._client.put(
            url, json=body, headers={"Authorization": f"Bearer {await self._bearer()}"}
        )
        response.raise_for_status()


def _without_mentions(text: str, activity: dict[str, Any]) -> str:
    """The text without the ``<at>Bot</at>`` mention Teams puts in channel messages."""
    for entity in activity.get("entities") or []:
        if entity.get("type") == "mention":
            mentioned = str(entity.get("text") or "")
            if mentioned:
                text = text.replace(mentioned, "")
    return text.strip()

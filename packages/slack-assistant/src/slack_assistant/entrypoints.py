"""Ways into the assistant beyond mentions: its slash command, a message shortcut, the App Home."""

from __future__ import annotations

import logging
from typing import Any

from slack_bolt.context.ack.async_ack import AsyncAck
from slack_bolt.context.respond.async_respond import AsyncRespond
from slack_bolt.context.set_status.async_set_status import AsyncSetStatus

from slack_assistant.artifacts import head_of, upload_file
from slack_assistant.block_kit import (
    build_home_view,
    markdown_to_slack_mrkdwn,
    open_in_rocket_context,
)
from slack_assistant.bot import DEFAULT_COMMAND
from slack_assistant.gates import SlackGatesMixin
from specifications.interfaces.session import SessionRecord, SessionSource

logger = logging.getLogger(__name__)


def help_text(command: str) -> str:
    return (
        "*Rocket Chat in Slack*\n"
        f"• `{command} new <task>`: start a new thread working on a task\n"
        f"• `{command} status`: your recent and running threads\n"
        f"• `{command} stop`: stop what you have running in this channel\n"
        f"• `{command} pair`: link your Slack account to Rocket Chat\n"
        "• Mention me, or message me directly, to talk in a thread. *Ask Rocket* in a message's "
        "⋮ menu works on that message."
    )


SHORTCUT_CALLBACK_ID = "ask_rocket"
MAX_STATUS_ROWS = 8


def channel_type_of(channel_id: str) -> str:
    """Slack's channel kind from its id prefix (a slash command does not say)."""
    if channel_id.startswith("D"):
        return "im"
    if channel_id.startswith("G"):
        return "group"
    return "channel"


class SlackEntrypointsMixin(SlackGatesMixin):
    @property
    def command(self) -> str:
        """This bot's slash command (``/rocket`` for apps set up before bots had their own)."""
        return self.bot.command if self.bot is not None else DEFAULT_COMMAND

    def _register_entrypoints(self) -> None:
        async def handle_rocket(ack: AsyncAck, body: dict[str, Any], respond: AsyncRespond) -> None:
            await ack()
            await self._rocket_command(body, respond)

        # Slack only delivers the commands this app declares, so answering the legacy
        # ``/rocket`` too keeps apps installed from an older manifest working.
        for command in dict.fromkeys((self.command, DEFAULT_COMMAND)):
            self.app.command(command)(handle_rocket)

        @self.app.shortcut(SHORTCUT_CALLBACK_ID)
        async def handle_ask_rocket(ack: AsyncAck, shortcut: dict[str, Any]) -> None:
            await ack()
            await self._ask_about_message(shortcut)

        @self.app.event("app_home_opened")
        async def handle_home(event: dict[str, Any], context: dict[str, Any]) -> None:
            if event.get("tab", "home") == "home":
                await self.publish_home(str(event.get("user", "")), event.get("team"))

    # -- slash command -----------------------------------------------------------------------

    async def _rocket_command(self, body: dict[str, Any], respond: AsyncRespond) -> None:
        verb, _, rest = str(body.get("text", "")).strip().partition(" ")
        verb = verb.lower()
        if verb == "new":
            await self._command_new(body, rest.strip(), respond)
        elif verb == "status":
            await respond(text=await self._status_text(body), response_type="ephemeral")
        elif verb == "stop":
            await respond(text=await self._stop_text(body), response_type="ephemeral")
        elif verb == "pair":
            await respond(text=await self._pair_text(body), response_type="ephemeral")
        else:
            await respond(text=help_text(self.command), response_type="ephemeral")

    async def _command_new(self, body: dict[str, Any], task: str, respond: AsyncRespond) -> None:
        if not task:
            await respond(
                text=f"Tell me what to work on: `{self.command} new <task>`.",
                response_type="ephemeral",
            )
            return
        channel_id = str(body.get("channel_id", ""))
        user_id = str(body.get("user_id", ""))
        posted = await self.app.client.chat_postMessage(
            channel=channel_id, text=f"<@{user_id}> asked Rocket: {task}"
        )
        thread_ts = str(posted.get("ts", ""))
        await self._run_in_thread(
            channel_id=channel_id,
            thread_ts=thread_ts,
            prompt=task,
            user_id=user_id,
            team_id=str(body.get("team_id", "")) or None,
        )

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
        """Run a request that did not arrive as a thread message, replying in ``thread_ts``."""
        client = self.app.client

        async def say(**kwargs: Any) -> Any:
            return await client.chat_postMessage(channel=channel_id, **kwargs)

        await self.process_incoming_request(
            channel_id=channel_id,
            thread_ts=thread_ts,
            raw_text=prompt,
            say_fn=say,
            status_fn=AsyncSetStatus(client=client, channel_id=channel_id, thread_ts=thread_ts),
            user_id=user_id,
            channel_type=channel_type_of(channel_id),
            team_id=team_id,
            message_ts=message_ts or thread_ts,
        )

    async def _my_sessions(self, body: dict[str, Any]) -> list[SessionRecord]:
        if self.session_store is None:
            return []
        user_id = str(body.get("user_id", ""))
        org = await self._workspace_org(str(body.get("team_id", "")) or self.team_id or "")
        self._bind_tenant(org)
        records = await self.session_store.list_sessions(org, None, limit=200)
        mine = [
            r
            for r in records
            if r.source == SessionSource.SLACK
            and isinstance(r.metadata, dict)
            and r.metadata.get("slack_user_id") == user_id
        ]
        return mine[:MAX_STATUS_ROWS]

    async def _status_text(self, body: dict[str, Any]) -> str:
        sessions = await self._my_sessions(body)
        if not sessions:
            return f"You have no Slack threads with Rocket yet. Try `{self.command} new <task>`."
        base = self.settings.slack_web_base_url.rstrip("/")
        lines = []
        for record in sessions:
            state = "running" if record.session_id in self._active_turns else "idle"
            title = f"<{base}/chat/{record.session_id}|{record.title}>" if base else record.title
            lines.append(f"• {title} — _{state}_")
        return "*Your recent threads*\n" + "\n".join(lines)

    async def _stop_text(self, body: dict[str, Any]) -> str:
        channel_id = str(body.get("channel_id", ""))
        user_id = str(body.get("user_id", ""))
        stopped = 0
        for session_id in list(self._active_turns):
            if self._session_meta.get(session_id, ("", ""))[0] != channel_id:
                continue
            if await self._durable_requester(session_id) != user_id:
                continue
            await self.orchestrator.cancel_turn(session_id)
            stopped += 1
        if not stopped:
            return "You have nothing running in this channel."
        return f":stop_button: Stopped {stopped} running thread{'s' if stopped != 1 else ''}."

    async def _pair_text(self, body: dict[str, Any]) -> str:
        """Start linking the caller to a Rocket account. The link is confirmed with this
        one-time code from the member's signed-in session, so typing someone's e-mail address
        claims nothing; the reply is ephemeral, as the code is a secret."""
        if self._begin_pairing_fn is None:
            return "Account linking is not enabled for this workspace."
        code = await self._begin_pairing_fn(
            str(body.get("user_id", "")), str(body.get("team_id", "")) or self.team_id or ""
        )
        if code is None:
            return "Account linking is not enabled for this workspace."
        return (
            "🔗 *Link your Rocket Chat account*\n"
            "Open *Settings → Slack & Chat Ops* in Rocket Chat while signed in and enter the "
            f"code `{code}` (valid for 10 minutes, single use)."
        )

    # -- message shortcut --------------------------------------------------------------------

    async def _ask_about_message(self, shortcut: dict[str, Any]) -> None:
        message = shortcut.get("message") or {}
        channel_id = str((shortcut.get("channel") or {}).get("id", ""))
        thread_ts = str(message.get("thread_ts") or message.get("ts") or "")
        if not (channel_id and thread_ts):
            return
        quoted = str(message.get("text", "")).strip().replace("\n", "\n> ")
        prompt = (
            f"Please look at this Slack message from <@{message.get('user', 'unknown')}> and "
            f"help with whatever it asks or reports:\n> {quoted}"
        )
        await self._run_in_thread(
            channel_id=channel_id,
            thread_ts=thread_ts,
            prompt=prompt,
            user_id=str((shortcut.get("user") or {}).get("id", "")),
            team_id=str((shortcut.get("team") or {}).get("id", "")) or None,
            message_ts=str(message.get("ts") or "") or None,
        )

    # -- scheduled missions ------------------------------------------------------------------

    async def post_mission_report(
        self,
        *,
        org_id: str,
        channel_id: str,
        mission_name: str,
        session_id: str,
        answer: str,
        succeeded: bool,
    ) -> bool:
        """Post a finished mission run to the channel its owner chose; False if it could not."""
        if not self._slack_api_ready:
            return False
        policy = await self._load_org_policy(org_id)
        if policy.outbound_channels and channel_id not in policy.outbound_channels:
            logger.warning("Mission report to %s refused by the outbound allowlist", channel_id)
            return False
        verdict = ":white_check_mark: finished" if succeeded else ":warning: failed"
        body = markdown_to_slack_mrkdwn(head_of(answer.strip() or "(no output)", 1500))
        blocks: list[dict[str, Any]] = [
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*{mission_name}* {verdict}\n{body}"[:2900]},
            }
        ]
        if context := open_in_rocket_context(self._session_link(policy, session_id)):
            blocks.append(context)
        try:
            posted = await self.app.client.chat_postMessage(
                channel=channel_id, text=f"{mission_name} {verdict}", blocks=blocks
            )
            if len(answer) > policy.long_output_chars and policy.long_output_to_file:
                await upload_file(
                    self.app.client,
                    channel_id=channel_id,
                    thread_ts=str(posted.get("ts", "")),
                    filename="report.md",
                    content=answer,
                    title=f"{mission_name}: full report",
                )
        except Exception as err:
            logger.warning("Mission report to %s failed: %s", channel_id, err)
            return False
        return True

    # -- App Home ----------------------------------------------------------------------------

    async def publish_home(self, user_id: str, team_id: str | None) -> None:
        """Render the home tab for ``user_id``: link state, recent threads, how to start."""
        if not user_id:
            return
        body = {"user_id": user_id, "team_id": team_id or ""}
        linked_as: str | None = None
        if self.resolve_user_fn is not None:
            org = await self._workspace_org(team_id or self.team_id or "")
            resolved = await self.resolve_user_fn(user_id, None, org)
            if resolved:
                linked_as = str(resolved[0])
        base = self.settings.slack_web_base_url.rstrip("/")
        rows = []
        for record in await self._my_sessions(body):
            state = "running" if record.session_id in self._active_turns else "idle"
            link = f"{base}/chat/{record.session_id}" if base else None
            rows.append((record.title, state, link))
        try:
            await self.app.client.views_publish(
                user_id=user_id,
                view=build_home_view(
                    linked_as=linked_as, sessions=rows, web_url=base or None, command=self.command
                ),
            )
        except Exception as err:
            logger.warning("Could not publish the App Home for %s: %s", user_id, err)

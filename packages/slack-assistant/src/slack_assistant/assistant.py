"""Native Slack Assistant integration using 100% Socket Mode and Slack Bolt."""

import logging
import re
import time
from collections.abc import Callable
from typing import Any

from agent_core.tools.registry import ToolRegistry
from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler
from slack_bolt.async_app import AsyncApp, AsyncAssistant
from slack_bolt.context.ack.async_ack import AsyncAck
from slack_bolt.context.say.async_say import AsyncSay
from slack_bolt.context.set_status.async_set_status import AsyncSetStatus
from slack_bolt.context.set_suggested_prompts.async_set_suggested_prompts import (
    AsyncSetSuggestedPrompts,
)

from slack_assistant.block_kit import (
    build_approval_blocks,
    build_diff_blocks,
    build_question_blocks,
)
from slack_assistant.config import SlackAssistantSettings
from specifications.interfaces.agent import (
    AgentOrchestratorProtocol,
    FileDiff,
    InteractiveQuestion,
)
from specifications.interfaces.sandbox import SandboxDriverProtocol, WorkspaceSpec

logger = logging.getLogger(__name__)


def clean_mention_text(text: str) -> str:
    """Remove Slack user/bot mention markup from message content."""
    return re.sub(r"<@[A-Z0-9]+>", "", text).strip()


class SlackAssistant:
    """
    Manages Slack Assistant lifecycle, Socket Mode connection, Block Kit rendering,
    and bidirectional session mapping to Docker sandbox workspaces.
    """

    def __init__(
        self,
        orchestrator: AgentOrchestratorProtocol,
        settings: SlackAssistantSettings | None = None,
        sandbox_driver: SandboxDriverProtocol | None = None,
        tool_registry: ToolRegistry | None = None,
        app: AsyncApp | None = None,
        handler: AsyncSocketModeHandler | None = None,
    ) -> None:
        self.settings: SlackAssistantSettings = settings or SlackAssistantSettings()
        self.orchestrator: AgentOrchestratorProtocol = orchestrator
        self.sandbox_driver: SandboxDriverProtocol | None = sandbox_driver
        self.tool_registry: ToolRegistry | None = tool_registry
        self._last_activity: dict[str, float] = {}

        bot_token = self.settings.slack_bot_token or "xoxb-test-placeholder"
        self.app: AsyncApp = app or AsyncApp(token=bot_token)
        self.assistant: AsyncAssistant = AsyncAssistant()
        self.handler: AsyncSocketModeHandler | None = handler

        self._register_handlers()

    @staticmethod
    def get_session_id(channel_id: str, thread_ts: str) -> str:
        """Derive sanitized 1:1 session identifier compatible with Docker naming rules."""
        sanitized_ts = thread_ts.replace(".", "_")
        return f"slack_{channel_id}_{sanitized_ts}"

    def _register_handlers(self) -> None:
        """Register Slack Bolt Assistant listeners, mention events, and action gates."""

        @self.assistant.thread_started
        async def handle_thread_started(
            say: AsyncSay,
            set_suggested_prompts: AsyncSetSuggestedPrompts,
            event: dict[str, Any],
        ) -> None:
            thread_ts = event.get("thread_ts") or event.get("ts", "")
            await say(
                text=(
                    "Hello! I'm your Rocket Chat coding assistant. "
                    "I have access to your repositories and an isolated sandbox. "
                    "What are we building or fixing today?"
                ),
                thread_ts=thread_ts,
            )
            await set_suggested_prompts(
                prompts=[
                    {
                        "title": "Run test suite",
                        "message": "Run the project test suite and report any failures",
                    },
                    {
                        "title": "Fix open issue",
                        "message": "Investigate and resolve the latest open GitHub issue",
                    },
                    {
                        "title": "Explain architecture",
                        "message": "Give me a high-level overview of this codebase",
                    },
                ],
                thread_ts=thread_ts,
            )

        @self.assistant.user_message
        async def handle_user_message(
            say: AsyncSay,
            set_status: AsyncSetStatus,
            event: dict[str, Any],
        ) -> None:
            channel_id = event.get("channel", "")
            thread_ts = event.get("thread_ts") or event.get("ts", "")
            text = event.get("text", "")
            await self.process_incoming_request(
                channel_id=channel_id,
                thread_ts=thread_ts,
                raw_text=text,
                say_fn=say,
                status_fn=set_status,
            )

        @self.app.event("app_mention")
        async def handle_app_mention(
            say: AsyncSay,
            event: dict[str, Any],
            context: dict[str, Any],
        ) -> None:
            channel_id = event.get("channel", "")
            thread_ts = event.get("thread_ts") or event.get("ts", "")
            text = event.get("text", "")
            status_fn = context.get("set_status")
            await self.process_incoming_request(
                channel_id=channel_id,
                thread_ts=thread_ts,
                raw_text=text,
                say_fn=say,
                status_fn=status_fn,
            )

        @self.app.action("approve_action")
        async def handle_approve_action(
            ack: AsyncAck,
            body: dict[str, Any],
            say: AsyncSay,
        ) -> None:
            await ack()
            actions = body.get("actions", [])
            action_id = actions[0].get("value", "") if actions else ""
            channel_id = body.get("channel", {}).get("id", "")
            container = body.get("container", {})
            thread_ts = container.get("thread_ts", "")
            session_id = self.get_session_id(channel_id, thread_ts) if thread_ts else ""

            if session_id and action_id:
                await self.orchestrator.submit_human_approval(
                    session_id=session_id,
                    action_id=action_id,
                    approved=True,
                )
            await say(
                text=f"✅ *Approved & Pushed* (Action: `{action_id}`)",
                thread_ts=thread_ts,
            )

        @self.app.action("reject_action")
        async def handle_reject_action(
            ack: AsyncAck,
            body: dict[str, Any],
            say: AsyncSay,
        ) -> None:
            await ack()
            actions = body.get("actions", [])
            action_id = actions[0].get("value", "") if actions else ""
            channel_id = body.get("channel", {}).get("id", "")
            container = body.get("container", {})
            thread_ts = container.get("thread_ts", "")
            session_id = self.get_session_id(channel_id, thread_ts) if thread_ts else ""

            if session_id and action_id:
                await self.orchestrator.submit_human_approval(
                    session_id=session_id,
                    action_id=action_id,
                    approved=False,
                )
            await say(
                text=f"❌ *Rejected* (Action: `{action_id}`)",
                thread_ts=thread_ts,
            )

        @self.app.action("question_option_select")
        async def handle_question_option(
            ack: AsyncAck,
            body: dict[str, Any],
            say: AsyncSay,
        ) -> None:
            await ack()
            actions = body.get("actions", [])
            val = actions[0].get("value", "") if actions else ""
            channel_id = body.get("channel", {}).get("id", "")
            container = body.get("container", {})
            thread_ts = container.get("thread_ts", "")
            session_id = self.get_session_id(channel_id, thread_ts) if thread_ts else ""

            if session_id and ":" in val:
                question_id, option = val.split(":", 1)
                await self.orchestrator.submit_question_answer(
                    session_id=session_id,
                    question_id=question_id,
                    selected_options=[option],
                )
                await say(text=f"Selected: *{option}*", thread_ts=thread_ts)

        self.app.assistant(self.assistant)

    def _determine_spinner_message(self, tool_name: str, arguments: dict[str, Any]) -> str:
        """Map active tool invocation to high-clarity Slack spinner label."""
        if tool_name == "bash_exec":
            command = arguments.get("command", "")
            if "pytest" in command or "test" in command:
                return "Running test suite in sandbox..."
            if command.startswith("git"):
                return "Running git operation..."
            truncated_cmd = command[:35] + ("..." if len(command) > 35 else "")
            return f"Executing command: `{truncated_cmd}`"
        if tool_name in {"file_edit", "apply_patch"}:
            path = arguments.get("path", "file")
            return f"Applying edits to `{path}`..."
        if tool_name == "file_write":
            path = arguments.get("path", "file")
            return f"Writing `{path}` in sandbox..."
        if tool_name == "file_read":
            path = arguments.get("path", "file")
            return f"Reading `{path}`..."
        if tool_name == "web_search":
            return "Searching the web..."
        if tool_name == "web_fetch":
            return "Fetching web documentation..."
        return f"Running tool `{tool_name}`..."

    async def _safely_update_status(
        self,
        status_fn: Callable[..., Any] | None,
        status_msg: str,
    ) -> None:
        if not status_fn:
            return
        try:
            res = status_fn(status=status_msg)
            if hasattr(res, "__await__"):
                await res
        except Exception as err:
            logger.debug("Slack setStatus call skipped or unsupported in context: %s", err)

    async def process_incoming_request(
        self,
        channel_id: str,
        thread_ts: str,
        raw_text: str,
        say_fn: Callable[..., Any],
        status_fn: Callable[..., Any] | None = None,
    ) -> None:
        """
        Execute full ReAct turn, bridge real-time spinners, format Block Kit diffs,
        and handle decision gates.
        """
        prompt = clean_mention_text(raw_text)
        session_id = self.get_session_id(channel_id, thread_ts)
        self._last_activity[session_id] = time.time()

        if self.sandbox_driver:
            await self.sandbox_driver.ensure_workspace(
                WorkspaceSpec(
                    session_id=session_id,
                    tenant_org_id="slack",
                    tenant_user_id=channel_id,
                    container_image="python:3.12-slim",
                )
            )
            if self.tool_registry:
                self.tool_registry.bind_sandbox(self.sandbox_driver, session_id)

        await self._safely_update_status(status_fn, "Analyzing request...")

        diffs: list[FileDiff] = []
        assistant_text_chunks: list[str] = []

        try:
            async for event in self.orchestrator.process_user_turn(session_id, prompt):
                self._last_activity[session_id] = time.time()

                if event.event_type == "tool_call":
                    raw_args = event.payload.get("arguments", "{}")
                    args = raw_args if isinstance(raw_args, dict) else {}
                    if isinstance(raw_args, str) and raw_args:
                        try:
                            import json

                            args = json.loads(raw_args)
                        except Exception:
                            args = {}
                    spinner_msg = self._determine_spinner_message(
                        event.payload.get("name", ""),
                        args,
                    )
                    await self._safely_update_status(status_fn, spinner_msg)

                elif event.event_type == "thought":
                    if not event.payload.get("is_reasoning", False):
                        delta = event.payload.get("delta", "")
                        assistant_text_chunks.append(delta)

                elif event.event_type == "file_diff":
                    diff_payload = event.payload
                    diffs.append(
                        FileDiff(
                            path=diff_payload["path"],
                            diff_content=diff_payload["diff_content"],
                            additions=diff_payload.get("additions", 0),
                            deletions=diff_payload.get("deletions", 0),
                            is_new_file=diff_payload.get("is_new_file", False),
                        )
                    )

                elif event.event_type == "interactive_question":
                    q_payload = event.payload
                    question = InteractiveQuestion(
                        question_id=q_payload["question_id"],
                        question_text=q_payload["question_text"],
                        options=q_payload.get("options", []),
                        is_multi_select=q_payload.get("is_multi_select", False),
                        default_recommended_option=q_payload.get("default_recommended_option"),
                    )
                    q_blocks = build_question_blocks(question)
                    await say_fn(
                        blocks=q_blocks,
                        text=question.question_text,
                        thread_ts=thread_ts,
                    )

        finally:
            await self._safely_update_status(status_fn, "")

        final_text = "".join(assistant_text_chunks).strip()
        if diffs:
            summary = final_text or "I have prepared and verified the requested code changes:"
            blocks: list[dict[str, Any]] = [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": summary,
                    },
                }
            ]
            blocks.extend(build_diff_blocks(diffs))
            blocks.extend(
                build_approval_blocks(
                    action_id=f"commit_{session_id}_{int(time.time())}",
                    title="Review & Commit Changes",
                    description="Approve to persist changes and commit to repository.",
                )
            )
            await say_fn(blocks=blocks, text=summary, thread_ts=thread_ts)
        elif final_text:
            await say_fn(text=final_text, thread_ts=thread_ts)

    async def hibernate_inactive_sessions(self, timeout_seconds: int | None = None) -> list[str]:
        """Stop ephemeral containers for idle threads while retaining persistent volumes."""
        timeout = (
            timeout_seconds if timeout_seconds is not None else self.settings.auto_hibernate_seconds
        )
        now = time.time()
        hibernated: list[str] = []

        if not self.sandbox_driver:
            return hibernated

        for session_id, last_time in list(self._last_activity.items()):
            if now - last_time >= timeout:
                try:
                    await self.sandbox_driver.hibernate_sandbox(session_id)
                    hibernated.append(session_id)
                except Exception as err:
                    logger.warning("Failed to hibernate sandbox %s: %s", session_id, err)

        return hibernated

    async def start_socket_mode(self) -> None:
        """Establish persistent outbound WebSocket connection with Slack."""
        if not self.settings.slack_app_token:
            raise ValueError("SLACK_APP_TOKEN is required for Socket Mode")
        if not self.handler:
            self.handler = AsyncSocketModeHandler(
                app=self.app,
                app_token=self.settings.slack_app_token,
            )
        # slack-bolt AsyncSocketModeHandler.start_async method lacks type hints
        await self.handler.start_async()  # type: ignore[no-untyped-call]

    async def stop_socket_mode(self) -> None:
        """Cleanly close Socket Mode connection."""
        if self.handler:
            # slack-bolt AsyncSocketModeHandler.close_async method lacks type hints
            await self.handler.close_async()  # type: ignore[no-untyped-call]

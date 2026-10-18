"""Running a shared turn and rendering its live events into Slack."""

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, ClassVar

from slack_assistant.artifacts import (
    combined_diff,
    diff_is_large,
    head_of,
    screenshot_paths,
    upload_file,
)
from slack_assistant.block_kit import (
    build_approval_blocks,
    build_diff_blocks,
    build_question_blocks,
    chunk_slack_message,
    markdown_to_slack_mrkdwn,
    open_in_rocket_context,
)
from slack_assistant.policy import SlackOrgPolicy
from slack_assistant.state import SlackAssistantState
from slack_assistant.stream import ANSWER_CHARS, TurnStream
from specifications.interfaces.agent import (
    FileDiff,
    InteractiveQuestion,
    TurnOverlay,
)
from specifications.interfaces.events import EventType, SessionEvent

logger = logging.getLogger(__name__)


@dataclass
class TurnOutcome:
    """What a shared turn produced, as seen by Slack."""

    chunks: list[str] = field(default_factory=list)
    diffs: list[FileDiff] = field(default_factory=list)
    error: str | None = None

    @property
    def text(self) -> str:
        return "".join(self.chunks).strip()


@dataclass
class TurnView:
    """Everything the renderer needs to know about one running Slack turn."""

    session_id: str
    channel_id: str
    thread_ts: str
    requester_slack_id: str | None
    say_fn: Callable[..., Any]
    status_fn: Callable[..., Any] | None
    outcome: TurnOutcome
    stream: TurnStream | None = None
    link_url: str | None = None
    screenshots: list[str] = field(default_factory=list)
    # Private response mode: every message goes to the requester alone (ephemeral).
    private: bool = False


def readable_tool(name: str) -> str:
    """A tool's name as a step: ``mcp__signoz__signoz_search_logs`` -> "Signoz search logs
    (signoz)". MCP tool names repeat their server; the server stays visible, shortly."""
    server = ""
    if name.startswith("mcp__") and "__" in name[5:]:
        server, name = name[5:].split("__", 1)
    words = name.replace("_", " ").replace("-", " ").strip() or "tool"
    label = words[0].upper() + words[1:]
    return f"{label} ({server})" if server else label


class SlackTurnStreamMixin(SlackAssistantState):
    """Running a shared turn and rendering its live events into Slack."""

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
        if tool_name == "update_status":
            return arguments.get("message") or "Updating status..."
        if tool_name == "web_search":
            return "Searching the web..."
        if tool_name == "web_fetch":
            return "Fetching web documentation..."
        return f"{readable_tool(tool_name)}..."

    async def _safely_update_status(
        self,
        status_fn: Callable[..., Any] | None,
        status_msg: str,
    ) -> None:
        if not status_fn:
            return
        if not status_msg:
            try:
                logger.info("Clearing Slack status loader")
                res = status_fn(status="")
                if hasattr(res, "__await__"):
                    await res
            except Exception as err:
                logger.debug("Slack clear status skipped: %s", err)
            return

        formatted_status = status_msg
        # Slack rotates through loading_messages for the shimmering loader indicator in the UI.
        # Without loading_messages, Slack defaults to displaying '<App Name> is working...'.
        loading_messages = [status_msg]

        try:
            logger.info(
                "Updating Slack status loader: '%s' (loading_messages=%s)",
                formatted_status,
                loading_messages,
            )
            try:
                res = status_fn(status=formatted_status, loading_messages=loading_messages)
            except TypeError:
                # Fallback if caller / mock callable only accepts status
                res = status_fn(status=formatted_status)
            if hasattr(res, "__await__"):
                await res
        except Exception as err:
            logger.warning("Slack setStatus call skipped or unsupported in context: %s", err)

    async def _run_shared_turn(
        self, view: TurnView, *, prompt: str, overlay: TurnOverlay, author_user_id: str
    ) -> TurnOutcome:
        """Start the shared turn and render its live events until it reaches a final state."""
        if self._run_turn_fn is None or self.event_bus is None:
            raise RuntimeError("Slack is not wired to the shared turn pipeline")
        queue, close = self.event_bus.open_queue(view.session_id)
        run_turn = self._run_turn_fn
        session_id = view.session_id

        async def _run() -> None:
            await run_turn(
                session_id=session_id,
                prompt=prompt,
                overlay=overlay,
                author_user_id=author_user_id,
            )

        runner: asyncio.Task[None] = asyncio.create_task(_run())
        runner.add_done_callback(self._log_runner_failure)
        self._background.add(runner)
        runner.add_done_callback(self._background.discard)
        try:
            while not await self._drain_one(queue, runner, view):
                pass
        finally:
            close()
        failure = runner.exception() if runner.done() and not runner.cancelled() else None
        if failure is not None:
            raise failure
        return view.outcome

    @staticmethod
    def _log_runner_failure(task: asyncio.Task[Any]) -> None:
        if not task.cancelled() and task.exception() is not None:
            logger.error("Shared turn task failed: %s", task.exception(), exc_info=task.exception())

    async def _drain_one(
        self, queue: asyncio.Queue[SessionEvent], runner: asyncio.Task[Any], view: TurnView
    ) -> bool:
        """Handle the next event; return True once the turn is over."""
        getter = asyncio.ensure_future(queue.get())
        done, _ = await asyncio.wait({getter, runner}, return_when=asyncio.FIRST_COMPLETED)
        if getter in done:
            event = getter.result()
            await self._render_event(event, view)
            return self._is_terminal(event)
        getter.cancel()
        while not queue.empty():
            await self._render_event(queue.get_nowait(), view)
        return True

    @staticmethod
    def _is_terminal(event: SessionEvent) -> bool:
        return event.event_type == EventType.STATUS_CHANGED and event.payload.get("status") in (
            "completed",
            "error",
            "cancelled",
        )

    async def _render_event(self, event: SessionEvent, view: TurnView) -> None:
        self._last_activity[view.session_id] = time.time()
        handler = self._EVENT_RENDERERS.get(event.event_type)
        if handler is not None:
            await handler(self, event.payload, view)

    async def _render_tool_started(self, payload: dict[str, Any], view: TurnView) -> None:
        name = str(payload.get("name", ""))
        arguments = payload.get("arguments")
        arguments = arguments if isinstance(arguments, dict) else {}
        label = self._determine_spinner_message(name, arguments)
        await self._safely_update_status(view.status_fn, label)
        if view.stream is not None:
            await view.stream.tool_started(name, arguments, label)

    async def _render_tool_completed(self, payload: dict[str, Any], view: TurnView) -> None:
        result = payload.get("result")
        if isinstance(result, str):
            for path in screenshot_paths(result):
                if path not in view.screenshots:
                    view.screenshots.append(path)

    async def _render_status(self, payload: dict[str, Any], view: TurnView) -> None:
        message = payload.get("message")
        if payload.get("status") == "error":
            view.outcome.error = view.outcome.error or str(message or "The turn failed")
        elif message and payload.get("status") != "completed":
            await self._safely_update_status(view.status_fn, str(message))

    async def _render_token(self, payload: dict[str, Any], view: TurnView) -> None:
        view.outcome.chunks.append(str(payload.get("delta", "")))

    async def _render_error(self, payload: dict[str, Any], view: TurnView) -> None:
        view.outcome.error = str(payload.get("error") or "The turn failed")

    async def _render_file_diff(self, payload: dict[str, Any], view: TurnView) -> None:
        view.outcome.diffs.append(
            FileDiff(
                path=payload["path"],
                diff_content=payload["diff_content"],
                additions=payload.get("additions", 0),
                deletions=payload.get("deletions", 0),
                is_new_file=payload.get("is_new_file", False),
            )
        )

    async def _render_question(self, payload: dict[str, Any], view: TurnView) -> None:
        question = InteractiveQuestion(
            question_id=payload["question_id"],
            question_text=payload["question_text"],
            options=payload.get("options", []),
            is_multi_select=payload.get("is_multi_select", False),
            default_recommended_option=payload.get("default_recommended_option"),
        )
        self._gate_owners[question.question_id] = (view.session_id, view.requester_slack_id)
        await view.say_fn(
            blocks=build_question_blocks(question),
            text=question.question_text,
            thread_ts=view.thread_ts,
        )

    async def _render_approval(self, payload: dict[str, Any], view: TurnView) -> None:
        if payload.get("policy_decision", "require_human") != "require_human":
            return
        action_id = str(payload.get("action_id", ""))
        self._gate_owners[action_id] = (view.session_id, view.requester_slack_id)
        action_type = payload.get("action_type", "action")
        await view.say_fn(
            blocks=build_approval_blocks(
                action_id=action_id,
                title=f"Approval Required — {action_type} ({payload.get('risk_level', 'medium')} risk)",
                description=payload.get("command_or_path", ""),
            ),
            text=f"Approval required for {action_type}",
            thread_ts=view.thread_ts,
        )

    async def _render_artifact_created(self, payload: dict[str, Any], view: TurnView) -> None:
        title = payload.get("title", "Deliverable")
        filename = payload.get("filename", "artifact.md")
        artifact_type = payload.get("artifact_type", "document")
        text = f"📄 *Artifact Created:* `{filename}` — *{title}* ({artifact_type})\nViewable in the Cockpit Artifacts drawer."
        await view.say_fn(text=text, thread_ts=view.thread_ts)

    async def _render_artifact_updated(self, payload: dict[str, Any], view: TurnView) -> None:
        title = payload.get("title", "Deliverable")
        filename = payload.get("filename", "artifact.md")
        version = payload.get("version", "")
        version_str = f" (v{version})" if version else ""
        text = f"📝 *Artifact Updated:* `{filename}` — *{title}*{version_str}\nUpdated deliverable viewable in the Cockpit Artifacts drawer."
        await view.say_fn(text=text, thread_ts=view.thread_ts)

    _EVENT_RENDERERS: ClassVar[dict[EventType, Callable[..., Awaitable[None]]]] = {
        EventType.TOOL_STARTED: _render_tool_started,
        EventType.TOOL_COMPLETED: _render_tool_completed,
        EventType.STATUS_CHANGED: _render_status,
        EventType.TOKEN: _render_token,
        EventType.ERROR: _render_error,
        EventType.FILE_DIFF: _render_file_diff,
        EventType.DECISION_REQUIRED: _render_question,
        EventType.APPROVAL_REQUIRED: _render_approval,
        EventType.ARTIFACT_CREATED: _render_artifact_created,
        EventType.ARTIFACT_UPDATED: _render_artifact_updated,
    }

    async def _post_result(self, view: TurnView, org_policy: SlackOrgPolicy) -> None:
        """Post the answer, diffs and screenshots; long content goes up as files."""
        outcome = view.outcome
        final_text = outcome.text or ("" if outcome.diffs else "I've processed your request.")
        use_files = org_policy.long_output_to_file and self._slack_api_ready
        detail = view.stream.outcome_line() if view.stream is not None else None
        link = open_in_rocket_context(view.link_url, detail)
        stream = view.stream
        if stream is not None and stream.started:
            if await self._finish_stream(view, stream, final_text, link, use_files, org_policy):
                return

        if outcome.diffs:
            await self._post_changes(view, final_text, use_files and diff_is_large(outcome.diffs))
        elif final_text and use_files and len(final_text) > org_policy.long_output_chars:
            await self._post_long_answer(view, final_text, org_policy.long_output_chars)
        elif final_text:
            *head, last = chunk_slack_message(markdown_to_slack_mrkdwn(final_text))
            for chunk in head:
                await view.say_fn(text=chunk, thread_ts=view.thread_ts)
            # The link and what the turn did ride under the answer, not in a message of their own.
            await view.say_fn(text=last, blocks=_with_footer(last, link), thread_ts=view.thread_ts)
            link = None
        if use_files:
            await self._upload_screenshots(view)
        if link is not None:
            await view.say_fn(blocks=[link], text="Open in Rocket Chat", thread_ts=view.thread_ts)

    async def _finish_stream(
        self,
        view: TurnView,
        stream: TurnStream,
        text: str,
        link: dict[str, Any] | None,
        use_files: bool,
        org_policy: SlackOrgPolicy,
    ) -> bool:
        """Write the answer into the turn's streamed reply; then what does not fit a message
        (the full long answer, the diffs, screenshots) follows in the thread."""
        too_long = len(text) > (org_policy.long_output_chars if use_files else ANSWER_CHARS)
        shown = head_of(text, min(org_policy.long_output_chars, 1500)) if too_long else text
        if not await stream.finish(shown, [link] if link else []):
            return False
        if too_long:
            await self._post_overflow(view, text, use_files)
        if view.outcome.diffs:
            await self._post_diffs(view, use_files and diff_is_large(view.outcome.diffs))
        if use_files:
            await self._upload_screenshots(view)
        return True

    async def _post_overflow(self, view: TurnView, text: str, as_file: bool) -> None:
        if as_file:
            await upload_file(
                self.app.client,
                channel_id=view.channel_id,
                thread_ts=view.thread_ts,
                filename="answer.md",
                content=text,
                title="Full answer",
                comment="The full answer, as it is too long for a message.",
            )
            return
        for chunk in chunk_slack_message(markdown_to_slack_mrkdwn(text)):
            await view.say_fn(text=chunk, thread_ts=view.thread_ts)

    async def _post_diffs(self, view: TurnView, as_file: bool) -> None:
        diffs = view.outcome.diffs
        if as_file:
            await upload_file(
                self.app.client,
                channel_id=view.channel_id,
                thread_ts=view.thread_ts,
                filename="changes.diff",
                content=combined_diff(diffs),
                title=f"Changes in {len(diffs)} file(s)",
            )
            return
        await view.say_fn(blocks=build_diff_blocks(diffs), text="Changes", thread_ts=view.thread_ts)

    async def _post_changes(self, view: TurnView, summary: str, as_file: bool) -> None:
        diffs = view.outcome.diffs
        formatted = markdown_to_slack_mrkdwn(
            summary or "I have prepared and verified the requested code changes:"
        )
        blocks: list[dict[str, Any]] = [
            {"type": "section", "text": {"type": "mrkdwn", "text": formatted}}
        ]
        if as_file:
            listing = "\n".join(f"• `{d.path}` (+{d.additions} -{d.deletions})" for d in diffs[:15])
            blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": listing}})
            await view.say_fn(blocks=blocks, text=formatted, thread_ts=view.thread_ts)
            await upload_file(
                self.app.client,
                channel_id=view.channel_id,
                thread_ts=view.thread_ts,
                filename="changes.diff",
                content=combined_diff(diffs),
                title=f"Changes in {len(diffs)} file(s)",
            )
            return
        blocks.extend(build_diff_blocks(diffs))
        await view.say_fn(blocks=blocks, text=formatted, thread_ts=view.thread_ts)

    async def _post_long_answer(self, view: TurnView, text: str, limit: int) -> None:
        head = markdown_to_slack_mrkdwn(head_of(text, min(limit, 1500)))
        await view.say_fn(text=head, thread_ts=view.thread_ts)
        await upload_file(
            self.app.client,
            channel_id=view.channel_id,
            thread_ts=view.thread_ts,
            filename="answer.md",
            content=text,
            title="Full answer",
            comment="The full answer, as it is too long for a message.",
        )

    async def _upload_screenshots(self, view: TurnView) -> None:
        if self._read_file_bytes_fn is None:
            return
        for path in view.screenshots[:3]:
            try:
                data = await self._read_file_bytes_fn(view.session_id, path)
            except Exception as err:
                logger.debug("Screenshot %s could not be read: %s", path, err)
                continue
            await upload_file(
                self.app.client,
                channel_id=view.channel_id,
                thread_ts=view.thread_ts,
                filename=path.rsplit("/", 1)[-1],
                content=data,
                title="Screenshot",
            )


def _with_footer(text: str, footer: dict[str, Any] | None) -> list[dict[str, Any]]:
    section = {"type": "section", "text": {"type": "mrkdwn", "text": text}}
    return [section, footer] if footer is not None else [section]

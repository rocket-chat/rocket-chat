"""Running a shared turn and rendering its live events into Slack."""

import logging
import re
import time
import uuid
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
)
from slack_assistant.cards import (
    TurnSummary,
    build_summary_card,
    published_branch_of,
    pull_request_of,
    wait_notice,
)
from slack_assistant.policy import SlackOrgPolicy
from slack_assistant.refusals import access_denied_text, budget_blocked_text
from slack_assistant.state import SlackAssistantState
from slack_assistant.stream import ANSWER_CHARS, TurnStream
from specifications.interfaces.agent import (
    FileDiff,
    InteractiveQuestion,
)
from specifications.interfaces.events import EventType, SessionEvent

logger = logging.getLogger(__name__)

# Artifacts up to this size are uploaded to the thread; larger ones are linked.
ARTIFACT_UPLOAD_CHARS = 200_000


@dataclass
class TurnOutcome:
    """What a shared turn produced, as seen by Slack."""

    # The streamed text since the last tool call: the model's narration before a tool call is
    # not part of its answer.
    chunks: list[str] = field(default_factory=list)
    diffs: list[FileDiff] = field(default_factory=list)
    error: str | None = None
    # The platform refused the turn (a spend limit, an agent the person may not use): what to
    # tell them instead of an error.
    refusal: str | None = None
    # The answer as the runner stored it, read once the turn's final status arrives. The
    # stream can drop events under load, so the stored answer wins when there is one.
    stored_answer: str | None = None
    # Stopped from elsewhere (the web app); a stop from Slack is reported by the assistant.
    cancelled: bool = False
    # How the runner closed the turn ("completed" or "waiting") and what its model calls cost.
    final_status: str = ""
    cost_usd: float = 0.0
    # Pull requests the turn opened (owner/name, number, url), and the branch it last pushed.
    pull_requests: list[tuple[str, int, str]] = field(default_factory=list)
    branch: str = ""
    # The wait the turn ended on (type, target).
    wait: tuple[str, str] | None = None

    @property
    def text(self) -> str:
        if self.stored_answer is not None:
            return self.stored_answer.strip()
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
    org_id: str = ""
    # The id this view's turn records its user message under: events before that message
    # belong to a turn already running in the session, which this view must not render.
    message_id: str = field(default_factory=lambda: f"slack-{uuid.uuid4().hex}")
    own_turn_started: bool = False
    # The request as its author wrote it and its message, for Retry and for edits and deletions.
    request_text: str = ""
    request_ts: str = ""


def readable_tool(name: str) -> str:
    """A tool's name as a step: ``mcp__signoz__signoz_search_logs`` -> "Signoz search logs
    (signoz)". MCP tool names repeat their server; the server stays visible, shortly."""
    server = ""
    if name.startswith("mcp__") and "__" in name[5:]:
        server, name = name[5:].split("__", 1)
    words = name.replace("_", " ").replace("-", " ").strip() or "tool"
    label = words[0].upper() + words[1:]
    return f"{label} ({server})" if server else label


SUBAGENT_SUMMARY_CHARS = 120


def _role_label(payload: dict[str, Any]) -> str:
    return (
        str(payload.get("name") or payload.get("role") or "Subagent").replace("_", " ").capitalize()
    )


def subagent_started_line(payload: dict[str, Any]) -> str:
    """ "Reviewer: check the diff for missing tests"."""
    task = " ".join(str(payload.get("task") or "").split())
    return f"{_role_label(payload)}: {task}" if task else _role_label(payload)


def subagent_completed_line(payload: dict[str, Any]) -> str:
    """ "Reviewer done: 3 findings, one high · $0.04" (the summary's first line, clamped)."""
    verb = {"completed": "done", "partial": "stopped early", "cancelled": "cancelled"}.get(
        str(payload.get("status") or "completed"), "failed"
    )
    report = payload.get("report")
    summary = str((report if isinstance(report, dict) else {}).get("summary") or "")
    summary = summary or str(payload.get("summary") or "")
    lines = summary.strip().splitlines()
    first = " ".join(lines[0].split()) if lines else ""
    if len(first) > SUBAGENT_SUMMARY_CHARS:
        first = first[: SUBAGENT_SUMMARY_CHARS - 1] + "…"
    line = f"{_role_label(payload)} {verb}" + (f": {first}" if first else "")
    cost = payload.get("cost")
    return f"{line} · ${float(cost):.2f}" if isinstance(cost, int | float) and cost > 0 else line


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

    async def _own_turn_started(self, view: TurnView) -> None:
        """Hook for when this view's turn begins (the assistant records its requester)."""
        return None

    @staticmethod
    def _is_terminal(event: SessionEvent) -> bool:
        """The runner's closing event, published once the answer is stored."""
        return event.event_type == EventType.STATUS_CHANGED and bool(event.payload.get("final"))

    async def _read_stored_answer(self, view: TurnView) -> None:
        """The turn's answer as stored: its last assistant message without tool calls."""
        if self.session_store is None:
            return
        try:
            record = await self.session_store.get_session(view.session_id, view.org_id or None)
        except Exception as err:
            logger.warning("Could not read the answer of %s: %s", view.session_id, err)
            return
        if record is None:
            return
        for message in reversed(record.conversation_history):
            if message.role == "user":
                return
            if message.role == "assistant" and message.content and not message.tool_calls:
                view.outcome.stored_answer = message.content
                return

    async def _render_event(self, event: SessionEvent, view: TurnView) -> None:
        self._last_activity[view.session_id] = time.time()
        handler = self._EVENT_RENDERERS.get(event.event_type)
        if handler is not None:
            await handler(self, event.payload, view)

    async def _render_tool_started(self, payload: dict[str, Any], view: TurnView) -> None:
        if payload.get("subagent_id"):
            return  # a subagent's own step: its line says what it is doing
        view.outcome.chunks.clear()
        name = str(payload.get("name", ""))
        arguments = payload.get("arguments")
        arguments = arguments if isinstance(arguments, dict) else {}
        label = self._determine_spinner_message(name, arguments)
        await self._safely_update_status(view.status_fn, label)
        if view.stream is not None:
            await view.stream.tool_started(name, arguments, label)

    async def _render_tool_completed(self, payload: dict[str, Any], view: TurnView) -> None:
        result = payload.get("result")
        if not isinstance(result, str):
            return
        for path in screenshot_paths(result):
            if path not in view.screenshots:
                view.screenshots.append(path)
        if payload.get("subagent_id"):
            return
        name = str(payload.get("name") or "")
        if name == "git_open_pr" and (pull := pull_request_of(result)):
            if pull not in view.outcome.pull_requests:
                view.outcome.pull_requests.append(pull)
        elif name == "git_push" and (branch := published_branch_of(result)):
            view.outcome.branch = branch

    async def _render_plan(self, payload: dict[str, Any], view: TurnView) -> None:
        tasks = payload.get("tasks")
        if view.stream is not None and isinstance(tasks, list):
            await view.stream.plan(tasks)

    async def _render_wait_started(self, payload: dict[str, Any], view: TurnView) -> None:
        view.outcome.wait = (str(payload.get("wait_type") or ""), str(payload.get("target") or ""))

    async def _render_subagent_started(self, payload: dict[str, Any], view: TurnView) -> None:
        if view.stream is not None:
            await view.stream.subagent(
                str(payload.get("subagent_id") or ""), subagent_started_line(payload), "in_progress"
            )

    async def _render_subagent_completed(self, payload: dict[str, Any], view: TurnView) -> None:
        if view.stream is not None:
            status = str(payload.get("status") or "completed")
            await view.stream.subagent(
                str(payload.get("subagent_id") or ""),
                subagent_completed_line(payload),
                "complete" if status in ("completed", "partial") else "error",
            )

    async def _render_status(self, payload: dict[str, Any], view: TurnView) -> None:
        message = payload.get("message")
        status = payload.get("status")
        if payload.get("final"):
            view.outcome.final_status = str(status or "")
            cost = payload.get("cost_usd")
            if isinstance(cost, int | float):
                view.outcome.cost_usd = float(cost)
        if status == "error":
            view.outcome.error = view.outcome.error or str(message or "The turn failed")
        elif status == "cancelled":
            view.outcome.cancelled = True
        elif message and status not in ("completed", "waiting"):
            await self._safely_update_status(view.status_fn, str(message))

    async def _render_token(self, payload: dict[str, Any], view: TurnView) -> None:
        view.outcome.chunks.append(str(payload.get("delta", "")))

    async def _render_error(self, payload: dict[str, Any], view: TurnView) -> None:
        view.outcome.error = str(payload.get("error") or "The turn failed")

    async def _render_budget_blocked(self, payload: dict[str, Any], view: TurnView) -> None:
        view.outcome.refusal = budget_blocked_text(payload)

    async def _render_access_denied(self, payload: dict[str, Any], view: TurnView) -> None:
        view.outcome.refusal = access_denied_text(payload)

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
        await self._share_artifact(payload, view, "📄 *Artifact created*")

    async def _render_artifact_updated(self, payload: dict[str, Any], view: TurnView) -> None:
        version = payload.get("version")
        await self._share_artifact(
            payload, view, "📝 *Artifact updated*" + (f" (v{version})" if version else "")
        )

    async def _share_artifact(self, payload: dict[str, Any], view: TurnView, heading: str) -> None:
        """The deliverable itself, uploaded to the thread when its content came with the event,
        else linked to the session where it opens."""
        title = str(payload.get("title") or "Deliverable")
        filename = str(payload.get("filename") or "artifact.md")
        content = payload.get("content")
        if (
            isinstance(content, str)
            and content
            and len(content) <= ARTIFACT_UPLOAD_CHARS
            and self._slack_api_ready
            and not view.private
            and await upload_file(
                self.app.client,
                channel_id=view.channel_id,
                thread_ts=view.thread_ts,
                filename=filename,
                content=content,
                title=title,
                comment=f"{heading}: *{title}*",
            )
        ):
            return
        artifact_id = str(payload.get("id") or "")
        link = (
            f"<{view.link_url}?artifact={artifact_id}|open it in Rocket Chat>"
            if view.link_url and artifact_id
            else "open the session in Rocket Chat to read it"
        )
        await view.say_fn(
            text=f"{heading}: `{filename}` — *{title}*; {link}.", thread_ts=view.thread_ts
        )

    _EVENT_RENDERERS: ClassVar[dict[EventType, Callable[..., Awaitable[None]]]] = {
        EventType.TOOL_STARTED: _render_tool_started,
        EventType.TOOL_COMPLETED: _render_tool_completed,
        EventType.STATUS_CHANGED: _render_status,
        EventType.TOKEN: _render_token,
        EventType.ERROR: _render_error,
        EventType.BUDGET_BLOCKED: _render_budget_blocked,
        EventType.AGENT_ACCESS_DENIED: _render_access_denied,
        EventType.FILE_DIFF: _render_file_diff,
        EventType.DECISION_REQUIRED: _render_question,
        EventType.APPROVAL_REQUIRED: _render_approval,
        EventType.ARTIFACT_CREATED: _render_artifact_created,
        EventType.ARTIFACT_UPDATED: _render_artifact_updated,
        EventType.SUBAGENT_STARTED: _render_subagent_started,
        EventType.SUBAGENT_COMPLETED: _render_subagent_completed,
        EventType.PLAN_UPDATED: _render_plan,
        EventType.WAIT_STARTED: _render_wait_started,
    }

    async def _post_result(self, view: TurnView, org_policy: SlackOrgPolicy) -> None:
        """Post the answer, diffs and screenshots (long content goes up as files), closed by the
        turn's summary card; then a card for each pull request it opened."""
        outcome = view.outcome
        final_text = outcome.text or self._empty_answer(outcome)
        use_files = org_policy.long_output_to_file and self._slack_api_ready
        footer: list[dict[str, Any]] | None = self._summary_blocks(view)
        stream = view.stream
        posted = False
        if stream is not None and stream.started:
            if _MENTION.search(markdown_to_slack_mrkdwn(final_text)):
                # Slack notifies the mentions of a new message, not those written into the
                # reply the stream opened: the card closes and the answer follows it.
                await stream.finish("", [])
            elif await self._finish_stream(view, stream, final_text, footer, use_files, org_policy):
                posted = True
        if not posted:
            if outcome.diffs:
                await self._post_changes(
                    view, final_text, use_files and diff_is_large(outcome.diffs), footer or []
                )
                footer = None
            elif final_text and use_files and len(final_text) > org_policy.long_output_chars:
                await self._post_long_answer(view, final_text, org_policy.long_output_chars)
            elif final_text:
                *head, last = chunk_slack_message(markdown_to_slack_mrkdwn(final_text))
                for chunk in head:
                    await view.say_fn(text=chunk, thread_ts=view.thread_ts)
                # The summary rides under the answer, not in a message of its own.
                await view.say_fn(
                    text=last, blocks=_with_footer(last, footer), thread_ts=view.thread_ts
                )
                footer = None
            if use_files:
                await self._upload_screenshots(view)
            if footer:
                await view.say_fn(blocks=footer, text="Turn summary", thread_ts=view.thread_ts)
        await self._post_pull_request_cards(view)

    @staticmethod
    def _empty_answer(outcome: TurnOutcome) -> str:
        if outcome.cancelled:
            return "⏹️ Stopped from the web app."
        if outcome.diffs or outcome.wait is not None or outcome.pull_requests:
            return ""
        return "Done."

    def _summary_blocks(self, view: TurnView, *, failed: bool = False) -> list[dict[str, Any]]:
        outcome, stream = view.outcome, view.stream
        status = (
            "failed"
            if failed
            else "stopped"
            if outcome.cancelled
            else "waiting"
            if outcome.final_status == "waiting" or outcome.wait is not None
            else "completed"
        )
        changes = stream.changes_line() if stream is not None else ""
        if not changes and outcome.diffs:
            changes = f"Edited {len(outcome.diffs)} file{'s' if len(outcome.diffs) != 1 else ''}"
        return build_summary_card(
            TurnSummary(
                status=status,
                elapsed_seconds=stream.elapsed_seconds if stream is not None else None,
                changes=changes,
                cost_usd=outcome.cost_usd,
                pull_request_url=outcome.pull_requests[-1][2] if outcome.pull_requests else "",
                wait_notice=wait_notice(*outcome.wait)
                if status == "waiting" and outcome.wait
                else "",
            ),
            link_url=view.link_url,
            retry_value=view.session_id if failed and view.request_text else None,
        )

    async def _post_pull_request_cards(self, view: TurnView) -> None:
        """Hook: the assistant posts a card per pull request the turn opened."""
        return None

    async def _finish_stream(
        self,
        view: TurnView,
        stream: TurnStream,
        text: str,
        footer: list[dict[str, Any]] | None,
        use_files: bool,
        org_policy: SlackOrgPolicy,
    ) -> bool:
        """Write the answer into the turn's streamed reply; then what does not fit a message
        (the full long answer, the diffs, screenshots) follows in the thread."""
        too_long = len(text) > (org_policy.long_output_chars if use_files else ANSWER_CHARS)
        shown = head_of(text, min(org_policy.long_output_chars, 1500)) if too_long else text
        if not await stream.finish(shown, footer or []):
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

    async def _post_changes(
        self, view: TurnView, summary: str, as_file: bool, footer: list[dict[str, Any]]
    ) -> None:
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
            await view.say_fn(blocks=[*blocks, *footer], text=formatted, thread_ts=view.thread_ts)
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
        await view.say_fn(blocks=[*blocks, *footer], text=formatted, thread_ts=view.thread_ts)

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


# A user, a user group or @here, @channel, @everyone, as Slack mrkdwn writes them.
_MENTION = re.compile(
    r"<@[UW][A-Z0-9]+(?:\|[^>]*)?>|<!subteam\^S[A-Z0-9]+(?:\|[^>]*)?>|<!(?:here|channel|everyone)\b"
)


def _with_footer(text: str, footer: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    section = {"type": "section", "text": {"type": "mrkdwn", "text": text}}
    return [section, *(footer or [])]

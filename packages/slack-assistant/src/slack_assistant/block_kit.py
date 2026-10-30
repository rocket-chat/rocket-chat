import re
from typing import Any

from specifications.interfaces.agent import FileDiff, InteractiveQuestion

SLACK_TEXT_BLOCK_LIMIT = 2800


def _resolve_table_cell(cell: str, inline_codes: list[str] | None) -> str:
    """Resolve inline-code placeholders and strip emphasis inside a table cell.

    Slack never renders markdown emphasis or inline-code styling inside a fenced
    code block, so a converted table must contain plain text. Otherwise the
    ``@@ROCKET_INLINE_CODE_n@@`` sentinels would leak verbatim into the message
    whenever a cell contains inline code.
    """
    if not cell:
        return cell
    if inline_codes:

        def _sub(match: re.Match[str]) -> str:
            idx = int(match.group(1))
            if 0 <= idx < len(inline_codes):
                return inline_codes[idx].strip("`")
            return match.group(0)

        cell = re.sub(r"@@ROCKET_INLINE_CODE_(\d+)@@", _sub, cell)
    cell = cell.replace("**", "").replace("__", "")
    return cell.strip()


def _format_ascii_table(table_lines: list[str], inline_codes: list[str] | None = None) -> str:
    """Format markdown table lines into an aligned monospace ASCII table."""
    raw_rows: list[list[str]] = []
    for line in table_lines:
        trimmed = line.strip()
        if not trimmed:
            continue
        if trimmed.startswith("|"):
            trimmed = trimmed[1:]
        if trimmed.endswith("|"):
            trimmed = trimmed[:-1]
        cells = [_resolve_table_cell(c.strip(), inline_codes) for c in trimmed.split("|")]
        raw_rows.append(cells)

    if len(raw_rows) < 2:
        return "\n".join(table_lines)

    delimiter_row = raw_rows[1]
    is_delimiter = len(delimiter_row) > 0 and all(
        bool(re.match(r"^:?-{2,}:?$", cell)) for cell in delimiter_row if cell
    )
    if not is_delimiter:
        return "\n".join(table_lines)

    header = raw_rows[0]
    data_rows = raw_rows[2:]

    num_cols = max(len(header), *(len(r) for r in data_rows))
    header = header + [""] * (num_cols - len(header))
    padded_data = [r + [""] * (num_cols - len(r)) for r in data_rows]

    col_widths = [len(header[i]) for i in range(num_cols)]
    for r in padded_data:
        for i in range(num_cols):
            col_widths[i] = max(col_widths[i], len(r[i]))
    col_widths = [max(w, 3) for w in col_widths]

    res_lines: list[str] = []
    header_str = " | ".join(header[i].ljust(col_widths[i]) for i in range(num_cols))
    sep_str = "-+-".join("-" * col_widths[i] for i in range(num_cols))
    res_lines.append(header_str)
    res_lines.append(sep_str)
    for r in padded_data:
        res_lines.append(" | ".join(r[i].ljust(col_widths[i]) for i in range(num_cols)))

    return "```\n" + "\n".join(res_lines) + "\n```"


def _convert_tables_to_code_blocks(
    text: str, code_blocks: list[str], inline_codes: list[str] | None = None
) -> str:
    """Detect markdown tables and convert them to aligned monospace blocks."""
    lines = text.split("\n")
    output_lines: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if "|" in line and i + 1 < len(lines):
            next_line = lines[i + 1]
            if "|" in next_line:
                raw_delims = [c.strip() for c in next_line.strip().strip("|").split("|")]
                if raw_delims and all(re.match(r"^:?-{2,}:?$", c) for c in raw_delims if c):
                    table_lines = [line, next_line]
                    i += 2
                    while i < len(lines) and "|" in lines[i] and lines[i].strip():
                        table_lines.append(lines[i])
                        i += 1
                    formatted_table = _format_ascii_table(table_lines, inline_codes)
                    idx = len(code_blocks)
                    code_blocks.append(formatted_table)
                    output_lines.append(f"@@ROCKET_CODE_BLOCK_{idx}@@")
                    continue
        output_lines.append(line)
        i += 1
    return "\n".join(output_lines)


def markdown_to_slack_mrkdwn(text: str) -> str:
    """Converts standard CommonMark markdown into Slack mrkdwn formatting."""
    if not text:
        return ""

    # Preserve code blocks by replacing them with collision-free placeholders
    code_blocks: list[str] = []

    def _save_code_block(match: re.Match[str]) -> str:
        code = match.group(2).strip("\r\n")
        idx = len(code_blocks)
        # Slack code blocks use triple backticks without language tags
        if "\n" in code:
            code_blocks.append(f"```\n{code}\n```")
        else:
            code_blocks.append(f"```{code}```")
        return f"@@ROCKET_CODE_BLOCK_{idx}@@"

    text = re.sub(r"```([a-zA-Z0-9_-]*)\n?(.*?)```", _save_code_block, text, flags=re.DOTALL)

    # Preserve inline code with collision-free placeholders
    inline_codes: list[str] = []

    def _save_inline_code(match: re.Match[str]) -> str:
        idx = len(inline_codes)
        inline_codes.append(match.group(0))
        return f"@@ROCKET_INLINE_CODE_{idx}@@"

    text = re.sub(r"`[^`\n]+`", _save_inline_code, text)

    # Convert markdown tables to aligned monospace code blocks
    text = _convert_tables_to_code_blocks(text, code_blocks, inline_codes)

    # Convert markdown links [title](url) -> <url|title>
    text = re.sub(r"\[([^\]]+)\]\((https?://[^\s)]+)\)", r"<\2|\1>", text)

    # Slack mentions: Slack only linkifies "<@U123>"; a bare "@U123" the model writes is
    # rendered literally. Wrap bare user ids (and the special @here/@channel/@everyone),
    # leaving an already-bracketed "<@U123>" untouched via the negative lookbehind.
    text = re.sub(r"(?<![\w<])@(here|channel|everyone)\b", r"<!\1>", text)
    text = re.sub(r"(?<![\w<])@([UW][A-Z0-9]{6,})\b", r"<@\1>", text)

    # Convert bold **text** or __text__ -> *text*
    text = re.sub(r"\*\*([^*]+)\*\*", r"*\1*", text)
    text = re.sub(r"__([^_]+)__", r"*\1*", text)

    # Convert strikethrough ~~text~~ -> ~text~
    text = re.sub(r"~~([^~]+)~~", r"~\1~", text)

    # Convert headers (# Header, ## Header, ### Header) -> *Header*
    text = re.sub(r"^(?:#{1,6})\s+([^\n]+)", r"*\1*", text, flags=re.MULTILINE)

    # Convert bullet points (- item or * item) -> • item
    text = re.sub(r"^[\*\-]\s+([^\n]+)", r"• \1", text, flags=re.MULTILINE)

    # Restore code blocks first (they may legitimately contain inline-code
    # placeholders captured while converting markdown tables), then restore the
    # remaining inline codes. Restoring in this order guarantees no sentinel can
    # ever survive into the rendered message.
    for idx, block in enumerate(code_blocks):
        text = text.replace(f"@@ROCKET_CODE_BLOCK_{idx}@@", block)

    # Restore inline codes
    for idx, code in enumerate(inline_codes):
        text = text.replace(f"@@ROCKET_INLINE_CODE_{idx}@@", code)

    return text


def chunk_slack_message(text: str, max_chars: int = SLACK_TEXT_BLOCK_LIMIT) -> list[str]:
    """Splits a long message into sequential Slack message chunks within length limits."""
    if len(text) <= max_chars:
        return [text]

    chunks: list[str] = []
    paragraphs = text.split("\n\n")
    current_chunk: list[str] = []
    current_len = 0

    for p in paragraphs:
        if len(p) > max_chars:
            lines = p.split("\n")
            for line in lines:
                if current_len + len(line) + 1 > max_chars:
                    if current_chunk:
                        chunks.append("\n\n".join(current_chunk))
                        current_chunk = []
                        current_len = 0
                current_chunk.append(line)
                current_len += len(line) + 1
        elif current_len + len(p) + 2 > max_chars:
            if current_chunk:
                chunks.append("\n\n".join(current_chunk))
                current_chunk = []
                current_len = 0
            current_chunk.append(p)
            current_len += len(p) + 2
        else:
            current_chunk.append(p)
            current_len += len(p) + 2

    if current_chunk:
        chunks.append("\n\n".join(current_chunk))

    return chunks or [text]


def build_diff_blocks(
    diffs: list[FileDiff], max_chars: int = SLACK_TEXT_BLOCK_LIMIT
) -> list[dict[str, Any]]:
    """Format file diffs as syntax-highlighted diff blocks within Slack limits."""
    blocks: list[dict[str, Any]] = []

    for diff in diffs:
        status_indicator = "📝 Modified"
        if diff.is_new_file:
            status_indicator = "✨ Created"
        elif diff.deletions > 0 and diff.additions == 0:
            status_indicator = "🗑️ Deleted"

        header_text = f"*{status_indicator} `{diff.path}`* (+{diff.additions} / -{diff.deletions})"
        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": header_text,
                },
            }
        )

        content = diff.diff_content
        if len(content) > max_chars:
            truncated_len = max_chars - 100
            content = f"{content[:truncated_len]}\n... [Diff truncated due to Slack size limits]"

        code_text = f"```diff\n{content}\n```"
        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": code_text,
                },
            }
        )

    return blocks


QUESTION_SUBMIT_ACTION_ID = "question_submit"
QUESTION_CHECKBOXES_ACTION_ID = "question_checkboxes"
QUESTION_OTHER_ACTION_ID = "question_other"
QUESTION_OTHER_CALLBACK_ID = "question_other_modal"
# Slack's limits: 5 elements per actions block, 10 checkbox options, 150-character values.
_ACTIONS_PER_BLOCK = 5
_MAX_CHECKBOXES = 10
_OPTION_VALUE_CHARS = 150


def session_block_id(session_id: str, index: int = 0) -> str:
    """The block id of a gate's buttons: they name their session, so a click finds it whatever
    the thread or replica (a continued direct message answers in a newer thread)."""
    suffix = f"#{index}" if index else ""
    return f"{SESSION_BLOCK_PREFIX}{session_id}{suffix}"[:255]


def session_of_block(block_id: str) -> str:
    return block_id.removeprefix(SESSION_BLOCK_PREFIX).split("#", 1)[0]


def approve_label(action_type: str) -> str:
    """ "Approve git push" for ``git_push``: the button says what it allows."""
    readable = action_type.replace("_", " ").strip() or "action"
    return f"Approve {readable}"[:75]


def build_approval_blocks(
    action_id: str,
    title: str = "Human Approval Required",
    description: str | None = None,
    *,
    action_type: str = "",
    session_id: str = "",
) -> list[dict[str, Any]]:
    """A decision gate: what is asked, and buttons named after the action."""
    actions: dict[str, Any] = {
        "type": "actions",
        "elements": [
            {
                "type": "button",
                "text": {"type": "plain_text", "text": approve_label(action_type), "emoji": True},
                "style": "primary",
                "action_id": "approve_action",
                "value": action_id,
            },
            {
                "type": "button",
                "text": {"type": "plain_text", "text": "Reject", "emoji": True},
                "style": "danger",
                "action_id": "reject_action",
                "value": action_id,
            },
        ],
    }
    if session_id:
        actions["block_id"] = session_block_id(session_id)
    return [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"⚠️ *{title}*\n{description or 'Review the changes before proceeding.'}",
            },
        },
        actions,
    ]


def build_question_blocks(
    question: InteractiveQuestion, *, session_id: str = ""
) -> list[dict[str, Any]]:
    """A clarification question: one button per option (or checkboxes and Submit for a
    multi-select), and **Other…**, which opens a form for a free answer."""
    blocks: list[dict[str, Any]] = [
        {"type": "section", "text": {"type": "mrkdwn", "text": f"❓ *{question.question_text}*"}}
    ]
    if question.is_multi_select and question.options:
        elements = _checkbox_elements(question)
    else:
        elements = [
            _option_button(question, index, option) for index, option in enumerate(question.options)
        ]
    elements.append(
        {
            "type": "button",
            "text": {"type": "plain_text", "text": "Other…", "emoji": True},
            "action_id": QUESTION_OTHER_ACTION_ID,
            "value": question.question_id,
        }
    )
    for index, start in enumerate(range(0, len(elements), _ACTIONS_PER_BLOCK)):
        block: dict[str, Any] = {
            "type": "actions",
            "elements": elements[start : start + _ACTIONS_PER_BLOCK],
        }
        if session_id:
            block["block_id"] = session_block_id(session_id, index)
        blocks.append(block)
    return blocks


def _option_button(question: InteractiveQuestion, index: int, option: str) -> dict[str, Any]:
    label = f"⭐ {option}" if option == question.default_recommended_option else option
    return {
        "type": "button",
        "text": {"type": "plain_text", "text": label[:75], "emoji": True},
        # Slack requires action_id to be unique within a message; the selected option is
        # carried by `value`, so the index only guarantees uniqueness (matched by a regex).
        "action_id": f"question_option_select_{index}",
        "value": f"{question.question_id}:{option}",
    }


def _checkbox_elements(question: InteractiveQuestion) -> list[dict[str, Any]]:
    options = [
        {
            "text": {"type": "plain_text", "text": option[:75]},
            "value": option[:_OPTION_VALUE_CHARS],
        }
        for option in question.options[:_MAX_CHECKBOXES]
    ]
    return [
        {"type": "checkboxes", "action_id": QUESTION_CHECKBOXES_ACTION_ID, "options": options},
        {
            "type": "button",
            "text": {"type": "plain_text", "text": "Submit", "emoji": True},
            "style": "primary",
            "action_id": QUESTION_SUBMIT_ACTION_ID,
            "value": question.question_id,
        },
    ]


def build_other_answer_modal(question_text: str, private_metadata: str) -> dict[str, Any]:
    """The form **Other…** opens: the question, and a free answer."""
    return {
        "type": "modal",
        "callback_id": QUESTION_OTHER_CALLBACK_ID,
        "private_metadata": private_metadata[:3000],
        "title": {"type": "plain_text", "text": "Your answer"},
        "submit": {"type": "plain_text", "text": "Send"},
        "close": {"type": "plain_text", "text": "Cancel"},
        "blocks": [
            {"type": "section", "text": {"type": "mrkdwn", "text": f"*{question_text[:2900]}*"}},
            {
                "type": "input",
                "block_id": "answer",
                "label": {"type": "plain_text", "text": "Answer"},
                "element": {
                    "type": "plain_text_input",
                    "action_id": "text",
                    "multiline": True,
                    "max_length": 2000,
                },
            },
        ],
    }


def build_decision_blocks(text: str) -> list[dict[str, Any]]:
    """What a gate message becomes once answered: the decision, without its buttons."""
    return [{"type": "section", "text": {"type": "mrkdwn", "text": text[:2900]}}]


def open_in_rocket_context(
    link_url: str | None, detail: str | None = None
) -> dict[str, Any] | None:
    """A context line under an answer: the session link and what the turn did."""
    parts = [f":rocket: <{link_url}|Open in Rocket Chat>"] if link_url else []
    if detail:
        parts.append(detail)
    if not parts:
        return None
    return {"type": "context", "elements": [{"type": "mrkdwn", "text": " · ".join(parts)}]}


def build_home_view(
    *,
    linked_as: str | None,
    sessions: list[tuple[str, str, str | None]],
    web_url: str | None,
    command: str = "/rocket",
    bot_name: str = "Rocket",
) -> dict[str, Any]:
    """The App Home tab: who you are in Rocket Chat, your recent threads and how to start one.

    ``sessions`` holds (title, state, link) for the viewer's recent Slack work.
    """
    blocks: list[dict[str, Any]] = [
        {"type": "header", "text": {"type": "plain_text", "text": bot_name[:150]}},
    ]
    if linked_as:
        who = f":white_check_mark: Linked to *{linked_as}* in Rocket Chat."
    else:
        who = (
            f":link: Your Slack account is not linked yet. Run `{command} pair` and enter the "
            "code in Rocket Chat under *Settings → Slack*."
        )
    blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": who}})
    blocks.append({"type": "divider"})
    if sessions:
        lines = []
        for title, state, thread_link in sessions[:10]:
            label = f"<{thread_link}|{title}>" if thread_link else title
            lines.append(f"• {label} — _{state}_")
        recent = "*Recent threads*\n" + "\n".join(lines)
    else:
        recent = "*Recent threads*\nNothing yet. Mention me in a channel or send me a message."
    blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": recent[:2900]}})
    blocks.append({"type": "divider"})
    blocks.append(
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    "*Ways to work with me*\n"
                    f"• `@{bot_name} <task>` in any channel, or message me directly; reply in "
                    "the thread to follow up\n"
                    f"• `{command} new <task>` starts a thread; `{command} status` and "
                    f"`{command} stop` manage your running work\n"
                    "• *Ask Rocket* in a message's ⋮ menu to work on that message\n"
                    "• Shortcuts in a message: `!fast`, `!deep`, `!agent <name>`, "
                    "`!repo <owner/name>`; start with `(aside)` for a message I should ignore"
                ),
            },
        }
    )
    link = open_in_rocket_context(web_url)
    if link is not None:
        blocks.append(link)
    return {"type": "home", "blocks": blocks}


SHARE_ACTION_ID = "share_private_answer"
# The block id of a gate's buttons sent by direct message: a click there cannot name the
# session through its channel and thread, so the buttons carry it.
SESSION_BLOCK_PREFIX = "rocket-session:"


def with_session_and_origin(
    blocks: list[dict[str, Any]], session_id: str, origin: str
) -> list[dict[str, Any]]:
    """``blocks`` for a direct message: the buttons name their session, and a last line says
    which request they are about."""
    tagged = [
        {**block, "block_id": session_block_id(session_id)}
        if block.get("type") == "actions" and "block_id" not in block
        else block
        for block in blocks
    ]
    return [*tagged, {"type": "context", "elements": [{"type": "mrkdwn", "text": origin}]}]


def build_private_blocks(
    text: str, *, link_url: str | None, share_value: str | None = None
) -> list[dict[str, Any]]:
    """A private (ephemeral) reply: its text, an **Open session** button and, once there is an
    answer, a **Share in thread** button that posts it publicly."""
    blocks: list[dict[str, Any]] = [
        {"type": "section", "text": {"type": "mrkdwn", "text": text[:2900]}}
    ]
    buttons: list[dict[str, Any]] = []
    if link_url:
        buttons.append(
            {
                "type": "button",
                "text": {"type": "plain_text", "text": "Open session"},
                "url": link_url,
                "action_id": "open_private_session",
            }
        )
    if share_value:
        buttons.append(
            {
                "type": "button",
                "text": {"type": "plain_text", "text": "Share in thread"},
                "value": share_value,
                "action_id": SHARE_ACTION_ID,
            }
        )
    if buttons:
        blocks.append({"type": "actions", "elements": buttons})
    return blocks

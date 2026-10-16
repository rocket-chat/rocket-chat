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


def build_approval_blocks(
    action_id: str,
    title: str = "Human Approval Required",
    description: str | None = None,
) -> list[dict[str, Any]]:
    """Format decision gate with [Approve & Push] and [Reject] action buttons."""
    blocks: list[dict[str, Any]] = [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"⚠️ *{title}*\n{description or 'Review the changes before proceeding.'}",
            },
        },
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {
                        "type": "plain_text",
                        "text": "Approve & Push",
                        "emoji": True,
                    },
                    "style": "primary",
                    "action_id": "approve_action",
                    "value": action_id,
                },
                {
                    "type": "button",
                    "text": {
                        "type": "plain_text",
                        "text": "Reject",
                        "emoji": True,
                    },
                    "style": "danger",
                    "action_id": "reject_action",
                    "value": action_id,
                },
            ],
        },
    ]
    return blocks


def build_question_blocks(question: InteractiveQuestion) -> list[dict[str, Any]]:
    """Format interactive clarification questions as in-thread selectable options."""
    blocks: list[dict[str, Any]] = [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"❓ *{question.question_text}*",
            },
        }
    ]

    elements: list[dict[str, Any]] = []
    for opt_index, opt in enumerate(question.options):
        is_recommended = opt == question.default_recommended_option
        label = f"⭐ {opt}" if is_recommended else opt
        elements.append(
            {
                "type": "button",
                "text": {
                    "type": "plain_text",
                    "text": label[:75],
                    "emoji": True,
                },
                # Slack requires action_id to be unique within a message; the
                # selected option is carried by `value`, so the index only
                # guarantees uniqueness and is matched by a regex handler.
                "action_id": f"question_option_select_{opt_index}",
                "value": f"{question.question_id}:{opt}",
            }
        )

    # Slack allows up to 5 elements per actions block; split if needed
    for i in range(0, len(elements), 5):
        blocks.append(
            {
                "type": "actions",
                "elements": elements[i : i + 5],
            }
        )

    return blocks


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
) -> dict[str, Any]:
    """The App Home tab: who you are in Rocket Chat, your recent threads and how to start one.

    ``sessions`` holds (title, state, link) for the viewer's recent Slack work.
    """
    blocks: list[dict[str, Any]] = [
        {"type": "header", "text": {"type": "plain_text", "text": "Rocket Chat"}},
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
                    "• `@Rocket <task>` in any channel, or message me directly\n"
                    f"• `{command} new <task>` starts a thread; `{command} status` and "
                    f"`{command} stop` manage your running work\n"
                    "• *Ask Rocket* in a message's ⋮ menu to work on that message"
                ),
            },
        }
    )
    link = open_in_rocket_context(web_url)
    if link is not None:
        blocks.append(link)
    return {"type": "home", "blocks": blocks}


SHARE_ACTION_ID = "share_private_answer"


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

"""Context policy and request message layout for prefix-cached turns.

Enforces deterministic message positioning:
- Turn-fixed context (recalled memories, active repo, attachment manifest) is inserted
  once immediately after the current turn's user message. Because it remains in the same
  position across steps, requests 2..N share a byte-identical prefix for prompt caching.
- Dynamic notes (sticky turn notices, next-only truncation/checklist notices) ride at the tail.
- Images from `view_image` ride at the tail after the tool result that requested them,
  preventing retro-attachment that would invalidate the cached prefix of earlier steps.
- User attachments are described via lightweight manifest rather than dumped into the prompt,
  allowing on-demand retrieval via `file_read`.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace

from agent_core.notes import REMINDER_CLOSE, REMINDER_OPEN, is_engine_note, render_notes
from specifications.interfaces.llm import AttachmentRef, ChatMessage

__all__ = [
    "assemble_request_messages",
    "find_turn_user_index",
    "format_attachment_manifest",
    "with_cache_breakpoint",
]


def format_attachment_manifest(attachments: Sequence[AttachmentRef]) -> str:
    """Lightweight descriptor manifest so the model knows what was attached without dumping full contents."""
    if not attachments:
        return ""
    lines = [f"Attached files ({len(attachments)}):"]
    for att in attachments:
        path_info = f", path: {att.workspace_path}" if att.workspace_path else ""
        lines.append(f"- {att.name} ({att.mime}, {att.size:,} bytes{path_info})")
    lines.append("To inspect full file contents, use file_read or appropriate inspection tools.")
    return "\n".join(lines)


def find_turn_user_index(messages: Sequence[ChatMessage]) -> int:
    """Index of the latest real user message for this turn, skipping engine reminders."""
    for idx in range(len(messages) - 1, -1, -1):
        msg = messages[idx]
        if msg.role == "user" and not is_engine_note(msg):
            content = msg.content or ""
            if not content.startswith(REMINDER_OPEN):
                return idx
    return len(messages) - 1 if messages else -1


def with_cache_breakpoint(messages: list[ChatMessage]) -> list[ChatMessage]:
    """Mark the last stable conversation message with a prompt-cache breakpoint."""
    for index in range(len(messages) - 1, -1, -1):
        message = messages[index]
        if message.role != "system" and message.content and not message.attachments:
            if not message.content.startswith(REMINDER_OPEN):
                return [*messages[:index], replace(message, cacheable=True), *messages[index + 1 :]]
    return messages


def assemble_request_messages(
    messages: list[ChatMessage],
    *,
    context_note: str = "",
    sticky_notes: Sequence[str] = (),
    next_notes: Sequence[str] = (),
    turn_images: Sequence[AttachmentRef] = (),
    pending_attachments: Sequence[AttachmentRef] = (),
    is_synthetic: bool = False,
) -> list[ChatMessage]:
    """Assemble outgoing request messages with a stable cached prefix and tail notices."""
    user_idx = find_turn_user_index(messages)
    notes_list: list[str] = []
    if context_note and context_note.strip():
        notes_list.append(context_note.strip())

    images_to_append: list[AttachmentRef] = list(turn_images)

    turn_fixed_content = "\n\n".join(notes_list).strip()

    res: list[ChatMessage] = list(messages)
    if pending_attachments and user_idx >= 0 and not is_synthetic:
        res[user_idx] = replace(res[user_idx], attachments=list(pending_attachments))

    if turn_fixed_content:
        fixed_msg = ChatMessage(
            role="user",
            content=f"{REMINDER_OPEN}\n{turn_fixed_content}\n{REMINDER_CLOSE}",
        )
        if is_synthetic or user_idx < 0:
            res.append(fixed_msg)
        else:
            res.insert(user_idx + 1, fixed_msg)

    # Set cache breakpoint on the last stable message before tail additions
    res = with_cache_breakpoint(res)

    tail_notes = [n.strip() for n in (*sticky_notes, *next_notes) if n and n.strip()]
    if tail_notes:
        tail_msg = ChatMessage(role="user", content=render_notes(tail_notes))
        res.append(tail_msg)

    if images_to_append:
        if res and res[-1].role == "user" and not res[-1].cacheable:
            last = res[-1]
            existing = list(last.attachments or [])
            res[-1] = replace(last, attachments=[*existing, *images_to_append])
        else:
            res.append(ChatMessage(role="user", content=None, attachments=images_to_append))

    return res

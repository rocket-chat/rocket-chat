"""Backfill timestamps on sessions conversation_history from session_messages.

Revision ID: 041_backfill_conversation_history_timestamps
Revises: 040_msg_reasoning_blocks
Create Date: 2026-10-18 15:20:00.000000

Ensures existing sessions and their tool calls preserve their immutable
historical timestamps instead of defaulting to query time.
"""

import json
import time
from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "041_backfill_msg_timestamps"
down_revision: str | None = "040_msg_reasoning_blocks"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _backfill_tool_calls(m: dict[str, Any], created_at: int) -> bool:
    updated = False
    if not (m.get("tool_calls") and isinstance(m["tool_calls"], list)):
        return False
    msg_tc: list[Any] = []
    for tc in m["tool_calls"]:
        if isinstance(tc, dict):
            tc_dict = dict(tc)
            if "created_at" not in tc_dict or tc_dict["created_at"] is None:
                updated = True
                tc_dict["created_at"] = created_at
            msg_tc.append(tc_dict)
        else:
            msg_tc.append(tc)
    m["tool_calls"] = msg_tc
    return updated


def _backfill_message(
    m: dict[str, Any],
    role_indices: dict[str, int],
    messages_by_role: dict[str, list[Any]],
    session_created_at: int,
) -> bool:
    updated = False
    role = m.get("role", "user")
    if "created_at" not in m or m["created_at"] is None:
        updated = True
        curr_idx = role_indices.get(role, 0)
        role_indices[role] = curr_idx + 1
        role_msgs = messages_by_role.get(role, [])
        if curr_idx < len(role_msgs):
            m["created_at"] = int(role_msgs[curr_idx][3])
        else:
            m["created_at"] = session_created_at

    msg_created_at = int(m.get("created_at") or session_created_at)
    if _backfill_tool_calls(m, msg_created_at):
        updated = True
    return updated


def _backfill_session(row: Any, tables: set[str], bind: Any) -> None:
    session_id = row[0]
    session_created_at = int(row[1] or time.time())
    history = row[2]
    if not history or not isinstance(history, list):
        return

    msg_rows: list[Any] = []
    if "session_messages" in tables:
        msg_rows = bind.execute(
            sa.text(
                "SELECT seq, role, content, created_at, tool_calls FROM session_messages "
                "WHERE session_id = :sid ORDER BY seq ASC"
            ),
            {"sid": session_id},
        ).fetchall()

    role_indices: dict[str, int] = {}
    messages_by_role: dict[str, list[Any]] = {}
    for mr in msg_rows:
        messages_by_role.setdefault(mr[1], []).append(mr)

    updated = False
    new_history: list[dict[str, Any]] = []
    for msg in history:
        if not isinstance(msg, dict):
            new_history.append(msg)
            continue
        m = dict(msg)
        if _backfill_message(m, role_indices, messages_by_role, session_created_at):
            updated = True
        new_history.append(m)

    if updated:
        if bind.dialect.name == "postgresql":
            bind.execute(
                sa.text(
                    "UPDATE sessions SET conversation_history = CAST(:hist AS jsonb) WHERE id = :sid"
                ),
                {"hist": json.dumps(new_history), "sid": session_id},
            )
        else:
            bind.execute(
                sa.text("UPDATE sessions SET conversation_history = :hist WHERE id = :sid"),
                {"hist": json.dumps(new_history), "sid": session_id},
            )


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "sessions" not in tables:
        return

    sessions = bind.execute(
        sa.text("SELECT id, created_at, conversation_history FROM sessions")
    ).fetchall()

    for row in sessions:
        _backfill_session(row, tables, bind)


def downgrade() -> None:
    pass

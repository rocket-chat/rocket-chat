"""One kind of automation: missions, their runs and the deliveries of their results.

Revision ID: 064_unified_missions
Revises: 063_team_settings_and_sandboxes
Create Date: 2026-11-03 12:00:00.000000

``scheduled_missions`` (a member's prompt on a cron schedule, optionally reported to a Slack
channel) and ``triggers`` (an organization's rules that start sessions from events) become one
``missions`` table: owned like every shared object, with one to three triggers, an agent, its
instructions and where the result goes. Each scheduled mission keeps its id, owner, schedule and
Slack report; each trigger becomes an organization mission with the same id, its event as its
trigger, and a review delivered on the pull request for review mode. ``mission_runs`` records
each run and ``mission_deliveries`` is the durable queue of results waiting to be posted. Every
new table has forced row-level security.
"""

import json
import time
from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "064_unified_missions"
down_revision: str | None = "063_team_settings_and_sandboxes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = ("missions", "mission_runs", "mission_deliveries")


def _json() -> sa.types.TypeEngine[Any]:
    return sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def _org_column() -> sa.Column[str]:
    return sa.Column(
        "org_id",
        sa.String(length=64),
        sa.ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )


def _create_tables() -> None:
    op.create_table(
        "missions",
        sa.Column("id", sa.String(length=64), primary_key=True, nullable=False),
        _org_column(),
        sa.Column("scope", sa.String(length=20), nullable=False, server_default="user", index=True),
        sa.Column(
            "user_id",
            sa.String(length=64),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=True,
            index=True,
        ),
        sa.Column(
            "team_id",
            sa.String(length=64),
            sa.ForeignKey("teams.id", ondelete="RESTRICT"),
            nullable=True,
            index=True,
        ),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("instructions", sa.Text(), nullable=False, server_default=""),
        sa.Column("triggers", _json(), nullable=False),
        sa.Column("deliveries", _json(), nullable=False),
        sa.Column("secrets", _json(), nullable=False),
        sa.Column("webhook_fingerprint", sa.String(length=32), nullable=False, server_default=""),
        sa.Column("agent_id", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("permission_mode", sa.String(length=16), nullable=False, server_default=""),
        sa.Column("model_tier", sa.String(length=16), nullable=False, server_default=""),
        sa.Column("sandbox_type", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("mode", sa.String(length=16), nullable=False, server_default="work"),
        sa.Column("auto_fix", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("git_repo", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("git_branch", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("run_as", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("next_run_at", sa.BigInteger(), nullable=True, index=True),
        sa.Column("last_run_at", sa.BigInteger(), nullable=True),
        sa.Column("last_session_id", sa.String(length=64), nullable=True),
        sa.Column("last_status", sa.String(length=32), nullable=True),
        sa.Column("created_by", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
    )
    op.create_table(
        "mission_runs",
        sa.Column("id", sa.String(length=64), primary_key=True, nullable=False),
        _org_column(),
        sa.Column(
            "mission_id",
            sa.String(length=64),
            sa.ForeignKey("missions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("session_id", sa.String(length=64), nullable=False, index=True),
        sa.Column("source", sa.String(length=40), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("item", _json(), nullable=False),
        sa.Column(
            "status", sa.String(length=20), nullable=False, server_default="running", index=True
        ),
        sa.Column("deliveries", _json(), nullable=False),
        sa.Column("started_at", sa.BigInteger(), nullable=False),
        sa.Column("finished_at", sa.BigInteger(), nullable=True),
    )
    op.create_index("ix_mission_runs_mission_started", "mission_runs", ["mission_id", "started_at"])
    op.create_table(
        "mission_deliveries",
        sa.Column("id", sa.String(length=64), primary_key=True, nullable=False),
        _org_column(),
        sa.Column(
            "run_id",
            sa.String(length=64),
            sa.ForeignKey("mission_runs.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("payload", _json(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_attempt_at", sa.BigInteger(), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
    )
    op.create_index(
        "ix_mission_deliveries_due", "mission_deliveries", ["status", "next_attempt_at"]
    )


def _scheduled_mission(row: Any, now: int) -> dict[str, Any]:
    deliveries: list[dict[str, Any]] = [{"kind": "session"}]
    if row.slack_channel:
        # No bot was ever named: the organization's bot posts, as before.
        deliveries.append({"kind": "chat", "bot_id": "", "channel": row.slack_channel})
    return {
        "id": row.id,
        "org_id": row.org_id,
        "scope": "user",
        "user_id": row.user_id,
        "team_id": None,
        "name": row.name,
        "instructions": row.prompt,
        "triggers": [{"source": "schedule", "schedule": row.cron, "timezone": row.timezone}],
        "deliveries": deliveries,
        "secrets": {},
        "agent_id": row.agent_id or "",
        "permission_mode": row.permission_mode or "",
        "git_repo": row.git_repo or "",
        "git_branch": row.git_branch or "",
        "enabled": bool(row.enabled),
        "run_as": row.user_id,
        "next_run_at": row.next_run_at,
        "last_run_at": row.last_run_at,
        "last_session_id": row.last_session_id,
        "last_status": row.last_status,
        "created_by": row.user_id,
        "created_at": row.created_at,
        "updated_at": now,
    }


def _trigger(row: Any, now: int) -> dict[str, Any]:
    trigger: dict[str, Any] = {"source": row.source, "filter": row.filter or {}}
    if row.source == "schedule":
        trigger.update(schedule=row.schedule, timezone="UTC")
    review = row.mode == "review"
    deliveries = [{"kind": "session"}] + ([{"kind": "github_review"}] if review else [])
    return {
        "id": row.id,
        "org_id": row.org_id,
        "scope": "org",
        "user_id": None,
        "team_id": None,
        "name": row.name,
        "instructions": row.instructions or "",
        "triggers": [trigger],
        "deliveries": deliveries,
        "secrets": {},
        "agent_id": row.agent_id or "",
        "permission_mode": row.permission_mode or "",
        "model_tier": row.model_tier or "",
        "sandbox_type": row.sandbox_type or "",
        "mode": row.mode or "work",
        "auto_fix": bool(row.auto_fix),
        "enabled": bool(row.enabled),
        # The service member is made at the first run.
        "run_as": "",
        "created_by": row.created_by or "",
        "created_at": row.created_at,
        "updated_at": now,
    }


def _carry_over() -> None:
    bind = op.get_bind()
    now = int(time.time())
    json_columns = ("triggers", "deliveries", "secrets")
    missions = sa.table(
        "missions",
        *(
            sa.column(name, _json()) if name in json_columns else sa.column(name)
            for name in (
                "id", "org_id", "scope", "user_id", "team_id", "name", "instructions",
                "triggers", "deliveries", "secrets", "agent_id", "permission_mode", "model_tier",
                "sandbox_type", "mode", "auto_fix", "git_repo", "git_branch", "enabled",
                "run_as", "next_run_at", "last_run_at", "last_session_id", "last_status",
                "created_by", "created_at", "updated_at",
            )
        ),
    )  # fmt: skip
    rows = [
        _scheduled_mission(r, now)
        for r in bind.execute(sa.text("SELECT * FROM scheduled_missions"))
    ]
    rows += [_trigger(r, now) for r in bind.execute(sa.text("SELECT * FROM triggers"))]
    if rows:
        defaults = {"model_tier": "", "sandbox_type": "", "mode": "work", "auto_fix": False}
        defaults |= {"next_run_at": None, "last_run_at": None, "last_session_id": None}
        defaults |= {"last_status": None, "git_repo": "", "git_branch": ""}
        op.bulk_insert(missions, [{**defaults, **row} for row in rows])


def upgrade() -> None:
    _create_tables()
    is_postgres = op.get_bind().dialect.name == "postgresql"
    if is_postgres:
        # The copy reads every organization's rows: the policies of the old tables would hide
        # them from anyone but the schema owner, which is who runs migrations.
        _carry_over()
        for table in _TABLES:
            op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;")
            op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY;")
            op.execute(
                f"""
                CREATE POLICY tenant_isolation_{table} ON {table}
                    FOR ALL
                    USING (org_id = current_setting('app.current_org_id', true));
                """
            )
    else:
        _carry_over()
    op.drop_table("triggers")
    op.drop_table("scheduled_missions")


def _recreate_old_tables() -> None:
    op.create_table(
        "scheduled_missions",
        sa.Column("id", sa.String(length=64), primary_key=True, nullable=False),
        _org_column(),
        sa.Column(
            "user_id",
            sa.String(length=64),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("cron", sa.String(length=120), nullable=False),
        sa.Column("timezone", sa.String(length=64), nullable=False, server_default="UTC"),
        sa.Column("agent_id", sa.String(length=64), nullable=True),
        sa.Column("git_repo", sa.String(length=255), nullable=True),
        sa.Column("git_branch", sa.String(length=255), nullable=True),
        sa.Column("permission_mode", sa.String(length=16), nullable=True),
        sa.Column("slack_channel", sa.String(length=64), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("next_run_at", sa.BigInteger(), nullable=True, index=True),
        sa.Column("last_run_at", sa.BigInteger(), nullable=True),
        sa.Column("last_session_id", sa.String(length=64), nullable=True),
        sa.Column("last_status", sa.String(length=32), nullable=True),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
    )
    op.create_table(
        "triggers",
        sa.Column("id", sa.String(length=64), primary_key=True, nullable=False),
        _org_column(),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("source", sa.String(length=40), nullable=False, index=True),
        sa.Column("filter", _json(), nullable=False),
        sa.Column("agent_id", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("permission_mode", sa.String(length=16), nullable=False, server_default=""),
        sa.Column("model_tier", sa.String(length=16), nullable=False, server_default=""),
        sa.Column("sandbox_type", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("mode", sa.String(length=16), nullable=False, server_default="work"),
        sa.Column("auto_fix", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("instructions", sa.Text(), nullable=False, server_default=""),
        sa.Column("schedule", sa.String(length=120), nullable=False, server_default=""),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_by", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
    )


def _carry_back() -> None:
    """What the old tables can hold: a personal mission on one schedule (and its first Slack
    report), and an organization mission with one trigger. Anything else has no older form."""
    bind = op.get_bind()
    for row in bind.execute(sa.text("SELECT * FROM missions")).mappings():
        triggers = row["triggers"] or []
        if len(triggers) != 1:
            continue
        trigger = triggers[0]
        chat = next((d for d in row["deliveries"] or [] if d.get("kind") == "chat"), None)
        if row["scope"] == "user" and trigger.get("source") == "schedule":
            bind.execute(
                sa.text(
                    "INSERT INTO scheduled_missions (id, org_id, user_id, name, prompt, cron, "
                    "timezone, agent_id, git_repo, git_branch, permission_mode, slack_channel, "
                    "enabled, next_run_at, created_at) VALUES (:id, :org_id, :user_id, :name, "
                    ":prompt, :cron, :timezone, :agent_id, :git_repo, :git_branch, :mode, "
                    ":channel, :enabled, :next_run_at, :created_at)"
                ),
                {
                    **{k: row[k] for k in ("id", "org_id", "user_id", "name", "enabled")},
                    "prompt": row["instructions"],
                    "cron": trigger.get("schedule", ""),
                    "timezone": trigger.get("timezone", "UTC"),
                    "agent_id": row["agent_id"] or None,
                    "git_repo": row["git_repo"] or None,
                    "git_branch": row["git_branch"] or None,
                    "mode": row["permission_mode"] or None,
                    "channel": chat.get("channel") if chat else None,
                    "next_run_at": row["next_run_at"],
                    "created_at": row["created_at"],
                },
            )
        elif row["scope"] == "org":
            bind.execute(
                sa.text(
                    "INSERT INTO triggers (id, org_id, name, source, filter, agent_id, "
                    "permission_mode, model_tier, sandbox_type, mode, auto_fix, instructions, "
                    "schedule, enabled, created_by, created_at, updated_at) VALUES (:id, "
                    ":org_id, :name, :source, CAST(:filter AS JSONB), :agent_id, :mode_p, "
                    ":model_tier, :sandbox_type, :mode, :auto_fix, :instructions, :schedule, "
                    ":enabled, :created_by, :created_at, :updated_at)"
                ),
                {
                    **{
                        k: row[k]
                        for k in (
                            "id", "org_id", "name", "agent_id", "model_tier", "sandbox_type",
                            "mode", "auto_fix", "instructions", "enabled", "created_by",
                            "created_at", "updated_at",
                        )
                    },
                    "source": trigger.get("source", ""),
                    "filter": json.dumps(trigger.get("filter") or {}),
                    "mode_p": row["permission_mode"],
                    "schedule": trigger.get("schedule", ""),
                },
            )  # fmt: skip


def downgrade() -> None:
    _recreate_old_tables()
    if op.get_bind().dialect.name == "postgresql":
        _carry_back()
        for table in ("scheduled_missions", "triggers"):
            op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;")
            op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY;")
            op.execute(
                f"""
                CREATE POLICY tenant_isolation_{table} ON {table}
                    FOR ALL
                    USING (org_id = current_setting('app.current_org_id', true));
                """
            )
    for table in reversed(_TABLES):
        op.drop_table(table)

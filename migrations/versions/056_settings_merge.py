"""Move stored settings to the merged keys, and budgets into spend limits.

Revision ID: 056_settings_merge
Revises: 055_spend_and_grants
Create Date: 2026-10-29 18:30:00.000000

Settings:

* ``routing.{mode, fast_model, economy_model}`` -> ``models.{routing_mode, fast_model,
  economy_model}``; ``governance.{provider_allowlist, zero_retention, region}`` -> ``models``;
  ``budgets.byok_policy`` -> ``models.member_keys`` (``user_keys`` -> ``preferred``, otherwise
  ``off``; under ``platform_only`` the organization's own credentials become restricted to
  the agents bound to them, so the default chain keeps using the platform's keys);
* ``github.pr_creation_policy`` -> ``github.publish_as`` and ``github.commit_signing_mode`` ->
  ``github.signing``; ``commit_authorship_policy`` and members' ``commit_as_me`` are dropped
  (the signing mode decides now) and logged;
* ``governance.allow_bypass_permissions`` -> the ``access.use_bypass_mode`` capability
  (owners, administrators and members when it was on);
* ``slack.default_agent_persona`` is dropped (logged): the bot names its agent.

Budgets: ``budgets.monthly_budget_usd`` becomes an organization monthly block limit,
``member_monthly_budget_usd`` a monthly block limit for each member, ``session_budget_usd`` a
per-session limit, all with the old ``warn_at_percent``; a Slack workspace's
``monthly_usd_cap`` becomes a ``slack_workspace`` monthly limit. Their counters start from this
month's spend in the usage ledger, so a cap that was nearly used stays nearly used.

Agents that Slack bots or channel profiles name get the grants that keep today's behaviour
(everyone, and guests, whom a bot answers before they pair).
"""

import json
import logging
import time
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa
from alembic import op

revision: str = "056_settings_merge"
down_revision: str | None = "055_spend_and_grants"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

logger = logging.getLogger("alembic.runtime.migration")

_MOVES: tuple[tuple[str, str, str, str], ...] = (
    ("routing", "mode", "models", "routing_mode"),
    ("routing", "fast_model", "models", "fast_model"),
    ("routing", "economy_model", "models", "economy_model"),
    ("governance", "provider_allowlist", "models", "provider_allowlist"),
    ("governance", "zero_retention", "models", "zero_retention"),
    ("governance", "region", "models", "region"),
)
_SIGNING = {"github_app": "github", "gpg_key": "member_gpg", "none": "none"}
_CAPABILITY_ROLES = ["owner", "admin", "member"]


def _json(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str) and value:
        loaded = json.loads(value)
        return dict(loaded) if isinstance(loaded, dict) else {}
    return {}


def _list(value: Any) -> list[str]:
    if isinstance(value, str) and value:
        value = json.loads(value)
    return [str(item) for item in value] if isinstance(value, list) else []


def _month_start() -> int:
    now = datetime.now(tz=UTC)
    return int(now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).timestamp())


class _Orgs:
    """Every organization's stored settings, rewritten in memory, then saved."""

    def __init__(self, bind: Any) -> None:
        self.bind = bind
        self.rows: dict[tuple[str, str], tuple[dict[str, Any], list[str]]] = {}
        for org_id, domain, config, locked in bind.execute(
            sa.text("SELECT org_id, domain, config, locked_keys FROM org_settings")
        ):
            self.rows[(org_id, domain)] = (_json(config), _list(locked))

    def orgs(self) -> set[str]:
        return {org for org, _ in self.rows}

    def get(self, org_id: str, domain: str) -> dict[str, Any]:
        return self.rows.setdefault((org_id, domain), ({}, []))[0]

    def locked(self, org_id: str, domain: str) -> list[str]:
        return self.rows.setdefault((org_id, domain), ({}, []))[1]

    def save(self) -> None:
        now = int(time.time())
        for (org_id, domain), (config, locked) in self.rows.items():
            updated = self.bind.execute(
                sa.text(
                    "UPDATE org_settings SET config = CAST(:config AS jsonb), "
                    "locked_keys = CAST(:locked AS jsonb), updated_at = :now "
                    "WHERE org_id = :org AND domain = :domain"
                ),
                {
                    "config": json.dumps(config),
                    "locked": json.dumps(locked),
                    "now": now,
                    "org": org_id,
                    "domain": domain,
                },
            )
            if updated.rowcount == 0 and config:
                self.bind.execute(
                    sa.text(
                        "INSERT INTO org_settings (id, org_id, domain, config, locked_keys, "
                        "created_at, updated_at) VALUES (:id, :org, :domain, CAST(:config AS jsonb), "
                        "CAST(:locked AS jsonb), :now, :now)"
                    ),
                    {
                        "id": f"org_cfg_{uuid.uuid4().hex[:12]}",
                        "org": org_id,
                        "domain": domain,
                        "config": json.dumps(config),
                        "locked": json.dumps(locked),
                        "now": now,
                    },
                )


def _move_keys(orgs: _Orgs, org_id: str) -> None:
    for domain, key, new_domain, new_key in _MOVES:
        old = orgs.get(org_id, domain)
        if key in old:
            orgs.get(org_id, new_domain).setdefault(new_key, old.pop(key))


def _member_keys(orgs: _Orgs, org_id: str, bind: Any) -> None:
    budgets = orgs.get(org_id, "budgets")
    if "byok_policy" not in budgets:
        return
    policy = budgets.pop("byok_policy")
    orgs.get(org_id, "models").setdefault(
        "member_keys", "preferred" if policy == "user_keys" else "off"
    )
    if policy == "platform_only":
        bind.execute(
            sa.text(
                "UPDATE provider_credentials SET restricted_to_agents = true, "
                "default_for_provider = false WHERE org_id = :org AND owner_kind = 'org'"
            ),
            {"org": org_id},
        )


def _git(orgs: _Orgs, org_id: str) -> None:
    github = orgs.get(org_id, "github")
    if "pr_creation_policy" in github:
        github.setdefault(
            "publish_as", "bot" if github.pop("pr_creation_policy") == "as_bot" else "member"
        )
    if "commit_signing_mode" in github:
        github.setdefault("signing", _SIGNING.get(str(github.pop("commit_signing_mode")), "none"))
    if github.pop("commit_authorship_policy", None) not in (None, "co_authored"):
        logger.warning(
            "Organization %s set a commit authorship policy; it is no longer used", org_id
        )
    locked = orgs.locked(org_id, "github")
    retired = {"pr_creation_policy", "commit_authorship_policy", "commit_signing_mode"}
    if retired & set(locked):
        locked[:] = sorted({k for k in locked if k not in retired} | {"publish_as"})


def _capabilities(orgs: _Orgs, org_id: str) -> None:
    governance = orgs.get(org_id, "governance")
    if governance.pop("allow_bypass_permissions", False):
        orgs.get(org_id, "access").setdefault("use_bypass_mode", list(_CAPABILITY_ROLES))
    if orgs.get(org_id, "slack").pop("default_agent_persona", ""):
        logger.warning(
            "Organization %s named a default Slack agent; choose it on the bot instead", org_id
        )


def _limit(
    bind: Any, org_id: str, kind: str, subject: str | None, period: str, amount: float, warn: int
) -> str:
    limit_id = f"lim_{uuid.uuid4().hex[:16]}"
    now = int(time.time())
    bind.execute(
        sa.text(
            "INSERT INTO spend_limits (id, org_id, subject_kind, subject_id, member_id, period, "
            "amount_usd, mode, warn_at_percent, is_ceiling, created_by, created_at, updated_at) "
            "VALUES (:id, :org, :kind, :subject, NULL, :period, :amount, 'block', :warn, false, "
            "'migration', :now, :now)"
        ),
        {
            "id": limit_id,
            "org": org_id,
            "kind": kind,
            "subject": subject,
            "period": period,
            "amount": amount,
            "warn": warn,
            "now": now,
        },
    )
    return limit_id


def _seed(bind: Any, org_id: str, limit_id: str, key: str, spent: float) -> None:
    if spent <= 0:
        return
    bind.execute(
        sa.text(
            "INSERT INTO spend_counters (limit_id, subject_key, window_start, org_id, spent_usd, "
            "reserved_usd, updated_at) VALUES (:lid, :key, :start, :org, :spent, 0, :now)"
        ),
        {
            "lid": limit_id,
            "key": key,
            "start": _month_start(),
            "org": org_id,
            "spent": spent,
            "now": int(time.time()),
        },
    )


def _org_month_spend(
    bind: Any, org_id: str, user_column: str | None = None
) -> list[tuple[str, float]]:
    group = f", {user_column}" if user_column else ""
    rows = bind.execute(
        sa.text(
            f"SELECT COALESCE(SUM(cost_usd), 0){group} FROM usage_events WHERE org_id = :org "
            f"AND created_at >= :since AND key_source IN ('platform', 'org')"
            + (f" GROUP BY {user_column}" if user_column else "")
        ),
        {"org": org_id, "since": _month_start()},
    ).all()
    return [(str(row[1]) if user_column else "org", float(row[0] or 0)) for row in rows]


def _budgets(orgs: _Orgs, org_id: str, bind: Any) -> None:
    budgets = orgs.get(org_id, "budgets")
    warn = int(budgets.pop("warn_at_percent", 80) or 80)
    monthly = float(budgets.pop("monthly_budget_usd", 0) or 0)
    member = float(budgets.pop("member_monthly_budget_usd", 0) or 0)
    session = float(budgets.pop("session_budget_usd", 0) or 0)
    if monthly > 0:
        limit_id = _limit(bind, org_id, "org", None, "month", monthly, warn)
        for key, spent in _org_month_spend(bind, org_id):
            _seed(bind, org_id, limit_id, key, spent)
    if member > 0:
        limit_id = _limit(bind, org_id, "member", None, "month", member, warn)
        for user_id, spent in _org_month_spend(bind, org_id, "user_id"):
            _seed(bind, org_id, limit_id, user_id, spent)
    if session > 0:
        _limit(bind, org_id, "org", None, "session", session, warn)


def _slack_quotas(bind: Any) -> None:
    for team_id, org_id, cap in bind.execute(
        sa.text(
            "SELECT team_id, org_id, monthly_usd_cap FROM slack_workspace_quotas "
            "WHERE monthly_usd_cap > 0"
        )
    ).all():
        _limit(bind, org_id, "slack_workspace", team_id, "month", float(cap), 80)
    op.drop_column("slack_workspace_quotas", "monthly_usd_cap")
    op.drop_column("slack_workspace_quotas", "current_month_spend_usd")


def _bot_agent_grants(bind: Any) -> None:
    """Agents Slack serves keep serving members and guests, as they did before grants."""
    named: set[tuple[str, str]] = set()
    for org_id, agent_id, policy in bind.execute(
        sa.text("SELECT org_id, agent_id, policy FROM slack_bots")
    ).all():
        if agent_id:
            named.add((org_id, agent_id))
        for profile in (_json(policy).get("channel_profiles") or {}).values():
            if isinstance(profile, dict) and profile.get("agent_persona"):
                named.add((org_id, str(profile["agent_persona"])))
    now = int(time.time())
    for org_id, agent_id in sorted(named):
        for kind in ("everyone", "guests"):
            bind.execute(
                sa.text(
                    "INSERT INTO agent_grants (id, org_id, agent_id, principal_kind, principal_id, "
                    "created_by, created_at) VALUES (:id, :org, :agent, :kind, '', 'migration', "
                    ":now) ON CONFLICT DO NOTHING"
                ),
                {
                    "id": f"agr_{uuid.uuid4().hex[:16]}",
                    "org": org_id,
                    "agent": agent_id,
                    "kind": kind,
                    "now": now,
                },
            )


def _member_overrides(bind: Any) -> None:
    for row_id, domain, overrides in bind.execute(
        sa.text(
            "SELECT id, domain, overrides FROM user_settings "
            "WHERE domain IN ('github', 'user_preferences')"
        )
    ).all():
        values = _json(overrides)
        dropped = [
            k
            for k in (
                "commit_authorship_policy",
                "pr_creation_policy",
                "commit_signing_mode",
                "commit_as_me",
            )
            if values.pop(k, None) is not None
        ]
        if dropped:
            logger.warning(
                "Dropped retired member settings %s (%s row %s)", dropped, domain, row_id
            )
            bind.execute(
                sa.text("UPDATE user_settings SET overrides = CAST(:o AS jsonb) WHERE id = :id"),
                {"o": json.dumps(values), "id": row_id},
            )


def upgrade() -> None:
    bind = op.get_bind()
    orgs = _Orgs(bind)
    for org_id in sorted(orgs.orgs()):
        _move_keys(orgs, org_id)
        _member_keys(orgs, org_id, bind)
        _git(orgs, org_id)
        _capabilities(orgs, org_id)
        _budgets(orgs, org_id, bind)
    orgs.save()
    bind.execute(sa.text("DELETE FROM org_settings WHERE domain = 'routing'"))
    _member_overrides(bind)
    _slack_quotas(bind)
    _bot_agent_grants(bind)


def downgrade() -> None:
    # The merged keys are still read under their old names for one release
    # (``config_engine.registry.LEGACY_KEYS``); this only restores the dropped columns.
    op.add_column(
        "slack_workspace_quotas",
        sa.Column("monthly_usd_cap", sa.Float(), nullable=False, server_default="0"),
    )
    op.add_column(
        "slack_workspace_quotas",
        sa.Column("current_month_spend_usd", sa.Float(), nullable=False, server_default="0"),
    )

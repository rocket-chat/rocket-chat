"""Store each turn trace's ``tokens.usage.cost_usd`` in dollars.

Revision ID: 042_turn_trace_cost_in_dollars
Revises: 041_backfill_msg_timestamps
Create Date: 2026-10-20 18:00:00.000000

Traces written before this revision kept the turn total in integer micro-dollars under a key
that says dollars, while each call in ``tokens.calls`` was already in dollars. The total is now
written in dollars; this converts the rows already stored.
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "042_turn_trace_cost_in_dollars"
down_revision: str | None = "041_backfill_msg_timestamps"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_HAS_COST = "jsonb_typeof(tokens #> '{usage,cost_usd}') = 'number'"


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute(
        f"""
        UPDATE turn_traces
        SET tokens = jsonb_set(
            tokens,
            '{{usage,cost_usd}}',
            to_jsonb(round((tokens #>> '{{usage,cost_usd}}')::numeric / 1000000, 6))
        )
        WHERE {_HAS_COST};
        """
    )


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute(
        f"""
        UPDATE turn_traces
        SET tokens = jsonb_set(
            tokens,
            '{{usage,cost_usd}}',
            to_jsonb(round((tokens #>> '{{usage,cost_usd}}')::numeric * 1000000)::bigint)
        )
        WHERE {_HAS_COST};
        """
    )

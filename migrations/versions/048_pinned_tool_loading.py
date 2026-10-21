"""An agent that pinned tools under "on demand" keeps loading only those: the "pinned" mode.

Revision ID: 048_pinned_tool_loading
Revises: 047_api_tokens
Create Date: 2026-10-21 12:00:00.000000

"On demand" used to load only an agent's pinned tools once it had any, and the platform's own
tools otherwise. It now always loads the platform's own tools and keeps pinned MCP tools loaded;
"only the pinned tools" is its own mode, ``pinned``. An agent that had pinned tools is moved to
it, so what its steps carry does not change: the agents whose own mode is ``on_demand``, and the
ones that inherit an organization default of ``on_demand``.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "048_pinned_tool_loading"
down_revision: str | None = "047_api_tokens"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_HAS_PINS = "jsonb_array_length(p.active_tools::jsonb) > 0"
_ORG_IS_ON_DEMAND = """
    EXISTS (
        SELECT 1 FROM org_settings o
        WHERE o.org_id = p.org_id AND o.domain = 'general'
          AND o.config::jsonb ->> 'tool_loading' = 'on_demand'
    )
"""


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute(
        f"""
        UPDATE agent_personas p
        SET tool_loading = 'pinned'
        WHERE {_HAS_PINS}
          AND (p.tool_loading = 'on_demand' OR (p.tool_loading IS NULL AND {_ORG_IS_ON_DEMAND}));
        """
    )


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute(
        "UPDATE agent_personas SET tool_loading = 'on_demand' WHERE tool_loading = 'pinned';"
    )

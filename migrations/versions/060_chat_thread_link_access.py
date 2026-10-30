"""Sessions of public Slack threads are readable by the organization from their link.

Revision ID: 060_chat_thread_link_access
Revises: 059_automation
Create Date: 2026-10-30 18:00:00.000000

Slack thread sessions were created shared on the record but stored private (the store reads
sharing from the metadata), so only their guest owner and administrators could open them. A
public thread's session now carries ``link_access``: any member of the organization may read it
from "Open in Rocket Chat", and it joins the list of the members who take part in the thread.
Direct messages and private requests stay private.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "060_chat_thread_link_access"
down_revision: str | None = "059_automation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute(
        """
        UPDATE sessions
           SET metadata_json = (metadata_json::jsonb || '{"link_access": true}'::jsonb)::json
         WHERE source = 'slack'
           AND COALESCE((metadata_json::jsonb ->> 'slack_is_dm')::boolean, false) = false
           AND COALESCE((metadata_json::jsonb ->> 'slack_private')::boolean, false) = false
        """
    )


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute(
        "UPDATE sessions SET metadata_json = (metadata_json::jsonb - 'link_access')::json "
        "WHERE source = 'slack'"
    )

"""Where a Slack bot's private DM threads find their session again (``slack_private_threads``).

A stop or a reply in a private DM thread carries only its DM channel and thread; the row names
the private session it runs, on any replica and after a restart.
"""

from __future__ import annotations

import time

from slack_assistant.private_threads import PrivateThread
from sqlalchemy import select

from api.db.models import SlackPrivateThreadModel
from api.db.session import DatabaseSessionManager


class SqlPrivateThreads:
    """The ``PrivateThreadStore`` of a deployment with a database. Rows are the tenant's (RLS)."""

    def __init__(self, db_manager: DatabaseSessionManager) -> None:
        self._db = db_manager

    async def remember(self, org_id: str, thread: PrivateThread) -> None:
        async with self._db.session(tenant_org_id=org_id) as session:
            await session.merge(
                SlackPrivateThreadModel(
                    org_id=org_id,
                    dm_channel_id=thread.dm_channel_id,
                    dm_thread_ts=thread.dm_thread_ts,
                    session_id=thread.session_id,
                    slack_user_id=thread.slack_user_id,
                    origin_channel_id=thread.origin_channel_id,
                    origin_thread_ts=thread.origin_thread_ts,
                    origin_channel_type=thread.origin_channel_type,
                    created_at=int(time.time()),
                )
            )

    async def find(
        self, org_id: str, dm_channel_id: str, dm_thread_ts: str
    ) -> PrivateThread | None:
        async with self._db.session(tenant_org_id=org_id) as session:
            row = (
                await session.execute(
                    select(SlackPrivateThreadModel).where(
                        SlackPrivateThreadModel.org_id == org_id,
                        SlackPrivateThreadModel.dm_channel_id == dm_channel_id,
                        SlackPrivateThreadModel.dm_thread_ts == dm_thread_ts,
                    )
                )
            ).scalar_one_or_none()
        if row is None:
            return None
        return PrivateThread(
            dm_channel_id=row.dm_channel_id,
            dm_thread_ts=row.dm_thread_ts,
            session_id=row.session_id,
            slack_user_id=row.slack_user_id,
            origin_channel_id=row.origin_channel_id,
            origin_thread_ts=row.origin_thread_ts,
            origin_channel_type=row.origin_channel_type,
        )

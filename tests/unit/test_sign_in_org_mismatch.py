"""A member whose row lives in another organization than sign-in places them is reported.

Row-level security hides that row, so the sign-in's insert collides with it. It used to be a
quiet warning while pairing and linked accounts failed later with unrelated messages.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any

import pytest
from api.routers import auth
from sqlalchemy.exc import IntegrityError


class _HiddenRow:
    """A database where the member's row exists, but in an organization RLS hides."""

    session_factory = object()

    @asynccontextmanager
    async def session(self, tenant_org_id: str | None = None) -> Any:
        class _Session:
            async def get(self, *_: Any) -> None:
                return None

            async def execute(self, *_: Any) -> Any:
                class _Count:
                    def scalar_one_or_none(self) -> int:
                        return 3

                return _Count()

            def add(self, *_: Any) -> None:
                return None

            async def flush(self) -> None:
                raise IntegrityError("INSERT", {}, Exception("duplicate key users_pkey"))

        yield _Session()


async def test_a_member_stranded_in_another_organization_is_logged_as_an_error(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.ERROR, logger=auth.logger.name):
        roles = await auth._ensure_db_user_and_org(
            _HiddenRow(),  # type: ignore[arg-type]
            "usr_ada",
            "default_org",
            "ada@example.com",
            "Ada",
        )

    assert roles == ["developer"]
    assert "usr_ada already exists in another organization than default_org" in caplog.text

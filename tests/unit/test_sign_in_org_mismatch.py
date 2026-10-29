"""A member whose row lives in another organization than sign-in places them is refused.

``users`` has no row-level security, so the row is found; applying its role would let one
organization's administrator sign in as an administrator of another. It used to be a quiet
warning while the sign-in went ahead.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any

import pytest
from api.db.models import UserModel
from api.routers import auth


class _RowElsewhere:
    """A database where the member's row exists, as an administrator of another organization."""

    session_factory = object()

    @asynccontextmanager
    async def session(self, tenant_org_id: str | None = None) -> Any:
        class _Session:
            async def get(self, model: Any, _id: str) -> Any:
                if model is UserModel:
                    return UserModel(id="usr_ada", org_id="other_org", email="a@x", role="admin")
                return object()

            def add(self, *_: Any) -> None:
                return None

            async def flush(self) -> None:
                return None

        yield _Session()


async def test_a_member_stored_in_another_organization_is_refused_and_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with (
        caplog.at_level(logging.ERROR, logger=auth.logger.name),
        pytest.raises(auth.MemberOfAnotherOrganization),
    ):
        await auth._ensure_db_user_and_org(
            _RowElsewhere(),  # type: ignore[arg-type]
            "usr_ada",
            "default_org",
            "ada@example.com",
            "Ada",
        )

    assert "member usr_ada belongs to another organization than default_org" in caplog.text

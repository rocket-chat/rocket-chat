"""A person who belongs to another organization and signs in to this one becomes a member
here, and their role elsewhere never applies here.

``users`` is a person's global identity; roles live in ``memberships``. Applying the role of a
person's home organization would let one organization's administrator sign in as an
administrator of another.
"""

from __future__ import annotations

from typing import Any

import pytest
from api.db.models import OrganizationModel, UserModel
from api.routers import auth


class _Session:
    async def __aenter__(self) -> _Session:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def get(self, model: Any, key: str) -> Any:
        return OrganizationModel(id=key, name=key, slug=key)

    def add(self, *_: Any) -> None:
        return None

    async def flush(self) -> None:
        return None


class _Db:
    session_factory = True

    def session(self, **_: Any) -> _Session:
        return _Session()


async def test_an_administrator_elsewhere_signs_in_here_as_a_member(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("INITIAL_ADMIN_EMAIL", raising=False)
    monkeypatch.delenv("ADMIN_EMAILS", raising=False)
    memberships = {("usr_ada", "other_org"): "admin", ("usr_owner", "default_org"): "owner"}

    async def person(session: Any, user_id: str, *_: Any) -> UserModel:
        return UserModel(id=user_id, org_id="other_org", email="a@x", name="Ada", role="admin")

    async def membership(session: Any, user_id: str, org_id: str) -> Any:
        role = memberships.get((user_id, org_id))
        return None if role is None else type("M", (), {"role": role})()

    async def count(session: Any, org_id: str, exclude: tuple[str, ...] = ()) -> int:
        return sum(1 for (_, org) in memberships if org == org_id)

    async def ensure(session: Any, user_id: str, org_id: str, role: str) -> Any:
        memberships[(user_id, org_id)] = role
        return type("M", (), {"role": role})()

    monkeypatch.setattr(auth, "_person", person)
    monkeypatch.setattr(auth, "membership_row", membership)
    monkeypatch.setattr(auth, "member_count", count)
    monkeypatch.setattr(auth, "ensure_membership", ensure)

    _, roles = await auth._ensure_db_user_and_org(
        _Db(),  # type: ignore[arg-type]
        "usr_ada",
        "default_org",
        "ada@example.com",
        "Ada",
    )

    assert roles == ["member"]
    assert memberships[("usr_ada", "default_org")] == "member"
    assert memberships[("usr_ada", "other_org")] == "admin", "the other membership is untouched"

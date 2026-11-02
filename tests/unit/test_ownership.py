"""Who sees, changes and moves an object owned by a member, a team or the organization."""

from __future__ import annotations

import pytest

from specifications.interfaces.ownership import (
    Owner,
    Viewer,
    can_manage,
    can_move,
    can_see,
    owner_problem,
)

ALICE = Owner("user", user_id="alice")
PLATFORM = Owner("team", team_id="platform")
PAYMENTS = Owner("team", team_id="payments")
ORG = Owner("org")

MEMBER = Viewer("alice")
IN_PLATFORM = Viewer("bob", teams=frozenset({"platform"}))
PLATFORM_ADMIN = Viewer("carol", teams=frozenset({"platform"}), admin_teams=frozenset({"platform"}))
ORG_ADMIN = Viewer("root", is_org_admin=True)


@pytest.mark.parametrize(
    ("viewer", "owner", "sees"),
    [
        (MEMBER, ALICE, True),
        (IN_PLATFORM, ALICE, False),
        (ORG_ADMIN, ALICE, False),
        (MEMBER, PLATFORM, False),
        (IN_PLATFORM, PLATFORM, True),
        (IN_PLATFORM, PAYMENTS, False),
        (ORG_ADMIN, PLATFORM, True),
        (MEMBER, ORG, True),
    ],
)
def test_who_sees_an_object(viewer: Viewer, owner: Owner, sees: bool) -> None:
    assert can_see(viewer, owner) is sees


@pytest.mark.parametrize(
    ("viewer", "owner", "manages"),
    [
        (MEMBER, ALICE, True),
        (IN_PLATFORM, ALICE, False),
        (ORG_ADMIN, ALICE, True),
        (IN_PLATFORM, PLATFORM, False),
        (PLATFORM_ADMIN, PLATFORM, True),
        (PLATFORM_ADMIN, PAYMENTS, False),
        (ORG_ADMIN, PAYMENTS, True),
        (PLATFORM_ADMIN, ORG, False),
        (ORG_ADMIN, ORG, True),
    ],
)
def test_who_manages_an_object(viewer: Viewer, owner: Owner, manages: bool) -> None:
    assert can_manage(viewer, owner) is manages


def test_a_team_administrator_moves_their_own_object_into_the_team_and_back() -> None:
    carol = Owner("user", user_id="carol")
    assert can_move(PLATFORM_ADMIN, carol, PLATFORM)
    assert can_move(PLATFORM_ADMIN, PLATFORM, carol)


def test_a_move_needs_the_right_to_manage_both_owners() -> None:
    assert not can_move(PLATFORM_ADMIN, PLATFORM, ORG)
    assert not can_move(PLATFORM_ADMIN, PLATFORM, PAYMENTS)
    assert not can_move(IN_PLATFORM, Owner("user", user_id="bob"), PLATFORM)
    assert can_move(ORG_ADMIN, PLATFORM, ORG)


def test_a_member_never_hands_an_object_to_another_member() -> None:
    assert not can_move(PLATFORM_ADMIN, PLATFORM, ALICE)
    assert can_move(ORG_ADMIN, PLATFORM, ALICE)


@pytest.mark.parametrize(
    ("scope", "user_id", "team_id", "problem"),
    [
        ("user", "alice", None, None),
        ("team", None, "platform", None),
        ("org", None, None, None),
        ("group", None, None, "Unknown owner"),
        ("user", None, None, "names its member"),
        ("team", "alice", "platform", "names its team"),
        ("org", None, "platform", "neither"),
    ],
)
def test_an_owner_must_be_well_formed(
    scope: str, user_id: str | None, team_id: str | None, problem: str | None
) -> None:
    found = owner_problem(scope, user_id, team_id)
    assert (found is None) if problem is None else (problem in (found or ""))

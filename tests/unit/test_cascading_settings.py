"""Unit tests for CascadingSettingsResolver (System -> Org -> User with Admin Lockouts)."""

import pytest
from config_engine import CascadingSettingsResolver


@pytest.fixture
def resolver() -> CascadingSettingsResolver:
    return CascadingSettingsResolver()


def test_resolve_system_defaults(resolver: CascadingSettingsResolver) -> None:
    """Ensure system defaults are returned when no org or user overrides are present."""
    res = resolver.resolve("github")
    assert res.domain == "github"
    assert res.effective["commit_authorship_policy"] == "co_authored"
    assert res.effective["pr_creation_policy"] == "as_user"
    assert res.field_status["commit_authorship_policy"] == "org_default"
    assert res.locked_keys == []
    assert res.user_overrides == {}


def test_resolve_org_overrides(resolver: CascadingSettingsResolver) -> None:
    """Ensure organization configuration modifies defaults."""
    org_conf = {
        "commit_authorship_policy": "bot_only",
        "bot_author_name": "Acme CI Bot",
    }
    res = resolver.resolve("github", org_config=org_conf)
    assert res.effective["commit_authorship_policy"] == "bot_only"
    assert res.effective["bot_author_name"] == "Acme CI Bot"
    assert res.effective["pr_creation_policy"] == "as_user"  # Inherited from system
    assert res.field_status["commit_authorship_policy"] == "org_default"


def test_resolve_user_overrides_unlocked(resolver: CascadingSettingsResolver) -> None:
    """Ensure unlocked keys allow user overrides with 'overridden' status."""
    org_conf = {"commit_authorship_policy": "co_authored"}
    user_over = {"commit_authorship_policy": "user_only"}

    res = resolver.resolve(
        "github",
        org_config=org_conf,
        locked_keys=[],
        user_overrides=user_over,
    )
    assert res.effective["commit_authorship_policy"] == "user_only"
    assert res.field_status["commit_authorship_policy"] == "overridden"


def test_resolve_admin_lockout_enforcement(resolver: CascadingSettingsResolver) -> None:
    """Ensure locked keys reject user overrides and retain org values."""
    org_conf = {"commit_authorship_policy": "co_authored", "pr_creation_policy": "as_bot"}
    locked = ["commit_authorship_policy"]
    user_over = {
        "commit_authorship_policy": "user_only",  # Locked -> Must be ignored
        "pr_creation_policy": "as_user",  # Unlocked -> Must be applied
    }

    res = resolver.resolve(
        "github",
        org_config=org_conf,
        locked_keys=locked,
        user_overrides=user_over,
    )
    # Locked key retains org value
    assert res.effective["commit_authorship_policy"] == "co_authored"
    assert res.field_status["commit_authorship_policy"] == "locked"

    # Unlocked key receives user override
    assert res.effective["pr_creation_policy"] == "as_user"
    assert res.field_status["pr_creation_policy"] == "overridden"


def test_filter_valid_user_overrides(resolver: CascadingSettingsResolver) -> None:
    """Verify filter_valid_user_overrides separates allowed from rejected keys."""
    requested = {
        "commit_authorship_policy": "user_only",
        "pr_creation_policy": "as_user",
    }
    locked = ["commit_authorship_policy"]

    allowed, rejected = resolver.filter_valid_user_overrides("github", requested, locked)
    assert allowed == {"pr_creation_policy": "as_user"}
    assert rejected == ["commit_authorship_policy"]


def test_resolved_settings_to_dict(resolver: CascadingSettingsResolver) -> None:
    """Verify dictionary serialization for JSON API response."""
    res = resolver.resolve("general")
    data = res.to_dict()
    assert data["domain"] == "general"
    assert "effective" in data
    assert "field_status" in data
    assert "locked_keys" in data
    assert data["effective"]["session_privacy_default"] == "private"

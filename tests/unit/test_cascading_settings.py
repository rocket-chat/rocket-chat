"""Unit tests for CascadingSettingsResolver (platform -> org -> member, by policy and lock)."""

import pytest
from config_engine import CascadingSettingsResolver


@pytest.fixture
def resolver() -> CascadingSettingsResolver:
    return CascadingSettingsResolver()


def test_resolve_system_defaults(resolver: CascadingSettingsResolver) -> None:
    """Platform defaults apply when neither the organization nor the member set a value."""
    res = resolver.resolve("github")
    assert res.domain == "github"
    assert res.effective["publish_as"] == "member"
    assert res.effective["signing"] == "github"
    assert res.provenance["publish_as"].source == "platform"
    assert res.provenance["publish_as"].locked_by is None
    assert res.locked_keys == []
    assert res.user_overrides == {}


def test_resolve_org_overrides(resolver: CascadingSettingsResolver) -> None:
    """The organization's values replace the platform defaults."""
    org_conf = {"publish_as": "bot", "bot_author_name": "Acme CI Bot"}
    res = resolver.resolve("github", org_config=org_conf)
    assert res.effective["publish_as"] == "bot"
    assert res.effective["bot_author_name"] == "Acme CI Bot"
    assert res.effective["signing"] == "github"  # Inherited from the platform
    assert res.provenance["publish_as"].source == "org"
    assert res.provenance["signing"].source == "platform"


def test_resolve_user_overrides_unlocked(resolver: CascadingSettingsResolver) -> None:
    """An unlocked ``org_default`` key takes the member's value."""
    res = resolver.resolve(
        "github",
        org_config={"publish_as": "bot"},
        locked_keys=[],
        user_overrides={"publish_as": "member"},
    )
    assert res.effective["publish_as"] == "member"
    assert res.provenance["publish_as"].source == "member"


def test_an_organization_only_key_ignores_a_member_value(
    resolver: CascadingSettingsResolver,
) -> None:
    res = resolver.resolve(
        "github", org_config={"signing": "member_gpg"}, user_overrides={"signing": "none"}
    )
    assert res.effective["signing"] == "member_gpg"
    assert res.provenance["signing"].source == "org"


def test_resolve_admin_lockout_enforcement(resolver: CascadingSettingsResolver) -> None:
    """A locked key keeps the organization's value and says the member's was refused."""
    res = resolver.resolve(
        "github",
        org_config={"publish_as": "bot"},
        locked_keys=["publish_as"],
        user_overrides={"publish_as": "member"},
    )
    assert res.effective["publish_as"] == "bot"
    assert res.provenance["publish_as"].locked_by == "org"
    assert res.provenance["publish_as"].refused == ("member",)


def test_filter_valid_user_overrides(resolver: CascadingSettingsResolver) -> None:
    """Verify filter_valid_user_overrides separates allowed from rejected keys."""
    requested = {"publish_as": "bot", "default_branch": "develop"}

    allowed, rejected = resolver.filter_valid_user_overrides("github", requested, ["publish_as"])
    assert allowed == {"default_branch": "develop"}
    assert rejected == ["publish_as"]


def test_resolved_settings_to_dict(resolver: CascadingSettingsResolver) -> None:
    """Verify dictionary serialization for JSON API response."""
    res = resolver.resolve("general")
    data = res.to_dict()
    assert data["domain"] == "general"
    assert "effective" in data
    assert data["provenance"]["session_privacy_default"] == {
        "source": "platform",
        "locked_by": None,
        "refused": [],
    }
    assert "field_status" not in data
    assert "locked_keys" in data
    assert data["effective"]["session_privacy_default"] == "private"

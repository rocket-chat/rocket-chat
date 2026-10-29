"""The GitHub App allowlist names repositories by owner: ``acme/api`` never admits ``evil/api``."""

from __future__ import annotations

import pytest
from api.services.git_credentials import _target_repositories, app_may_act_on


@pytest.mark.setting("github.app_token_target_repositories")
@pytest.mark.parametrize(
    "allowlist, repository, allowed",
    [
        ("acme/api", "acme/api", True),
        ("acme/api", "evil/api", False),
        ("Acme/API", "acme/api", True),
        ("acme/api, acme/web", "acme/web", True),
        ("api", "evil/api", True),  # a bare name (deprecated) still matches every owner
        ("all", "anyone/anything", True),
    ],
)
def test_the_app_acts_only_on_the_named_owner_and_repository(
    allowlist: str, repository: str, allowed: bool
) -> None:
    assert app_may_act_on({"app_token_target_repositories": allowlist}, repository) is allowed


def test_an_installation_token_is_scoped_to_its_owners_entries_only() -> None:
    allowlist = "acme/api, evil/tools, web"

    assert _target_repositories(allowlist, "acme") == ["api", "web"]
    assert _target_repositories(allowlist, "evil") == ["tools", "web"]
    assert _target_repositories("acme/api", "other") == []
    assert _target_repositories("all", "acme") is None

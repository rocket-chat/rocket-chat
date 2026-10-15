"""No GitHub credential is shared between organizations, and none reaches a tool result.

A deployment-wide ``GITHUB_TOKEN`` used to stand in for the GitHub App for any member who had
linked nothing, in every organization, and was handed to their sandboxes. It is not read any
more: an organization uses its mapped App installation or its member's own account.
"""

from __future__ import annotations

import pytest
from api.auth import git_gateway_token
from api.services.git_credentials import resolve_session_git_credentials
from api.services.redaction import RedactionPolicy, Redactor
from git_engine.config import GitEngineSettings
from git_engine.engine import GitEngine


async def test_a_deployment_token_in_the_environment_is_never_used(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_deployment_wide_" + "x" * 20)
    monkeypatch.setenv("GITHUB_PAT", "ghp_deployment_wide_" + "y" * 20)
    engine = GitEngine(driver=None, settings=GitEngineSettings.from_env())

    assert await engine.get_github_access_token(repo="acme/api") is None
    bundle = await resolve_session_git_credentials(
        user_id="member_without_link", org_id="org_a", git_engine=engine, repo="acme/api"
    )

    assert bundle.mode == "public" and bundle.token is None and bundle.user_token is None


def test_tokens_and_the_gateway_capability_are_redacted_from_tool_output() -> None:
    redactor = Redactor(RedactionPolicy())
    capability = git_gateway_token.mint("s1", "org", "u1")
    output = (
        "GITHUB_TOKEN=ghs_" + "a" * 36 + "\n"
        "token=github_pat_" + "b" * 40 + "\n"
        f"GIT_CONFIG_VALUE_3=X-Rocket-Git-Capability: {capability}\n"
    )

    result = redactor.redact(output)

    assert "ghs_" + "a" * 36 not in result.text
    assert "github_pat_" + "b" * 40 not in result.text
    assert capability not in result.text and result.count >= 3

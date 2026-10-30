"""A review comment is answered end to end: read the thread, fix, publish, reply on the thread."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from agent_core.tools import github_client
from api.services.git_credentials import GitCredentialBundle
from api.services.github_watches import next_step_hint, pending_events_notice
from api.services.settings import save_org_config
from api.turns import callbacks as turn_callbacks

from specifications.interfaces.agent import GithubAccess, GitPolicy
from specifications.interfaces.git import PublishResult, PublishSpec
from tests.turn_harness import run_turn
from tests.unit.test_github_work_tools import FakeGitHub


class _Engine:
    settings = SimpleNamespace(
        github_api_url="https://api.github.com", bot_author_name="Rocket", bot_author_email="r@x"
    )

    async def publish_branch(self, session_id: str, spec: PublishSpec) -> PublishResult:
        return PublishResult("acme/app", "fix/rename", "abc123def456", "git_push", commits=1)

    async def get_github_access_token(self, **kwargs: Any) -> str | None:
        return "ghs_app_write"


async def test_a_review_is_read_fixed_published_and_answered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeGitHub()
    real = httpx.AsyncClient
    monkeypatch.setattr(
        github_client.httpx,
        "AsyncClient",
        lambda **kwargs: real(transport=httpx.MockTransport(fake.handler), **kwargs),
    )
    script = [
        ("github_pull_request", {"repository": "acme/app", "actions": ["review_threads"], "number": 7}),
        ("git_push", {}),
        (
            "github_pull_request",
            {"repository": "acme/app", "actions": ["reply_to_thread"], "number": 7,
             "comment_id": 77, "body": "Renamed in abc123."},
        ),
    ]  # fmt: skip

    outcome = await run_turn("org_review_loop", script=script, git_engine=_Engine())

    tool_results = outcome.text_of_events()
    assert "Rename x" in tool_results and "Pushed acme/app@fix/rename" in tool_results
    replies = [c for c in fake.calls if c[1] == "/repos/acme/app/pulls/7/comments/77/replies"]
    assert replies and replies[0][2] == "ghs_app_write", "changes use the App's write token"
    assert "ghs_app_write" not in tool_results


def _bundle(user_token: str | None) -> GitCredentialBundle:
    return GitCredentialBundle(
        token=None,
        mode="member" if user_token else "public",
        author_name="o",
        author_email="o@x",
        user_token=user_token,
        source_details={"username": "octocat"},
    )


def _ctx(publish_as: str, engine: Any, org: str = "o") -> Any:
    git = GitPolicy(publish_as=publish_as)
    return SimpleNamespace(
        req=SimpleNamespace(git_engine=engine, db_mgr=None),
        binding=SimpleNamespace(policy=SimpleNamespace(git=git)),
        session=SimpleNamespace(tenant_org_id=org),
    )


@pytest.mark.parametrize(
    "publish_as, member, engine, expected",
    [
        ("member", "gho_member", _Engine(), GithubAccess("gho_member", actor="@octocat")),
        (
            "bot",
            "gho_member",
            _Engine(),
            GithubAccess("ghs_app_write", actor="the organization's GitHub App"),
        ),
        ("bot", "gho_member", None, GithubAccess("gho_member", actor="@octocat")),
        ("bot", None, None, GithubAccess(None)),
    ],
)
async def test_who_acts_follows_the_organizations_policy(
    publish_as: str, member: str | None, engine: Any, expected: GithubAccess
) -> None:
    access = turn_callbacks._github_write_access(_ctx(publish_as, engine), _bundle(member))

    got = await access("acme/app")

    assert (got.token, got.actor) == (expected.token, expected.actor)


@pytest.mark.setting("github.app_token_target_repositories")
@pytest.mark.parametrize(
    "repository, expected", [("acme/app", "ghs_app_write"), ("acme/payroll", "gho_member")]
)
async def test_the_app_acts_only_on_the_repositories_the_organization_allows(
    repository: str, expected: str
) -> None:
    org = "org_app_scope"
    await save_org_config(None, org, "github", {"app_token_target_repositories": "app, web"}, [])

    access = turn_callbacks._github_write_access(_ctx("bot", _Engine(), org), _bundle("gho_member"))

    assert (await access(repository)).token == expected


def test_event_notices_name_the_tool_that_answers_them() -> None:
    failing = next_step_hint("check_suite.completed", "failure", "acme/app", 7)
    review = next_step_hint("review.submitted", "changes_requested", "acme/app", 7)

    assert "github_actions run_view / job_logs" in failing
    assert "reply_to_thread" in review
    notice = pending_events_notice(
        [
            {
                "summary": "CI failed",
                "kind": "check_suite.completed",
                "outcome": "failure",
                "repo": "acme/app",
                "pr_number": 7,
            }
        ]
    )
    assert "job_logs" in notice

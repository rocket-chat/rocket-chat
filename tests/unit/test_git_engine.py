"""Unit tests for GitEngine, Co-author trailer formatting, signing modes, and webhook ingestion."""

import hashlib
import hmac
import json
from unittest.mock import AsyncMock

import pytest
from git_engine.config import GitEngineSettings
from git_engine.engine import GitEngine
from git_engine.trailers import format_commit_message, parse_co_authors

from specifications.interfaces.git import CoAuthor, CommitSpec, SigningMode
from specifications.interfaces.sandbox import ExecResult


def test_co_author_trailer_formatting() -> None:
    base_msg = "feat(auth): add rate limiting middleware\n\nProtect API routes against brute force."
    authors = [
        CoAuthor(name="Alice Developer", email="alice@acme.com"),
        CoAuthor(name="RocketChat Bot", email="bot@rocketchat.internal"),
    ]

    formatted = format_commit_message(base_msg, authors)
    assert "Co-authored-by: Alice Developer <alice@acme.com>" in formatted
    assert "Co-authored-by: RocketChat Bot <bot@rocketchat.internal>" in formatted
    assert (
        formatted
        == "feat(auth): add rate limiting middleware\n\nProtect API routes against brute force.\n\nCo-authored-by: Alice Developer <alice@acme.com>\nCo-authored-by: RocketChat Bot <bot@rocketchat.internal>"
    )


def test_co_author_deduplication() -> None:
    msg_with_author = (
        "fix(db): close pool connections\n\nCo-authored-by: Alice Developer <alice@acme.com>"
    )
    duplicate_author = [
        CoAuthor(name="Alice Developer", email="alice@acme.com"),
        CoAuthor(name="Bob Contributor", email="bob@acme.com"),
    ]

    formatted = format_commit_message(msg_with_author, duplicate_author)
    # Alice should appear only once
    assert formatted.count("Co-authored-by: Alice Developer <alice@acme.com>") == 1
    assert "Co-authored-by: Bob Contributor <bob@acme.com>" in formatted


def test_parse_co_authors() -> None:
    commit_text = (
        "refactor(core): cleanup task loops\n\n"
        "Co-authored-by: Carol Tester <carol@qa.org>\n"
        "Co-authored-by: Dave Eng <dave@dev.io>"
    )
    parsed = parse_co_authors(commit_text)
    assert len(parsed) == 2
    assert parsed[0].name == "Carol Tester"
    assert parsed[0].email == "carol@qa.org"
    assert parsed[1].name == "Dave Eng"
    assert parsed[1].email == "dave@dev.io"


@pytest.mark.asyncio
async def test_create_commit_offline_fallback() -> None:
    engine = GitEngine(driver=None)
    spec = CommitSpec(
        message="feat: sample commit",
        branch="main",
        co_authors=[CoAuthor(name="Dev", email="dev@test.com")],
        signing_mode=SigningMode.NONE,
    )
    sha = await engine.create_commit("session_offline", spec)
    assert len(sha) == 40  # SHA-1 hash length


@pytest.mark.asyncio
async def test_create_commit_gpg_signing_mode() -> None:
    driver = AsyncMock()
    # Mock git rev-parse HEAD returning a 40-char SHA
    driver.exec_command.return_value = ExecResult(
        exit_code=0,
        stdout="a1b2c3d4e5f607182930415263748596a7b8c9d0\n",
        stderr="",
        duration_ms=10,
    )

    engine = GitEngine(driver=driver)
    spec = CommitSpec(
        message="feat(crypto): signed payload",
        branch="feature/signed",
        signing_mode=SigningMode.GPG_KEY,
        gpg_key_id="3AA5C34371567BD2",
    )

    sha = await engine.create_commit("session_gpg", spec)
    assert sha == "a1b2c3d4e5f607182930415263748596a7b8c9d0"

    # Verify driver calls include gpg configurations
    commands = [call.args[1] for call in driver.exec_command.call_args_list]
    assert any("user.signingkey" in cmd and "3AA5C34371567BD2" in cmd for cmd in commands)
    assert any("commit.gpgsign true" in cmd for cmd in commands)
    assert any("git commit -S -m" in cmd for cmd in commands)


@pytest.mark.asyncio
async def test_create_commit_github_app_mode() -> None:
    driver = AsyncMock()
    driver.exec_command.return_value = ExecResult(
        exit_code=0,
        stdout="deadbeefdeadbeefdeadbeefdeadbeefdeadbeef\n",
        stderr="",
        duration_ms=10,
    )

    engine = GitEngine(driver=driver)
    spec = CommitSpec(
        message="chore(ci): update workflow",
        branch="main",
        signing_mode=SigningMode.GITHUB_APP,
    )

    sha = await engine.create_commit("session_gh_app", spec)
    assert sha == "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef"

    commands = [call.args[1] for call in driver.exec_command.call_args_list]
    assert any("github-actions[bot]" in cmd for cmd in commands)
    assert any("git commit -m" in cmd for cmd in commands)


@pytest.mark.asyncio
async def test_webhook_hmac_verification() -> None:
    secret = "super_secret_webhook_key_123"
    settings = GitEngineSettings(github_webhook_secret=secret)
    engine = GitEngine(settings=settings)

    payload = json.dumps(
        {
            "action": "labeled",
            "label": {"name": "ai-fix"},
            "repository": {"full_name": "acme/repo"},
            "issue": {"number": 42, "title": "Memory leak", "body": "Fix the leak in pool.py"},
            "sender": {"login": "octocat"},
        }
    ).encode("utf-8")

    correct_sig = "sha256=" + hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()

    # Valid signature
    trigger = await engine.handle_webhook(
        raw_body=payload,
        headers={"X-Hub-Signature-256": correct_sig, "X-GitHub-Event": "issues"},
    )
    assert trigger is not None
    assert trigger.event_type == "issues.labeled"
    assert trigger.repo_full_name == "acme/repo"
    assert trigger.issue_or_pr_number == 42
    assert trigger.sender_login == "octocat"
    assert "Memory leak" in trigger.prompt_text

    # Invalid signature should raise ValueError
    with pytest.raises(ValueError, match="Invalid webhook HMAC signature"):
        await engine.handle_webhook(
            raw_body=payload,
            headers={"X-Hub-Signature-256": "sha256=invalidhash", "X-GitHub-Event": "issues"},
        )

    # Missing signature should raise ValueError
    with pytest.raises(ValueError, match="Missing X-Hub-Signature-256 header"):
        await engine.handle_webhook(
            raw_body=payload,
            headers={"X-GitHub-Event": "issues"},
        )


@pytest.mark.asyncio
async def test_webhook_issue_comment_created() -> None:
    engine = GitEngine(settings=GitEngineSettings(github_webhook_secret=None))

    payload = json.dumps(
        {
            "action": "created",
            "comment": {"body": "@RocketChat please run pytest and fix failing tests."},
            "issue": {"number": 108, "labels": []},
            "repository": {"full_name": "org/backend"},
            "sender": {"login": "lead_dev"},
        }
    ).encode("utf-8")

    trigger = await engine.handle_webhook(
        raw_body=payload,
        headers={"X-GitHub-Event": "issue_comment"},
    )
    assert trigger is not None
    assert trigger.event_type == "issue_comment.created"
    assert trigger.repo_full_name == "org/backend"
    assert trigger.issue_or_pr_number == 108
    assert trigger.sender_login == "lead_dev"
    assert trigger.prompt_text == "@RocketChat please run pytest and fix failing tests."


@pytest.mark.asyncio
async def test_push_and_open_pr_synthetic_fallback() -> None:
    driver = AsyncMock()
    driver.exec_command.side_effect = [
        ExecResult(exit_code=0, stdout="feat/auth-patch\n", stderr="", duration_ms=5),
        ExecResult(
            exit_code=0, stdout="git@github.com:myorg/custom-repo.git\n", stderr="", duration_ms=5
        ),
        ExecResult(exit_code=0, stdout="", stderr="", duration_ms=10),  # git push
    ]

    engine = GitEngine(driver=driver, settings=GitEngineSettings(github_token=None))
    pr_url = await engine.push_and_open_pr(
        session_id="test_sess",
        title="feat: improve auth",
        body="Detailed body",
        target_branch="main",
    )
    assert "https://github.com/myorg/custom-repo/pull/" in pr_url


def test_generate_app_jwt_validity() -> None:
    from cryptography.hazmat.backends import default_backend
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    # Generate an ephemeral RSA key pair for testing
    private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
        backend=default_backend(),
    )
    pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("utf-8")

    settings = GitEngineSettings(
        github_app_id="123456",
        github_app_private_key=pem,
    )
    engine = GitEngine(settings=settings)
    token = engine.generate_app_jwt()

    assert token is not None
    # Validate payload
    import jwt

    public_key = private_key.public_key()
    pub_pem = public_key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("utf-8")

    decoded = jwt.decode(token, pub_pem, algorithms=["RS256"])
    assert decoded["iss"] == "123456"
    assert "exp" in decoded
    assert "iat" in decoded

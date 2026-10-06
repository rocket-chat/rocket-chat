"""Integration test for GitEngine and GitHub Webhook automated session resume."""

import hashlib
import hmac
import json
import uuid

import pytest
from agent_core.orchestrator import AsyncReActOrchestrator
from agent_core.tools.registry import ToolRegistry
from api.events import AsyncIOEventBus
from api.main import app
from api.store import InMemorySessionStore
from git_engine.config import GitEngineSettings
from git_engine.engine import GitEngine
from httpx import ASGITransport, AsyncClient
from llm_gateway.gateway import LiteLLMGateway
from sandbox_driver.docker_driver import DockerSandboxDriver

from specifications.interfaces.git import CoAuthor, CommitSpec, SigningMode
from specifications.interfaces.sandbox import SandboxStatus


@pytest.mark.asyncio
async def test_git_and_webhook_interoperability() -> None:
    # 1. Prepare environment and drivers
    driver = DockerSandboxDriver(default_image="python:3.12-slim")
    llm_gateway = LiteLLMGateway()
    tool_registry = ToolRegistry(driver=driver)
    orchestrator = AsyncReActOrchestrator(gateway=llm_gateway, registry=tool_registry)
    event_bus = AsyncIOEventBus()
    session_store = InMemorySessionStore()

    secret = "integration_test_secret_xyz"
    git_settings = GitEngineSettings(
        github_webhook_secret=secret,
        github_trigger_label="ai-fix",
        bot_author_name="RocketChat Bot",
        bot_author_email="bot@rocketchat.internal",
    )
    git_engine = GitEngine(driver=driver, settings=git_settings)

    app.state.sandbox_driver = driver
    app.state.llm_gateway = llm_gateway
    app.state.tool_registry = tool_registry
    app.state.orchestrator = orchestrator
    app.state.event_bus = event_bus
    app.state.session_store = session_store
    app.state.git_engine = git_engine

    issue_number = abs(hash(uuid.uuid4().hex)) % 9000 + 1000
    repo_name = f"acme/test-math-{uuid.uuid4().hex[:6]}"
    repo_sanitized = repo_name.replace("/", "_").replace("-", "_").lower()
    expected_session_id = f"gh_{repo_sanitized}_{issue_number}"

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            # -------------------------------------------------------------
            # Step 1: Simulate signed webhook event: issues.labeled (ai-fix)
            # -------------------------------------------------------------
            issue_payload = json.dumps(
                {
                    "action": "labeled",
                    "label": {"name": "ai-fix"},
                    "repository": {"full_name": repo_name},
                    "issue": {
                        "number": issue_number,
                        "title": "Fix division bug in calculator.py",
                        "body": "ZeroDivisionError occurs when dividing by 0. Raise ValueError instead.",
                    },
                    "sender": {"login": "alice-dev"},
                }
            ).encode("utf-8")

            sig = (
                "sha256="
                + hmac.new(secret.encode("utf-8"), issue_payload, hashlib.sha256).hexdigest()
            )

            headers = {
                "X-GitHub-Event": "issues",
                "X-Hub-Signature-256": sig,
                "Content-Type": "application/json",
            }

            resp = await client.post("/v1/webhooks/github", content=issue_payload, headers=headers)
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "created"
            assert data["session_id"] == expected_session_id

            # -------------------------------------------------------------
            # Step 2: Verify Docker Sandbox spawned & Git workspace initialized
            # -------------------------------------------------------------
            status = await driver.get_status(expected_session_id)
            assert status == SandboxStatus.RUNNING

            # Write buggy math code into sandbox
            initial_code = "def divide(a: float, b: float) -> float:\n    return a / b\n"
            await driver.write_file(expected_session_id, "calculator.py", initial_code)

            # -------------------------------------------------------------
            # Step 3: Implement fix and create commit with Co-authors & Signing
            # -------------------------------------------------------------
            fixed_code = (
                "def divide(a: float, b: float) -> float:\n"
                "    if b == 0:\n"
                "        raise ValueError('Cannot divide by zero')\n"
                "    return a / b\n"
            )
            await driver.write_file(expected_session_id, "calculator.py", fixed_code)

            spec = CommitSpec(
                message="fix(calc): prevent ZeroDivisionError and raise ValueError\n\nHandles division by zero gracefully.",
                branch=f"agent/issue-{issue_number}-fix",
                co_authors=[
                    CoAuthor(name="Alice Developer", email="alice@acme.com"),
                    CoAuthor(name="RocketChat Bot", email="bot@rocketchat.internal"),
                ],
                signing_mode=SigningMode.GITHUB_APP,
            )

            commit_sha = await git_engine.create_commit(expected_session_id, spec)
            assert len(commit_sha) == 40

            # Verify commit trailers inside container using git log
            log_res = await driver.exec_command(
                expected_session_id, "git log -1 --pretty=format:%B"
            )
            assert log_res.exit_code == 0
            assert "Co-authored-by: Alice Developer <alice@acme.com>" in log_res.stdout
            assert "Co-authored-by: RocketChat Bot <bot@rocketchat.internal>" in log_res.stdout

            # Open PR
            pr_url = await git_engine.push_and_open_pr(
                session_id=expected_session_id,
                title=f"Fix division bug (#{issue_number})",
                body="Automated PR resolving ZeroDivisionError.",
                target_branch="main",
            )
            assert "pull" in pr_url

            # -------------------------------------------------------------
            # Step 4: Test Hibernation & Automated Session Resume
            # -------------------------------------------------------------
            await driver.hibernate_sandbox(expected_session_id)
            hibernated_status = await driver.get_status(expected_session_id)
            assert hibernated_status == SandboxStatus.HIBERNATED

            # Mark session status in store as hibernated
            session_rec = await session_store.get_session(expected_session_id)
            assert session_rec is not None
            session_rec.sandbox_status = SandboxStatus.HIBERNATED
            await session_store.update_session(session_rec)

            # Send follow-up issue comment
            comment_payload = json.dumps(
                {
                    "action": "created",
                    "comment": {
                        "body": "@RocketChat please ensure unit tests are also included in test_calculator.py."
                    },
                    "issue": {
                        "number": issue_number,
                        "labels": [{"name": "ai-fix"}],
                    },
                    "repository": {"full_name": repo_name},
                    "sender": {"login": "alice-dev"},
                }
            ).encode("utf-8")

            comment_sig = (
                "sha256="
                + hmac.new(secret.encode("utf-8"), comment_payload, hashlib.sha256).hexdigest()
            )

            comment_headers = {
                "X-GitHub-Event": "issue_comment",
                "X-Hub-Signature-256": comment_sig,
                "Content-Type": "application/json",
            }

            resume_resp = await client.post(
                "/v1/webhooks/github", content=comment_payload, headers=comment_headers
            )
            assert resume_resp.status_code == 200
            resume_data = resume_resp.json()
            assert resume_data["status"] == "resumed"
            assert resume_data["session_id"] == expected_session_id

            # Verify sandbox was resumed and is RUNNING
            resumed_status = await driver.get_status(expected_session_id)
            assert resumed_status == SandboxStatus.RUNNING

            # Verify volume persistence: fixed file still exists with its contents
            persisted_code = await driver.read_file(expected_session_id, "calculator.py")
            assert "Cannot divide by zero" in persisted_code

    finally:
        # Clean up sandbox and persistent docker volume
        await driver.destroy_workspace(expected_session_id)

"""GitHub Webhook Ingestion Router with Automated Session Resume."""

import logging
import shlex
import time
from typing import Any

from agent_core.orchestrator import AsyncReActOrchestrator
from agent_core.tools.registry import ToolRegistry
from fastapi import APIRouter, Header, HTTPException, Request, status
from git_engine.engine import GitEngine

from api.routes import _spawn_background_turn
from specifications.interfaces.events import EventBusProtocol
from specifications.interfaces.sandbox import SandboxDriverProtocol, SandboxStatus, WorkspaceSpec
from specifications.interfaces.session import (
    SessionRecord,
    SessionSource,
    SessionStoreProtocol,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks", tags=["GitHub Webhooks"])


@router.post("/github", status_code=status.HTTP_200_OK)
async def github_webhook_endpoint(
    request: Request,
    x_hub_signature_256: str | None = Header(None, alias="X-Hub-Signature-256"),
    x_github_event: str | None = Header(None, alias="X-GitHub-Event"),
) -> dict[str, Any]:
    """
    Ingests signed GitHub webhooks (issues.labeled, issue_comment.created).
    Automatically wakes or initializes Docker/K8s sandbox workspaces and resumes ReAct agent loops.
    """
    raw_body = await request.body()
    headers_dict = dict(request.headers)

    git_engine: GitEngine = getattr(request.app.state, "git_engine", None) or GitEngine(
        driver=getattr(request.app.state, "sandbox_driver", None)
    )

    try:
        trigger = await git_engine.handle_webhook(raw_body, headers_dict)
    except ValueError as err:
        logger.warning("Rejected invalid GitHub webhook signature: %s", err)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Webhook authentication failed: {err}",
        ) from err

    if not trigger:
        return {"status": "ignored", "reason": "No actionable trigger in webhook event"}

    # Derive deterministic session ID mapped to repo and issue/PR
    repo_sanitized = trigger.repo_full_name.replace("/", "_").replace("-", "_").lower()
    session_id = f"gh_{repo_sanitized}_{trigger.issue_or_pr_number}"

    store: SessionStoreProtocol = request.app.state.session_store
    driver: SandboxDriverProtocol | None = getattr(request.app.state, "sandbox_driver", None)
    orchestrator: AsyncReActOrchestrator = request.app.state.orchestrator
    event_bus: EventBusProtocol = request.app.state.event_bus
    registry: ToolRegistry | None = getattr(request.app.state, "tool_registry", None)

    existing_session = await store.get_session(session_id)

    # ---------------------------------------------------------
    # Case A: Existing Session Found -> Automated Session Resume
    # ---------------------------------------------------------
    if existing_session:
        logger.info(
            "Found existing session %s for %s#%d. Resuming workspace...",
            session_id,
            trigger.repo_full_name,
            trigger.issue_or_pr_number,
        )

        if driver:
            if existing_session.sandbox_status == SandboxStatus.HIBERNATED:
                logger.info("Sandbox is hibernated. Resuming container for %s...", session_id)
                await driver.start_sandbox(session_id)
            else:
                await driver.start_sandbox(session_id)

            existing_session.sandbox_status = SandboxStatus.RUNNING

            # Sync latest git changes from remote
            await driver.exec_command(
                session_id,
                "git fetch origin && git pull origin HEAD --rebase || true",
            )
            if registry:
                registry.bind_sandbox(driver, session_id)

        existing_session.updated_at = int(time.time())
        await store.update_session(existing_session)

        prompt_text = (
            f"Follow-up instruction from @{trigger.sender_login}:\n\n{trigger.prompt_text}"
        )

        _spawn_background_turn(
            session_id=session_id,
            prompt=prompt_text,
            orchestrator=orchestrator,
            event_bus=event_bus,
            driver=driver,
            registry=registry,
            store=store,
        )

        return {
            "status": "resumed",
            "session_id": session_id,
            "issue_or_pr_number": trigger.issue_or_pr_number,
            "repo": trigger.repo_full_name,
        }

    # ---------------------------------------------------------
    # Case B: No Existing Session -> Create Workspace & Start Fix
    # ---------------------------------------------------------
    logger.info(
        "Creating new session %s for %s#%d...",
        session_id,
        trigger.repo_full_name,
        trigger.issue_or_pr_number,
    )

    branch = trigger.branch_name or f"agent/issue-{trigger.issue_or_pr_number}-fix"

    new_session = SessionRecord(
        session_id=session_id,
        tenant_org_id="github",
        tenant_user_id=trigger.sender_login,
        title=f"Fix {trigger.repo_full_name}#{trigger.issue_or_pr_number}",
        source=SessionSource.GITHUB_WEBHOOK,
        git_repo=trigger.repo_full_name,
        git_branch=branch,
        sandbox_status=SandboxStatus.RUNNING if driver else SandboxStatus.NON_EXISTENT,
        metadata={
            "github_repo": trigger.repo_full_name,
            "github_issue": trigger.issue_or_pr_number,
            "sender": trigger.sender_login,
        },
    )
    await store.create_session(new_session)

    if driver:
        await driver.ensure_workspace(
            WorkspaceSpec(
                session_id=session_id,
                tenant_org_id="github",
                tenant_user_id=trigger.sender_login,
                container_image=new_session.container_image or "python:3.12-slim",
            )
        )
        await driver.start_sandbox(session_id)

        # Ensure git binary is installed
        check_git = await driver.exec_command(session_id, "which git")
        if check_git.exit_code != 0:
            await driver.exec_command(
                session_id,
                "apt-get update -y && apt-get install -y --no-install-recommends git || apk add --no-cache git || true",
            )

        # Initialize git workspace with dedicated branch
        await driver.exec_command(
            session_id,
            f"git rev-parse --is-inside-work-tree || (git init -b {shlex.quote(branch)} && "
            "git config user.name 'RocketChat Bot' && "
            "git config user.email 'bot@rocketchat.internal')",
        )
        if registry:
            registry.bind_sandbox(driver, session_id)

    prompt_text = (
        f"Resolve GitHub issue #{trigger.issue_or_pr_number} in {trigger.repo_full_name}:\n\n"
        f"{trigger.prompt_text}"
    )

    _spawn_background_turn(
        session_id=session_id,
        prompt=prompt_text,
        orchestrator=orchestrator,
        event_bus=event_bus,
        driver=driver,
        registry=registry,
        store=store,
    )

    return {
        "status": "created",
        "session_id": session_id,
        "issue_or_pr_number": trigger.issue_or_pr_number,
        "repo": trigger.repo_full_name,
    }

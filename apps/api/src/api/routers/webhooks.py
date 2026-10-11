"""GitHub Webhook Ingestion Router with Automated Session Resume."""

import logging
import os
import time
from typing import Any

from agent_core.orchestrator import AsyncReActOrchestrator
from agent_core.tools.registry import ToolRegistry
from fastapi import APIRouter, Header, HTTPException, Request, status
from git_engine.engine import GitEngine

from api.db.store import ensure_tenant_identity
from api.services.git_credentials import (
    inject_git_credentials_into_sandbox,
    resolve_session_git_credentials,
)
from api.services.github_installations import installation_org
from api.services.repository_hint import with_repository_hint
from api.services.settings import effective_settings
from api.turns.tasks import _spawn_background_turn
from specifications.interfaces.events import EventBusProtocol
from specifications.interfaces.sandbox import SandboxDriverProtocol, SandboxStatus, WorkspaceSpec
from specifications.interfaces.session import (
    SessionRecord,
    SessionSource,
    SessionStoreProtocol,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks", tags=["GitHub Webhooks"])


async def _webhook_identity(
    request: Request, sender_login: str, installation_id: int | None
) -> tuple[str, str]:
    """Tenant and synthetic user that own sessions started by a webhook.

    Webhooks carry no Rocket identity. The organization is the one that mapped the GitHub App
    installation the event came through; only an unmapped installation falls back to the
    organization configured in ``ROCKET_WEBHOOK_ORG_ID`` (single-tenant installs).
    """
    mapped = (
        await installation_org(getattr(request.app.state, "db_manager", None), installation_id)
        if installation_id is not None
        else None
    )
    org_id: str = mapped or os.getenv("ROCKET_WEBHOOK_ORG_ID") or "default_org"
    safe_login = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in sender_login)
    return org_id, f"gh_{safe_login}"


async def _provision_webhook_identity(request: Request, org_id: str, user_id: str) -> None:
    db_mgr = getattr(request.app.state, "db_manager", None)
    if db_mgr is None:
        return
    async with db_mgr.session(tenant_org_id=org_id) as session:
        await ensure_tenant_identity(session, org_id, user_id)


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
            git_engine=git_engine,
            db_mgr=getattr(request.app.state, "db_manager", None),
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

    webhook_org_id, webhook_user_id = await _webhook_identity(
        request, trigger.sender_login, trigger.installation_id
    )
    await _provision_webhook_identity(request, webhook_org_id, webhook_user_id)

    new_session = SessionRecord(
        session_id=session_id,
        tenant_org_id=webhook_org_id,
        tenant_user_id=webhook_user_id,
        is_shared=True,
        title=f"Fix {trigger.repo_full_name}#{trigger.issue_or_pr_number}",
        source=SessionSource.GITHUB_WEBHOOK,
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
                tenant_org_id=webhook_org_id,
                tenant_user_id=webhook_user_id,
                container_image=new_session.container_image
                or str(
                    (
                        await effective_settings(
                            getattr(request.app.state, "db_manager", None),
                            webhook_org_id,
                            "sandboxes",
                        )
                    )["default_image"]
                ),
            )
        )
        await driver.start_sandbox(session_id)

        git_bundle = await resolve_session_git_credentials(
            user_id=webhook_user_id,
            org_id=webhook_org_id,
            git_engine=git_engine,
            db_mgr=getattr(request.app.state, "db_manager", None),
            repo=trigger.repo_full_name,
        )
        await inject_git_credentials_into_sandbox(driver, session_id, git_bundle)

    work_branch_instruction = (
        "Work on the branch above."
        if trigger.branch_name
        else f"Do the work on a new branch named `agent/issue-{trigger.issue_or_pr_number}-fix`."
    )
    prompt_text = with_repository_hint(
        f"Resolve GitHub issue #{trigger.issue_or_pr_number} in {trigger.repo_full_name}:\n\n"
        f"{trigger.prompt_text}\n\n{work_branch_instruction}",
        trigger.repo_full_name,
        trigger.branch_name,
    )

    _spawn_background_turn(
        session_id=session_id,
        prompt=prompt_text,
        orchestrator=orchestrator,
        event_bus=event_bus,
        driver=driver,
        registry=registry,
        store=store,
        git_engine=git_engine,
        db_mgr=getattr(request.app.state, "db_manager", None),
    )

    return {
        "status": "created",
        "session_id": session_id,
        "issue_or_pr_number": trigger.issue_or_pr_number,
        "repo": trigger.repo_full_name,
    }

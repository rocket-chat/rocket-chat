"""Bringing a session's sandbox up (always blank) and running a cloned repository's setup."""

import logging
from collections.abc import Sequence
from typing import Any

from sandbox_driver.exceptions import SandboxError

from api.services.git_credentials import (
    inject_git_credentials_into_sandbox,
    resolve_session_git_credentials,
)
from api.services.git_gateway_env import gateway_endpoint, github_api_url_of
from api.services.repo_setup import find_setup_plan, run_setup
from api.services.sandbox_catalogue import SandboxCatalogue, SandboxEntry
from api.services.sandbox_quota import enforce_running_sandbox_quota
from api.services.session_support import _repo_slug
from api.services.settings import (
    effective_settings,
)
from api.services.skills_lookup import _find_visible_skill
from api.services.slash_commands import expand_skill, parse_slash_command
from specifications.interfaces.events import EventBusProtocol, EventType, SessionEvent
from specifications.interfaces.sandbox import (
    ResourceLimits,
    SandboxDriverProtocol,
    SandboxStatus,
    WorkspaceSpec,
)
from specifications.interfaces.session import (
    SessionRecord,
    SessionStoreProtocol,
)

logger = logging.getLogger(__name__)


async def run_repository_setup(
    driver: SandboxDriverProtocol, session_id: str, folder: str, event_bus: EventBusProtocol
) -> str | None:
    """Run the cloned repository's own setup (``.rocket/setup.sh`` / devcontainer hooks), once.

    Returns a note for the agent: what ran and whether it worked, or None when the
    repository declares no setup.
    """
    plan = await find_setup_plan(driver, session_id, folder)
    if plan is None:
        return None
    await event_bus.publish(
        SessionEvent(
            session_id=session_id,
            event_type=EventType.STATUS_CHANGED,
            payload={
                "status": "running",
                "message": f"Running repository setup ({plan.source})...",
            },
        )
    )
    ok, output = await run_setup(driver, session_id, plan, folder)
    if ok:
        return f"Repository setup ({plan.source}) completed." + (
            "" if output == "already done" else f"\n{output}"
        )
    return f"Repository setup ({plan.source}) failed; the workspace may be incomplete:\n{output}"


def _session_metadata(record: SessionRecord | None) -> dict[str, Any]:
    return record.metadata if record is not None and isinstance(record.metadata, dict) else {}


async def _start_sandbox_or_report(
    driver: SandboxDriverProtocol, event_bus: EventBusProtocol, session_id: str
) -> None:
    """Start the sandbox; on failure tell the cockpit why before the error propagates.

    The error also reaches the agent as a failed tool result, but the person should not have
    to wait for the model to paraphrase it (a bad image name is theirs to fix).
    """
    try:
        await driver.start_sandbox(session_id)
    except SandboxError as err:
        await event_bus.publish(
            SessionEvent(
                session_id=session_id,
                event_type=EventType.SANDBOX_STATE,
                payload={"state": "failed", "message": str(err)},
            )
        )
        raise


async def provision_session_sandbox(
    *,
    store: SessionStoreProtocol,
    driver: SandboxDriverProtocol,
    event_bus: EventBusProtocol,
    db_mgr: Any,
    git_engine: Any,
    session_id: str,
    binding: Any,
    egress_allowed_cidrs: Sequence[str],
    block_internal_subnets: bool,
    fallback_user: str,
    fallback_org: str,
) -> None:
    """Bring the session's sandbox up: workspace and credentials.

    The workspace starts blank; the agent clones what it needs. Idempotent. A turn calls it
    lazily on the first tool that needs a sandbox; creating a session with ``prepare_sandbox``
    calls it straight away, so the sandbox is warm while the person types the first instruction.
    """
    s_rec = await store.get_session(session_id)
    # The stored status can be stale (the idle reaper hibernates sandboxes), so
    # trust the driver, not the record.
    if (
        s_rec
        and s_rec.sandbox_status == SandboxStatus.RUNNING
        and await driver.get_status(session_id) == SandboxStatus.RUNNING
    ):
        return
    t_user = s_rec.tenant_user_id if s_rec else fallback_user
    t_org = s_rec.tenant_org_id if s_rec else fallback_org
    # Transports surface this as their "working" indicator (Slack status loader).
    await event_bus.publish(
        SessionEvent(
            session_id=session_id,
            event_type=EventType.STATUS_CHANGED,
            payload={
                "status": "running",
                "message": "Spinning up sandbox environment...",
            },
        )
    )
    member_prefs = await effective_settings(db_mgr, t_org, "user_preferences", t_user)
    git_bundle = await resolve_session_git_credentials(
        user_id=t_user,
        org_id=t_org,
        git_engine=git_engine,
        db_mgr=db_mgr,
        user_overrides=member_prefs,
        repo=_repo_slug(s_rec.git_repo if s_rec else None),
    )
    sandbox_cfg = await effective_settings(db_mgr, t_org, "sandboxes")
    catalogue = SandboxCatalogue(db_mgr)
    chosen = await catalogue.chosen(
        t_org,
        session_sandbox_id=_session_metadata(s_rec).get("sandbox_id"),
        session_image=s_rec.container_image if s_rec else None,
        agent_sandbox_id=binding.sandbox_id,
    )
    image = (
        (chosen.image if chosen else None)
        or (s_rec.container_image if s_rec else None)
        or binding.sandbox_image
        or (await catalogue.default(t_org)).image
    )
    if not await catalogue.image_is_offered(
        t_org, image, [str(p) for p in sandbox_cfg.get("allowed_images") or ()]
    ):
        raise ValueError(
            f"Sandbox image '{image}' is not in the organization's sandbox catalogue. "
            "An administrator can add it under Settings > Organization > Sandboxes."
        )
    await enforce_running_sandbox_quota(
        store,
        driver,
        org_id=t_org,
        session_id=session_id,
        limit=int(sandbox_cfg.get("max_running_sandboxes") or 0),
    )
    sandbox_type = chosen or await catalogue.resolve(t_org, image=image)
    await driver.ensure_workspace(
        WorkspaceSpec(
            session_id=session_id,
            tenant_org_id=t_org,
            tenant_user_id=t_user,
            container_image=image,
            # The agent's own sizing, else its sandbox type's, else the organization's.
            resources=_resources(binding, sandbox_type, sandbox_cfg),
            profile=sandbox_type.profile if sandbox_type else "",
            # Data-egress governance (G5): carry the org's computed allowlist
            # onto the workspace spec so the driver can enforce it.
            egress_allowlist=list(egress_allowed_cidrs),
            block_internal_subnets=block_internal_subnets,
            require_non_root=bool(sandbox_cfg["require_non_root"]),
            # Git reaches GitHub through the platform's gateway, whatever the egress policy.
            platform_endpoints=[gateway_endpoint()],
        )
    )
    await _start_sandbox_or_report(driver, event_bus, session_id)
    await inject_git_credentials_into_sandbox(
        driver,
        session_id,
        git_bundle,
        org_id=t_org,
        user_id=t_user,
        api_url=github_api_url_of(git_engine),
    )
    if s_rec:

        def mark_running(record: SessionRecord) -> None:
            record.sandbox_status = SandboxStatus.RUNNING

        await store.mutate_session(session_id, s_rec.tenant_org_id, mark_running)
        if hasattr(driver, "write_file"):
            meta = s_rec.metadata if isinstance(s_rec.metadata, dict) else {}
            if plan_md := meta.get("plan_md"):
                try:
                    await driver.write_file(
                        session_id, ".rocket/PLAN.md", str(plan_md), overwrite=False
                    )
                except Exception:
                    pass
            if progress_md := meta.get("progress_md"):
                try:
                    await driver.write_file(
                        session_id, ".rocket/PROGRESS.md", str(progress_md), overwrite=False
                    )
                except Exception:
                    pass
    await event_bus.publish(
        SessionEvent(
            session_id=session_id,
            event_type=EventType.STATUS_CHANGED,
            payload={
                "status": "ready",
                "sandbox_status": "running",
                "message": "Sandbox workspace initialized.",
            },
        )
    )


async def _expand_slash_command(
    prompt: str,
    db_mgr: Any,
    org_id: str,
    user_id: str,
    allowed_skill_ids: Sequence[str],
) -> str:
    """The instruction the model receives: ``/skill args`` becomes the skill's text."""
    invocation = parse_slash_command(prompt)
    if invocation is None:
        return prompt
    skill = await _find_visible_skill(
        db_mgr, org_id, user_id, invocation.name, allowed_ids=allowed_skill_ids
    )
    if skill is None or skill.runtime != "text" or not skill.body:
        return prompt
    return expand_skill(skill.body, invocation.arguments)


def _resources(
    binding: Any, sandbox_type: SandboxEntry | None, sandbox_cfg: dict[str, Any]
) -> ResourceLimits:
    own = sandbox_type or SandboxEntry(id="", org_id="", name="", image="")
    return ResourceLimits(
        cpu_cores=float(binding.cpu_limit or own.cpu_limit or sandbox_cfg["cpu_limit"]),
        memory_limit=binding.memory_limit or own.memory_limit or str(sandbox_cfg["memory_limit"]),
        disk_size=own.disk_size or ResourceLimits().disk_size,
    )

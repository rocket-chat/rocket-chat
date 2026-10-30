"""Rocket Chat FastAPI Control Plane and Service Host."""

import logging
import os
import re
import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slack_assistant.config import SlackAssistantSettings

from api.bootstrap.background import BackgroundServices
from api.bootstrap.chat import start_chat_bridges
from api.bootstrap.core import CoreServices, assemble_core
from api.bootstrap.slack import SlackBotManager, start_slack
from api.db.session import current_tenant_org_id
from api.logging_config import bind_log_context, setup_logging
from api.middleware.auth import AuthMiddleware
from api.routers.admin_audit import router as admin_audit_router
from api.routers.admin_users import router as admin_users_router
from api.routers.agents import router as agents_router
from api.routers.api_tokens import router as api_tokens_router
from api.routers.approval_rules import router as approval_rules_router
from api.routers.artifacts import router as artifacts_router
from api.routers.attachments import router as attachments_router
from api.routers.auth import router as auth_router
from api.routers.automation import router as automation_router
from api.routers.changes import router as changes_router
from api.routers.chat import router as chat_router
from api.routers.checkpoints import router as checkpoints_router
from api.routers.credentials import router as credentials_router
from api.routers.dev import router as dev_router
from api.routers.feedback import router as feedback_router
from api.routers.fork import router as fork_router
from api.routers.git_gateway import router as git_gateway_router
from api.routers.git_signing import router as git_signing_router
from api.routers.github_app import router as github_app_router
from api.routers.github_credentials import router as github_credentials_router
from api.routers.gitlab_credentials import router as gitlab_credentials_router
from api.routers.goals import router as goals_router
from api.routers.knowledge import router as knowledge_router
from api.routers.mcp_oauth import router as mcp_oauth_router
from api.routers.mcp_servers import router as mcp_servers_router
from api.routers.memory import router as memory_router
from api.routers.missions import router as missions_router
from api.routers.models_catalog import router as models_catalog_router
from api.routers.organizations import router as organizations_router
from api.routers.plan import router as plan_router
from api.routers.preview import router as preview_router
from api.routers.processes import router as processes_router
from api.routers.provider_oauth import router as provider_oauth_router
from api.routers.repository_setups import router as repository_setups_router
from api.routers.sandboxes import router as sandboxes_router
from api.routers.session_crud import router as session_crud_router
from api.routers.session_git import router as session_git_router
from api.routers.session_participants import router as session_participants_router
from api.routers.session_search import router as session_search_router
from api.routers.session_turns import router as session_turns_router
from api.routers.session_ws import router as session_ws_router
from api.routers.sessions_list import router as sessions_list_router
from api.routers.settings_api import router as settings_api_router
from api.routers.setup_health import router as setup_health_router
from api.routers.sharing import router as sharing_router
from api.routers.skills import router as skills_router
from api.routers.slack_admin import router as slack_admin_router
from api.routers.slack_bots import router as slack_bots_router
from api.routers.slack_status import router as slack_status_router
from api.routers.spend_limits import router as spend_limits_router
from api.routers.subagents import router as subagents_router
from api.routers.templates import router as templates_router
from api.routers.terminal import router as terminal_router
from api.routers.tools import router as tools_router
from api.routers.turns import router as turns_router
from api.routers.usage import router as usage_router
from api.routers.version import router as version_router
from api.routers.webhooks import router as webhooks_router
from api.routers.workspace import router as workspace_router
from api.routers.workspace_snapshots import router as workspace_snapshots_router
from api.security_guards import enforce_production_config
from api.services.metrics import (
    drain_endpoint,
    metrics_endpoint,
    readiness_endpoint,
)
from api.services.shutdown import begin_shutdown
from api.telemetry import setup_tracing
from specifications.interfaces.sandbox import SandboxDriverProtocol

load_dotenv()
log_level = setup_logging()
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Manage application lifecycle, driver initializations, and embedded background tasks."""
    enforce_production_config()
    logger.info(
        "Rocket Chat Control Plane starting up with log level: %s", logging.getLevelName(log_level)
    )
    core = await assemble_core(app)
    background = BackgroundServices(app, core)
    background.start()
    slack = start_slack(app, core, SlackAssistantSettings())
    chat = start_chat_bridges(app, core)

    yield

    await chat.stop()
    await _shut_down(app, core, background, slack)


async def _shut_down(
    app: FastAPI,
    core: CoreServices,
    background: BackgroundServices,
    slack: SlackBotManager | None,
) -> None:
    """Drain, then stop background work and release shared resources, dependents last."""
    # Enter draining before cancelling background work so /v1/ready reports `draining` for
    # the remainder of the grace period; the preStop hook in the Helm chart calls
    # POST /v1/drain even earlier, so the pod leaves the Service endpoints before SIGTERM.
    app.state.draining = True
    begin_shutdown()

    await background.wait_for_running_turns(drain_wait_seconds())
    await background.stop()
    if core.shared_bus is not None:
        await core.shared_bus.close()
    await _cleanup_warm_pool(core.sandbox_driver)
    if slack is not None:
        await slack.stop()

    # Release the cached semantic indexes before tearing the pool down: they are
    # derived data and the largest process-resident allocations.
    code_index_cache = getattr(app.state, "code_index_cache", None)
    if code_index_cache is not None:
        code_index_cache.clear()

    # Dispose the connection pool inside the loop that created it. Without this,
    # every application lifespan (notably across repeated TestClient contexts)
    # leaves asyncpg connections bound to a closed event loop, which surfaces as
    # "another operation is in progress" / InterfaceError in later tests.
    if core.db_manager:
        await core.db_manager.close()
        app.state.db_manager = None
        app.state.transcript_store = None


def drain_wait_seconds() -> float:
    """How long shutdown lets running turns finish (``ROCKET_DRAIN_WAIT_SECONDS``, 0: not at all)."""
    try:
        return max(0.0, float(os.getenv("ROCKET_DRAIN_WAIT_SECONDS", "0")))
    except ValueError:
        return 0.0


async def _cleanup_warm_pool(driver: SandboxDriverProtocol) -> None:
    if not hasattr(driver, "cleanup_warm_pool"):
        return
    try:
        await driver.cleanup_warm_pool()
    except Exception as err:
        logger.debug("Warm pool cleanup on shutdown error: %s", err)


app = FastAPI(
    title="Rocket Chat Control Plane",
    description="FastAPI service hosting the ReAct agent engine and embedded Slack Assistant.",
    version="0.7.2",
    lifespan=lifespan,
)

cors_origins = [
    origin.strip()
    for origin in os.getenv("CORS_ALLOW_ORIGINS", "http://localhost:3000").split(",")
    if origin.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Authentication is always enforced. Local development opts into an anonymous identity
# explicitly with DEV_AUTH_BYPASS=true (ignored when ENVIRONMENT=production).
app.add_middleware(AuthMiddleware)


@app.middleware("http")
async def establish_tenant_context(request: Request, call_next: Callable[[Request], Any]) -> Any:
    """Bind the caller's organization (and, in dev, their identity) to this request.

    Ten tables FORCE row-level security keyed on `app.current_org_id` (see migration
    002). Setting it once here means *every* session the request opens is correctly
    scoped — including routes that never passed an org explicitly, which would
    otherwise read an empty result set or fail to write.
    """
    from api.auth.access import _get_request_identity
    from api.auth.api_tokens import GUEST_TOKEN_SOURCE, guest_may_call
    from api.auth.identity import attach_api_token_identity

    org_id: str | None = None
    user_id: str | None = None
    try:
        # This middleware runs before AuthMiddleware: a personal API token is verified here, before
        # anything resolves the caller.
        await attach_api_token_identity(request, getattr(request.app.state, "db_manager", None))
        org_id, user_id, _roles = _get_request_identity(request)
    except Exception as err:  # pragma: no cover - identity resolution must never 500
        logger.debug("Tenant context resolution skipped for %s: %s", request.url.path, err)

    caller = getattr(request.state, "context", None)
    if getattr(caller, "source", None) == GUEST_TOKEN_SOURCE and not guest_may_call(
        request.method, request.url.path
    ):
        return JSONResponse(
            status_code=403,
            content={"detail": "Forbidden: a guest token can only hold conversations"},
        )

    if org_id and user_id:
        await _provision_identity_if_enabled(request, org_id, user_id)

    token = current_tenant_org_id.set(org_id)
    bind_log_context(org_id=org_id, user_id=user_id)
    try:
        return await call_next(request)
    finally:
        current_tenant_org_id.reset(token)


@app.middleware("http")
async def assign_request_id(request: Request, call_next: Callable[[Request], Any]) -> Any:
    """Correlate a request's log lines (and the caller's report of it) by one id.

    Registered last so it is the outermost layer: even requests rejected by the auth
    middleware carry an id. A well-formed inbound id is kept so a gateway's id survives.
    """
    inbound = request.headers.get("x-request-id", "")
    request_id = inbound if _REQUEST_ID_PATTERN.fullmatch(inbound) else uuid.uuid4().hex
    bind_log_context(request_id=request_id)
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    return response


_REQUEST_ID_PATTERN = re.compile(r"[A-Za-z0-9._-]{8,64}")

setup_tracing(app)

_provisioned_tenant_pairs: set[tuple[str, str]] = set()


async def _provision_identity_if_enabled(request: Request, org_id: str, user_id: str) -> None:
    """Create the caller's organisation/user row on first sight (development only).

    Rows referenced by a foreign key must exist, so in development, E2E and CI a caller
    that arrives with tenant headers should not have to be pre-seeded. Disabled by
    default: in production identities come from the identity provider.
    """
    from api.db.store import _auto_provision_tenants_enabled, ensure_tenant_identity

    if not _auto_provision_tenants_enabled():
        return
    pair = (org_id, user_id)
    if pair in _provisioned_tenant_pairs:
        return
    db_manager = getattr(request.app.state, "db_manager", None)
    if db_manager is None:
        return
    # Bound to the organization: memberships' row-level policy checks it on insert.
    async with db_manager.session(tenant_org_id=org_id) as session:
        await ensure_tenant_identity(session, org_id, user_id)
    _provisioned_tenant_pairs.add(pair)


app.include_router(admin_audit_router)
app.include_router(admin_users_router)
app.include_router(organizations_router)
app.include_router(attachments_router)
app.include_router(artifacts_router)
app.include_router(changes_router)
app.include_router(checkpoints_router)
app.include_router(git_gateway_router)
app.include_router(auth_router)
app.include_router(dev_router)
# The keyset listing and fork routers must precede the legacy sessions router so the
# richer GET /v1/sessions (cursor + search) wins the route match.
app.include_router(sessions_list_router)
app.include_router(session_search_router)
app.include_router(fork_router)
app.include_router(github_app_router)
app.include_router(memory_router)
app.include_router(preview_router)
app.include_router(processes_router)
app.include_router(sandboxes_router)
app.include_router(sharing_router)
app.include_router(missions_router)
app.include_router(templates_router)
app.include_router(tools_router)
# The former single sessions router, in its original route order (path matching depends on it).
for _router in (
    agents_router,
    mcp_servers_router,
    mcp_oauth_router,
    session_crud_router,
    settings_api_router,
    usage_router,
    slack_status_router,
    # Before credentials_router: its /user/credentials/{credential_id} would capture
    # /user/credentials/github.
    github_credentials_router,
    gitlab_credentials_router,
    provider_oauth_router,
    credentials_router,
    spend_limits_router,
    git_signing_router,
    session_turns_router,
    models_catalog_router,
    session_ws_router,
    goals_router,
    knowledge_router,
    skills_router,
):
    app.include_router(_router)
app.include_router(terminal_router)
app.include_router(turns_router)
app.include_router(slack_admin_router)
app.include_router(slack_bots_router)
app.include_router(subagents_router)
app.include_router(api_tokens_router)
app.include_router(repository_setups_router)
app.include_router(version_router)
app.include_router(setup_health_router)
app.include_router(automation_router)
app.include_router(workspace_snapshots_router)
app.include_router(chat_router)
app.include_router(session_participants_router)
app.include_router(feedback_router)
app.include_router(session_git_router)
app.include_router(plan_router)
app.include_router(approval_rules_router)
app.include_router(workspace_router)
app.include_router(webhooks_router, prefix="/v1")
app.include_router(webhooks_router, prefix="/api/v1")


@app.get("/health")
@app.get("/v1/health")
async def health_check() -> dict[str, str]:
    """Static **liveness** signal: healthy while the process runs, no dependency is checked.

    Use `/v1/ready` for the database check; conflating the two would restart pods for a
    database outage they cannot fix.
    """
    return {"status": "healthy"}


# --- S11-ops operational endpoints -------------------------------------------
# Registered with add_api_route rather than include_router so this probes/metrics
# region stays independent of the integrator's feature-router wiring below.
# `/v1/health` above remains a static liveness signal; `/v1/ready` is the deep
# readiness probe (database ping + code-index stats), and `/metrics` is the
# Prometheus scrape target.
app.add_api_route(
    "/metrics",
    metrics_endpoint,
    methods=["GET"],
    tags=["Operations"],
    summary="Prometheus metrics",
    description="Prometheus text exposition of the API's counters, gauges and histograms.",
)
app.add_api_route(
    "/v1/ready",
    readiness_endpoint,
    methods=["GET"],
    tags=["Operations"],
    summary="Readiness probe",
    description="Deep readiness: pings the database and reports code-index stats. A pod that is "
    "draining answers 503 so the load balancer stops sending it traffic.",
)
app.add_api_route(
    "/v1/drain",
    drain_endpoint,
    methods=["POST"],
    tags=["Operations"],
    summary="Start draining",
    description="Marks this replica as draining: new work is refused with 503 while running "
    "turns finish. Called by the pod's preStop hook; administrators only.",
)

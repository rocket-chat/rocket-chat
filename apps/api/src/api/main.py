"""Rocket Chat FastAPI Control Plane and Service Host."""

import asyncio
import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from agent_core.orchestrator import AsyncReActOrchestrator
from agent_core.tools.registry import ToolRegistry
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from git_engine.engine import GitEngine
from llm_gateway.gateway import LiteLLMGateway
from sandbox_driver.docker_driver import DockerSandboxDriver
from sandbox_driver.k8s_driver import K8sSandboxDriver
from slack_assistant.assistant import SlackAssistant
from slack_assistant.config import SlackAssistantSettings

from api.db.models import OrganizationModel, UserModel
from api.db.session import DatabaseSessionManager
from api.db.store import PostgresSessionStore
from api.events import AsyncIOEventBus
from api.middleware.auth import AuthMiddleware
from api.routers.webhooks import router as webhooks_router
from api.routes import router as sessions_router
from api.store import InMemorySessionStore
from specifications.interfaces.sandbox import SandboxDriverProtocol
from specifications.interfaces.session import SessionStoreProtocol

load_dotenv()
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Manage application lifecycle, driver initializations, and embedded background tasks."""
    settings = SlackAssistantSettings()
    driver_type = (
        os.getenv("DEFAULT_SANDBOX_DRIVER") or os.getenv("SANDBOX_DRIVER") or "docker"
    ).lower()
    sandbox_driver: SandboxDriverProtocol
    if driver_type == "k8s":
        logger.info("Initializing Kubernetes Sandbox Driver (Production)...")
        sandbox_driver = K8sSandboxDriver()
    else:
        logger.info("Initializing Docker Sandbox Driver (Local)...")
        sandbox_driver = DockerSandboxDriver()

    llm_gateway = LiteLLMGateway()
    tool_registry = ToolRegistry(driver=sandbox_driver)
    git_engine = GitEngine(driver=sandbox_driver)
    default_model = (
        os.getenv("ROCKET_DEFAULT_MODEL")
        or os.getenv("DEFAULT_MODEL")
        or "openrouter/anthropic/claude-3.7-sonnet"
    )
    orchestrator = AsyncReActOrchestrator(
        gateway=llm_gateway,
        registry=tool_registry,
        default_model=default_model,
    )
    event_bus = AsyncIOEventBus()
    session_store: SessionStoreProtocol
    db_manager_instance: DatabaseSessionManager | None = None

    database_url = os.getenv("DATABASE_URL") or os.getenv("TEST_DATABASE_URL")
    if database_url:
        try:
            logger.info(
                "Initializing persistent PostgresSessionStore with %s...",
                database_url.split("@")[-1],
            )
            db_manager_instance = DatabaseSessionManager(database_url)
            await db_manager_instance.create_tables()
            # Ensure default organization and developer identity exist for foreign key integrity
            async with db_manager_instance.session() as s:
                org = await s.get(OrganizationModel, "default_org")
                if not org:
                    org = OrganizationModel(
                        id="default_org",
                        name="Default Organization",
                        slug="default-org",
                    )
                    s.add(org)
                    await s.flush()
                user = await s.get(UserModel, "dev_user")
                if not user:
                    user = UserModel(
                        id="dev_user",
                        org_id="default_org",
                        email="dev@rocket.chat",
                        name="Developer",
                    )
                    s.add(user)
                    await s.flush()
            session_store = PostgresSessionStore(db_manager_instance)
            logger.info("PostgresSessionStore initialized successfully.")
        except Exception as db_err:
            logger.warning(
                "Failed connecting to PostgreSQL (%s); falling back to InMemorySessionStore.",
                db_err,
            )
            session_store = InMemorySessionStore()
    else:
        logger.info("DATABASE_URL not set; using InMemorySessionStore.")
        session_store = InMemorySessionStore()

    app.state.sandbox_driver = sandbox_driver
    app.state.llm_gateway = llm_gateway
    app.state.tool_registry = tool_registry
    app.state.orchestrator = orchestrator
    app.state.event_bus = event_bus
    app.state.session_store = session_store
    app.state.git_engine = git_engine
    if db_manager_instance:
        app.state.db_manager = db_manager_instance

    slack_task: asyncio.Task[None] | None = None
    slack_assistant: SlackAssistant | None = None

    if settings.slack_bot_token and settings.slack_app_token:
        logger.info("Initializing embedded Slack Socket Mode Assistant...")
        slack_assistant = SlackAssistant(
            orchestrator=orchestrator,
            settings=settings,
            sandbox_driver=sandbox_driver,
            tool_registry=tool_registry,
        )
        app.state.slack_assistant = slack_assistant
    else:
        logger.info("Slack tokens not configured; skipping embedded Socket Mode background task.")

    reaper_task: asyncio.Task[None] | None = None
    if hasattr(sandbox_driver, "reconcile_inactivity"):

        async def _reaper_loop() -> None:
            while True:
                try:
                    await asyncio.sleep(30)
                    if hasattr(sandbox_driver, "reconcile_inactivity"):
                        await sandbox_driver.reconcile_inactivity()
                    if hasattr(sandbox_driver, "reconcile_warm_pool"):
                        await sandbox_driver.reconcile_warm_pool()
                except asyncio.CancelledError:
                    break
                except Exception as err:
                    logger.warning("Sandbox background reaper error: %s", err)

        reaper_task = asyncio.create_task(_reaper_loop())

    yield

    if reaper_task and not reaper_task.done():
        reaper_task.cancel()
        try:
            await reaper_task
        except asyncio.CancelledError:
            pass

    if hasattr(sandbox_driver, "cleanup_warm_pool"):
        try:
            await sandbox_driver.cleanup_warm_pool()
        except Exception as err:
            logger.debug("Warm pool cleanup on shutdown error: %s", err)

    if slack_assistant:
        logger.info("Shutting down Slack Assistant Socket Mode...")
        await slack_assistant.stop_socket_mode()
    if slack_task and not slack_task.done():
        slack_task.cancel()
        try:
            await slack_task
        except asyncio.CancelledError:
            pass


app = FastAPI(
    title="Rocket Chat Control Plane",
    description="FastAPI service hosting the ReAct agent engine and embedded Slack Assistant.",
    version="0.1.2",
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

# Enforce JWT authentication whenever an identity provider is configured.
# Without OIDC settings the API runs in local-development mode with a fixed identity.
if os.getenv("OIDC_JWKS_URL") or os.getenv("OIDC_PUBLIC_KEY"):
    app.add_middleware(AuthMiddleware)

app.include_router(sessions_router)
app.include_router(webhooks_router, prefix="/v1")
app.include_router(webhooks_router, prefix="/api/v1")


@app.get("/health")
@app.get("/v1/health")
async def health_check() -> dict[str, str]:
    """Basic health check endpoint."""
    return {"status": "healthy"}

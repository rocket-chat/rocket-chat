"""Build the process-wide services every request, turn and background task shares."""

import logging
import os
from dataclasses import dataclass

from agent_core.orchestrator import AsyncReActOrchestrator
from agent_core.tools.registry import ToolRegistry
from fastapi import FastAPI
from git_engine.engine import GitEngine
from llm_gateway.gateway import LiteLLMGateway
from llm_gateway.stub import DeterministicStubGateway

from api.bootstrap.code_search import register_code_search
from api.bootstrap.persistence import open_persistence
from api.bootstrap.runtime import (
    build_llm_gateway,
    build_sandbox_driver,
    cluster_bus_enabled,
    default_model,
)
from api.db.session import DatabaseSessionManager
from api.events import AsyncIOEventBus
from api.events_shared import PostgresEventBus, asyncpg_dsn
from api.services.attachments import AttachmentService
from api.services.budgets import BudgetService
from api.services.cluster import ClusterRelay, ClusterSessionLocks
from api.services.git_gateway import GitGateway
from api.services.memory import MemoryPolicy, bind_context_memory_callbacks
from api.services.metrics import (
    GlobalConcurrencyGate,
    SessionWriteLocks,
    build_backpressure,
    session_lock_capacity,
)
from api.services.model_credentials import CredentialResolver, ModelCredentialStore
from api.services.redaction import RedactionPolicy, Redactor
from api.services.settings import load_org_config
from api.services.transcript import InMemoryTranscriptStore, PostgresTranscriptStore
from api.services.usage import UsageLedger
from specifications.interfaces.events import EventBusProtocol
from specifications.interfaces.sandbox import SandboxDriverProtocol
from specifications.interfaces.session import SessionStoreProtocol
from specifications.interfaces.transcript import TranscriptStoreProtocol

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CoreServices:
    """The long-lived objects the lifespan hands to background tasks and integrations."""

    orchestrator: AsyncReActOrchestrator
    sandbox_driver: SandboxDriverProtocol
    tool_registry: ToolRegistry
    git_engine: GitEngine
    session_store: SessionStoreProtocol
    event_bus: EventBusProtocol
    db_manager: DatabaseSessionManager | None
    usage_ledger: UsageLedger
    attachment_service: AttachmentService
    concurrency_gate: GlobalConcurrencyGate
    session_locks: SessionWriteLocks
    shared_bus: PostgresEventBus | None
    database_url: str | None

    @property
    def cluster_dsn(self) -> str | None:
        """asyncpg DSN for cluster-wide locks and leader election; ``None`` on a single replica."""
        if self.shared_bus is not None and self.database_url:
            return asyncpg_dsn(self.database_url)
        return None


async def assemble_core(app: FastAPI) -> CoreServices:
    """Create the services in dependency order and publish them on ``app.state``."""
    database_url = os.getenv("DATABASE_URL") or os.getenv("TEST_DATABASE_URL")
    sandbox_driver = build_sandbox_driver()
    llm_gateway = build_llm_gateway()
    tool_registry = ToolRegistry(driver=sandbox_driver)
    git_engine = GitEngine(driver=sandbox_driver)
    tool_registry.bind_git_engine(git_engine)
    app.state.code_index_cache = register_code_search(tool_registry, sandbox_driver)
    orchestrator = AsyncReActOrchestrator(
        gateway=llm_gateway, registry=tool_registry, default_model=default_model()
    )

    persistence = await open_persistence(database_url)
    db_manager = persistence.db_manager
    usage_ledger = _bind_spend_tracking(app, llm_gateway, db_manager)

    shared_bus = await _start_shared_bus(orchestrator, db_manager, database_url)
    event_bus: EventBusProtocol = shared_bus or AsyncIOEventBus()
    gate, session_locks = _build_backpressure(orchestrator, shared_bus, database_url)
    attachment_service = AttachmentService(db_manager, sandbox_driver)
    transcript_store = _bind_transcript_store(orchestrator, db_manager)
    _bind_long_term_memory(tool_registry, db_manager)

    app.state.sandbox_driver = sandbox_driver
    app.state.llm_gateway = llm_gateway
    app.state.tool_registry = tool_registry
    app.state.orchestrator = orchestrator
    app.state.event_bus = event_bus
    app.state.session_store = persistence.session_store
    app.state.git_engine = git_engine
    app.state.git_gateway = GitGateway(db_manager, persistence.session_store, git_engine)
    app.state.global_concurrency_gate = gate
    app.state.session_write_locks = session_locks
    app.state.draining = False
    app.state.attachment_service = attachment_service
    app.state.transcript_store = transcript_store
    if db_manager:
        app.state.db_manager = db_manager

    return CoreServices(
        orchestrator=orchestrator,
        sandbox_driver=sandbox_driver,
        tool_registry=tool_registry,
        git_engine=git_engine,
        session_store=persistence.session_store,
        event_bus=event_bus,
        db_manager=db_manager,
        usage_ledger=usage_ledger,
        attachment_service=attachment_service,
        concurrency_gate=gate,
        session_locks=session_locks,
        shared_bus=shared_bus,
        database_url=database_url,
    )


def _bind_spend_tracking(
    app: FastAPI,
    llm_gateway: LiteLLMGateway | DeterministicStubGateway,
    db_manager: DatabaseSessionManager | None,
) -> UsageLedger:
    """Spend, caps and provider keys live in PostgreSQL, so every replica enforces the same
    budgets and survives restarts; the gateway consults them for every model call."""
    usage_ledger = UsageLedger(db_manager)
    model_credentials = ModelCredentialStore(db_manager)
    budget_service = BudgetService(db_manager, usage_ledger)
    llm_gateway.usage_sink = usage_ledger
    llm_gateway.budget_guard = budget_service
    llm_gateway.credential_resolver = CredentialResolver(db_manager, model_credentials)
    app.state.usage_ledger = usage_ledger
    app.state.model_credentials = model_credentials
    app.state.budget_service = budget_service
    return usage_ledger


async def _start_shared_bus(
    orchestrator: AsyncReActOrchestrator,
    db_manager: DatabaseSessionManager | None,
    database_url: str | None,
) -> PostgresEventBus | None:
    """With several replicas, share events, sequence numbers and decisions through PostgreSQL."""
    if db_manager is None or not database_url or not cluster_bus_enabled():
        return None
    bus = PostgresEventBus(db_manager, asyncpg_dsn(database_url))
    await bus.start()
    orchestrator.cluster_relay = ClusterRelay(bus, orchestrator)
    logger.info("Cluster event bus active (instance %s).", bus.instance_id)
    return bus


def _build_backpressure(
    orchestrator: AsyncReActOrchestrator,
    shared_bus: PostgresEventBus | None,
    database_url: str | None,
) -> tuple[GlobalConcurrencyGate, SessionWriteLocks]:
    """Process-wide backpressure.

    The orchestrator's per-session ``_active_tasks`` guard stops a second concurrent turn on
    one session but places no ceiling on the process as a whole. The gate bounds concurrent
    turns and the bounded lock registry gives a single writer per session; both are exposed
    through /metrics.
    """
    gate, locks = build_backpressure()
    orchestrator.human_wait_scope = gate.suspended
    if shared_bus is not None and database_url:
        # A session runs one turn at a time cluster-wide, not merely per replica.
        locks = ClusterSessionLocks(asyncpg_dsn(database_url), capacity=session_lock_capacity())
    return gate, locks


def _bind_transcript_store(
    orchestrator: AsyncReActOrchestrator, db_manager: DatabaseSessionManager | None
) -> TranscriptStoreProtocol:
    """Durable LLM transcript: a restart, a fork or another replica continues
    with the full tool context."""
    store: TranscriptStoreProtocol = (
        PostgresTranscriptStore(db_manager) if db_manager else InMemoryTranscriptStore()
    )
    orchestrator.transcript_store = store
    return store


async def memory_policy_for(db_manager: DatabaseSessionManager | None, org_id: str) -> MemoryPolicy:
    """The organization's ``memory`` settings as the policy the memory service enforces."""
    conf, _ = await load_org_config(db_manager, org_id, "memory")
    return MemoryPolicy.from_settings(conf)


def _bind_long_term_memory(
    tool_registry: ToolRegistry, db_manager: DatabaseSessionManager | None
) -> None:
    """Bind memory once for every turn path (web, Slack, webhooks, subagents); the callbacks
    resolve org/user/session from the turn's execution context."""

    async def _policy(org_id: str) -> MemoryPolicy:
        return await memory_policy_for(db_manager, org_id)

    async def _redactor(org_id: str) -> object:
        conf, _ = await load_org_config(db_manager, org_id, "governance")
        # MemoryService expects a callable redactor, so hand it the bound method.
        return Redactor(RedactionPolicy.from_settings(conf)).redact_text

    bind_context_memory_callbacks(
        tool_registry, db_manager, policy_provider=_policy, redactor_provider=_redactor
    )

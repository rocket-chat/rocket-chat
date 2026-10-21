"""Shared pytest configuration for the Rocket Chat test suite.

The FastAPI ``app`` is a module-level singleton, so tests that swap
``app.state.*`` (scripted orchestrators, fake sandbox drivers, custom stores)
would otherwise leak that state into the next test. This module restores the
original state after every test, which keeps ordering-independent results for
both the unit and integration suites.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterator
from typing import Any

import pytest
from api.main import app

# Tests impersonate tenants through request headers (`X-Tenant-Org-Id`/
# `X-Tenant-User-Id`), which the PostgreSQL store normally rejects for unknown
# identities (foreign key). Development/E2E/CI enable on-demand tenant provisioning;
# enabling it here keeps local runs identical to CI.
os.environ.setdefault("ROCKET_AUTO_PROVISION_TENANTS", "true")
# The suite impersonates tenants with headers and relies on the anonymous development
# identity; production mode (see tests/security) turns both off explicitly.
os.environ.setdefault("DEV_AUTH_BYPASS", "true")
os.environ.setdefault("ROCKET_TRUST_PROXY_HEADERS", "true")
# Tests build the schema with create_all; real deployments only ever run Alembic.
os.environ.setdefault("ROCKET_AUTO_CREATE_SCHEMA", "true")
# Every event published on the bus is validated against its payload contract.
os.environ.setdefault("ROCKET_VALIDATE_EVENTS", "1")
# The application defaults to the Docker driver; unit and integration tests run on the fake
# driver so they need no daemon. Tests of the Docker driver build it explicitly.
os.environ.setdefault("DEFAULT_SANDBOX_DRIVER", "fake")
# Reading a setting the registry does not declare fails the test instead of silently using the
# code's fallback (api.services.settings.DeclaredSettings).
os.environ.setdefault("ROCKET_STRICT_SETTINGS", "1")


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--shard",
        default=None,
        metavar="I/N",
        help="Run only the I-th of N shards (1-based). Whole files are kept together.",
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Split the run across CI runners without a plugin: files are dealt out round-robin."""
    spec = config.getoption("--shard")
    if not spec:
        return
    index, total = (int(part) for part in str(spec).split("/"))
    files = sorted({str(item.path) for item in items})
    owner = {path: position % total for position, path in enumerate(files)}
    kept = [item for item in items if owner[str(item.path)] == index - 1]
    deselected = [item for item in items if owner[str(item.path)] != index - 1]
    config.hook.pytest_deselected(items=deselected)
    items[:] = kept


DISPOSABLE_DB_ENV = "ROCKET_TEST_DB_IS_DISPOSABLE"


def pytest_sessionstart(session: pytest.Session) -> None:
    """Refuse to run the database-backed tests against a database nobody declared disposable.

    The suite deletes every row of the test organizations and creates scratch tables in the
    configured database. Pointing it at a developer's working database (the compose Postgres on
    :5432 looks identical) silently destroys data, so a human or the CI service container must
    opt in explicitly.
    """
    configured = os.getenv("DATABASE_URL") or os.getenv("TEST_DATABASE_URL")
    if configured and not (os.getenv("CI") or os.getenv(DISPOSABLE_DB_ENV)):
        pytest.exit(
            f"Refusing to run database tests: set {DISPOSABLE_DB_ENV}=1 to confirm that "
            f"{configured.split('@')[-1]} is a disposable database. They delete tenant rows "
            "and create tables there.",
            returncode=4,
        )


# Attributes that tests legitimately replace on the shared application instance.
MUTABLE_APP_STATE_ATTRS = (
    "db_manager",
    "session_store",
    "orchestrator",
    "tool_registry",
    "sandbox_driver",
    "event_bus",
    "llm_gateway",
    "git_engine",
    "slack_assistant",
    # Wave-3 operational bootstrap: a test that flips draining or swaps the backpressure
    # objects must not leak that into the WebSocket/turn tests that follow.
    "draining",
    "attachment_service",
    "transcript_store",
    "global_concurrency_gate",
    "session_write_locks",
)

# Organisations and users the HTTP-level tests impersonate via tenant headers.
#
# The PostgreSQL store enforces `sessions.user_id -> users.id` and
# `sessions.org_id -> organizations.id`. Locally the suite used to run against the
# in-memory store and never noticed the missing rows; with a database configured these
# identities must exist, so they are provisioned idempotently before the first test.
TEST_ORGS: tuple[str, ...] = ("default_org", "test_org", "usage_org")
TEST_USERS: tuple[str, ...] = (
    "dev_user",
    "dev_bob",
    "dev_charlie",
    "dev_david",
    "dev_eve",
    "test_user",
    "admin_user",
    "regular_user",
    "usage_user",
    "user_reset",
    "user_1",
    "user_2",
    "user_3",
    "member_user",
)

_seeded_database_urls: set[str] = set()

# Tenant tables are cleaned between tests (see `clean_tenant_state`). The list is read
# from `information_schema` so new org-scoped tables are covered automatically.
_ORG_SCOPED_TABLES: list[str] = []


async def _org_scoped_tables(manager: Any) -> list[str]:
    """Discover every table carrying an ``org_id`` column (tenant-owned data)."""
    from sqlalchemy import text

    async with manager.session() as session:
        result = await session.execute(
            text(
                "SELECT table_name FROM information_schema.columns "
                "WHERE column_name = 'org_id' AND table_schema = 'public' "
                "AND table_name <> 'organizations'"
            )
        )
        return sorted(row[0] for row in result)


@pytest.fixture(autouse=True)
async def clean_tenant_state() -> None:
    """Delete tenant-owned rows before each test when a database is configured.

    With PostgreSQL the store is shared across the whole session, so a test that changes
    an org-level setting (or leaves a session behind) would leak into the next one. The
    in-memory store hid this because every TestClient lifespan built a fresh store;
    against a real database the suite must isolate itself explicitly.
    """
    from api.db.session import DatabaseSessionManager

    url = os.getenv("DATABASE_URL") or os.getenv("TEST_DATABASE_URL")
    if not url:
        return

    # The rows are about to be deleted, so the middleware's "already provisioned" memory
    # of (org, user) pairs must be forgotten with them.
    import api.main

    api.main._provisioned_tenant_pairs.clear()

    admin_url = os.getenv(
        "ADMIN_DATABASE_URL",
        "postgresql+asyncpg://postgres:postgres@localhost:5432/rocket_chat",
    )
    manager = DatabaseSessionManager(admin_url)
    try:
        if not _ORG_SCOPED_TABLES:
            _ORG_SCOPED_TABLES.extend(await _org_scoped_tables(manager))
        async with manager.session() as session:
            from sqlalchemy import text

            for table in _ORG_SCOPED_TABLES:
                await session.execute(
                    text(f"DELETE FROM {table} WHERE org_id = ANY(:orgs)").bindparams(
                        orgs=list(TEST_ORGS)
                    )
                )
    finally:
        await manager.close()


@pytest.fixture(autouse=True)
async def seed_test_identities() -> None:
    """Provision the tenant identities the HTTP tests impersonate (once per process).

    Complements `ROCKET_AUTO_PROVISION_TENANTS` (which covers sessions created through
    the API): some tests write to the store directly, and a few assert on a specific
    organisation. Skips silently when no database is configured; provisioning errors
    are allowed to surface, because a half-provisioned database must not look like a pass.
    """
    from api.db.models import OrganizationModel, UserModel
    from api.db.session import DatabaseSessionManager

    url = os.getenv("DATABASE_URL") or os.getenv("TEST_DATABASE_URL")
    if not url or url in _seeded_database_urls:
        return

    admin_url = os.getenv(
        "ADMIN_DATABASE_URL",
        "postgresql+asyncpg://postgres:postgres@localhost:5432/rocket_chat",
    )
    manager = DatabaseSessionManager(admin_url)
    try:
        await manager.create_tables()
        async with manager.session() as session:
            for org_id in TEST_ORGS:
                if await session.get(OrganizationModel, org_id) is None:
                    session.add(
                        OrganizationModel(id=org_id, name=org_id, slug=org_id.replace("_", "-"))
                    )
                    await session.flush()
            for user_id in TEST_USERS:
                if await session.get(UserModel, user_id) is None:
                    session.add(
                        UserModel(
                            id=user_id,
                            org_id="default_org",
                            email=f"{user_id}@rocket.chat",
                            name=user_id.replace("_", " ").title(),
                        )
                    )
                    await session.flush()
    finally:
        await manager.close()
    _seeded_database_urls.add(url)


@pytest.fixture(autouse=True)
def isolate_app_state() -> Iterator[None]:
    """Snapshot and restore the application's mutable state around each test."""
    saved = {name: getattr(app.state, name, None) for name in MUTABLE_APP_STATE_ATTRS}
    try:
        yield
    finally:
        for name, value in saved.items():
            setattr(app.state, name, value)


@pytest.fixture(autouse=True)
def _no_openrouter_catalogue_fetch(monkeypatch: pytest.MonkeyPatch) -> None:
    """A turn on an OpenRouter model reads the reasoning efforts it takes; tests stay offline."""
    from api.services import openrouter_efforts

    async def offline() -> list[dict[str, object]]:
        return []

    monkeypatch.setattr(openrouter_efforts, "_fetch_catalogue", offline)
    monkeypatch.setattr(openrouter_efforts.openrouter_efforts, "_expires", 0.0)


@pytest.fixture(autouse=True)
def _reset_process_local_settings() -> Iterator[None]:
    """Org/member settings written without a database live in process memory; isolate tests."""
    from api.services.settings import reset_memory_settings

    reset_memory_settings()
    yield
    reset_memory_settings()


@pytest.fixture(autouse=True)
def _reset_process_local_personas() -> Iterator[None]:
    """Development-mode personas live in process memory; isolate tests from each other."""
    from api.services.personas import reset_memory_personas
    from api.services.subagents import reset_memory_subagents

    reset_memory_personas()
    reset_memory_subagents()
    yield
    reset_memory_personas()
    reset_memory_subagents()


@pytest.fixture
def require_database() -> Callable[[Exception, str], None]:
    """Return a helper that skips when PostgreSQL is absent — but fails in CI.

    A database-backed suite that *silently skips* in CI is worse than a failing one:
    it reports green while testing nothing. Locally, skipping is convenient.
    """

    def _check(error: Exception, url: str) -> None:
        message = f"PostgreSQL not accessible on {url}: {error}"
        if os.getenv("CI"):
            pytest.fail(message)
        pytest.skip(message)

    return _check


@pytest.fixture
def fake_mcp_servers(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Probe/list MCP servers against an in-process fixture server instead of the network.

    The real handshake runs (initialize + tools/list through the official client); only the
    transport and the DNS-based SSRF check are replaced, so a registered server genuinely
    becomes ``connected`` with the fixture's tools.
    """
    from api.services import mcp_runtime
    from mcp import Client
    from mcp.server.mcpserver import MCPServer

    server = MCPServer("fixture")

    @server.tool()
    def ping(message: str = "pong") -> str:
        """Echo a message."""
        return message

    async def public(_url: str) -> None:
        return None

    monkeypatch.setattr(mcp_runtime, "connect_remote", lambda _target: Client(server))
    monkeypatch.setattr(mcp_runtime, "resolve_public_target", public)
    return server


@pytest.fixture
async def tenant_rows() -> None:
    """Make sure the orgs/users that direct store tests reference exist.

    The per-test cleanup deletes tenant rows, and only API requests auto-provision them;
    tests that call stores directly need the rows their foreign keys point at.
    """
    from api.db.models import OrganizationModel, UserModel
    from api.db.session import DatabaseSessionManager

    admin_url = os.getenv("ADMIN_DATABASE_URL")
    if not admin_url or not (os.getenv("DATABASE_URL") or os.getenv("TEST_DATABASE_URL")):
        return
    admin = DatabaseSessionManager(admin_url)
    try:
        async with admin.session() as session:
            for org_id in TEST_ORGS:
                if await session.get(OrganizationModel, org_id) is None:
                    session.add(
                        OrganizationModel(id=org_id, name=org_id, slug=org_id.replace("_", "-"))
                    )
                    await session.flush()
            for user_id in TEST_USERS:
                if await session.get(UserModel, user_id) is None:
                    owner = "usage_org" if user_id == "usage_user" else "test_org"
                    session.add(
                        UserModel(
                            id=user_id,
                            org_id=owner,
                            email=f"{user_id}@rocket.chat",
                            name=user_id,
                        )
                    )
                    await session.flush()
    finally:
        await admin.close()


@pytest.fixture(autouse=True)
def _linked_github_tokens_are_valid(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tests never call GitHub to validate a linked token; the check has its own tests."""

    async def _valid(token: str, api_url: str = "https://api.github.com") -> bool:
        return True

    monkeypatch.setattr("api.services.git_credentials.github_token_is_valid", _valid)


@pytest.fixture(autouse=True)
def _events_keep_their_payload_contract() -> Iterator[None]:
    """Fail any test that published an event whose payload breaks ``event_payloads``."""
    from api.events import CONTRACT_VIOLATIONS

    CONTRACT_VIOLATIONS.clear()
    yield
    violations = list(CONTRACT_VIOLATIONS)
    CONTRACT_VIOLATIONS.clear()
    assert not violations, "event payload contract violated:\n" + "\n".join(sorted(set(violations)))

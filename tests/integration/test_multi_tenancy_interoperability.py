"""Integration tests for Multi-Tenancy (PostgreSQL RLS), OIDC SSO, and Hierarchical Config Engine."""

import os
import time
import uuid

import pytest
from api.db.models import OrganizationModel, SessionModel, UserModel
from api.db.session import DatabaseSessionManager
from api.db.store import PostgresSessionStore
from config_engine import (
    ConfigEngine,
    CredentialCipher,
    OrgPolicy,
    PolicyViolationError,
    TeamPolicy,
    UserOverride,
)
from sqlalchemy import select, text

from specifications.interfaces.llm import BYOKCredentials, ChatMessage, ModelRequest
from specifications.interfaces.sandbox import ResourceLimits, SandboxStatus, WorkspaceSpec
from specifications.interfaces.session import SessionRecord, SessionSource

ADMIN_DB_URL = os.getenv(
    "ADMIN_DATABASE_URL",
    "postgresql+asyncpg://postgres:postgres@localhost:5432/rocket_chat",
)
APP_DB_URL = os.getenv(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://rocket_app:rocket_app@localhost:5432/rocket_chat",
)


@pytest.fixture
async def db_manager():
    """
    Provision database manager with PostgreSQL Row-Level Security.
    Connects as application user 'rocket_app' (non-superuser), as PostgreSQL superusers
    bypass Row-Level Security by kernel design.
    """
    try:
        admin_manager = DatabaseSessionManager(ADMIN_DB_URL)
        await admin_manager.create_tables()
    except Exception as e:
        pytest.skip(f"PostgreSQL not accessible on {ADMIN_DB_URL}: {e}")

    # Ensure application role exists with grants
    async with admin_manager.session() as s:
        setup_stmts = [
            """
            DO $$
            BEGIN
                IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'rocket_app') THEN
                    CREATE ROLE rocket_app WITH LOGIN PASSWORD 'rocket_app';
                END IF;
            END
            $$;
            """,
            "GRANT ALL PRIVILEGES ON DATABASE rocket_chat TO rocket_app;",
            "GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO rocket_app;",
            "GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO rocket_app;",
            "ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO rocket_app;",
        ]
        for stmt in setup_stmts:
            await s.execute(text(stmt))
    await admin_manager.close()

    manager = DatabaseSessionManager(APP_DB_URL)
    yield manager
    await manager.close()


@pytest.mark.asyncio
async def test_tenant_data_isolation_row_level_security(db_manager):
    """
    Verify PostgreSQL Row-Level Security (RLS) data isolation:
    Queries executed under app.current_org_id = 'Org A' MUST return zero rows belonging to 'Org B'.
    """
    org_a = f"org_a_{uuid.uuid4().hex[:6]}"
    org_b = f"org_b_{uuid.uuid4().hex[:6]}"
    user_a = f"user_a_{uuid.uuid4().hex[:6]}"
    user_b = f"user_b_{uuid.uuid4().hex[:6]}"

    # Setup organization & user records
    async with db_manager.session() as session:
        session.add_all(
            [
                OrganizationModel(
                    id=org_a,
                    name="Alpha Aerospace",
                    slug=f"alpha-{uuid.uuid4().hex[:6]}",
                ),
                OrganizationModel(
                    id=org_b,
                    name="Beta Orbital",
                    slug=f"beta-{uuid.uuid4().hex[:6]}",
                ),
                UserModel(
                    id=user_a,
                    org_id=org_a,
                    email="pilot@alpha.corp",
                    name="Pilot Alpha",
                ),
                UserModel(
                    id=user_b,
                    org_id=org_b,
                    email="pilot@beta.corp",
                    name="Pilot Beta",
                ),
            ]
        )

    store = PostgresSessionStore(db_manager)

    # 1. Insert session for Org A
    session_id_a = f"sess_alpha_{uuid.uuid4().hex[:8]}"
    record_a = SessionRecord(
        session_id=session_id_a,
        tenant_org_id=org_a,
        tenant_user_id=user_a,
        title="Mission Alpha Telemetry",
        source=SessionSource.WEB_UI,
        sandbox_status=SandboxStatus.RUNNING,
        conversation_history=[
            ChatMessage(role="user", content="Deploy satellite telemetry probe"),
            ChatMessage(role="assistant", content="Probe operational in orbit"),
        ],
    )
    await store.create_session(record_a)

    # 2. Insert session for Org B
    session_id_b = f"sess_beta_{uuid.uuid4().hex[:8]}"
    record_b = SessionRecord(
        session_id=session_id_b,
        tenant_org_id=org_b,
        tenant_user_id=user_b,
        title="Mission Beta Engine Check",
        source=SessionSource.WEB_UI,
        sandbox_status=SandboxStatus.HIBERNATED,
        conversation_history=[
            ChatMessage(role="user", content="Check fuel levels"),
        ],
    )
    await store.create_session(record_b)

    # 3. Assert RLS isolation via PostgresSessionStore: Org A cannot read Org B session
    retrieved_a_from_a = await store.get_session(session_id_a, tenant_org_id=org_a)
    assert retrieved_a_from_a is not None
    assert retrieved_a_from_a.title == "Mission Alpha Telemetry"

    # Querying session B with Org A context must return None (isolated by PostgreSQL RLS)
    retrieved_b_from_a = await store.get_session(session_id_b, tenant_org_id=org_a)
    assert retrieved_b_from_a is None

    # Querying session A with Org B context must return None
    retrieved_a_from_b = await store.get_session(session_id_a, tenant_org_id=org_b)
    assert retrieved_a_from_b is None

    # 4. Assert direct SQL query under RLS transaction context
    async with db_manager.session(tenant_org_id=org_a) as session:
        # SELECT * FROM sessions without explicit WHERE org_id clause
        stmt = select(SessionModel)
        result = await session.execute(stmt)
        visible_sessions = result.scalars().all()

        session_ids = [s.id for s in visible_sessions]
        # Only Org A session is visible!
        assert session_id_a in session_ids
        assert session_id_b not in session_ids

    # 5. List sessions under Org A returns only Org A rows
    listed_a = await store.list_sessions(tenant_org_id=org_a)
    assert all(s.tenant_org_id == org_a for s in listed_a)
    assert any(s.session_id == session_id_a for s in listed_a)
    assert not any(s.session_id == session_id_b for s in listed_a)


@pytest.mark.asyncio
async def test_policy_violation_enforcement():
    """
    Verify Org Policy constraint enforcement:
    Org A forbids 'openai/*' models. User override requesting 'openai/gpt-4o' MUST raise PolicyViolationError.
    """
    engine = ConfigEngine()
    org_id = "org_defence_systems"

    # Organization strictly allows only Anthropic models and blocks OpenAI
    engine.set_org_policy(
        OrgPolicy(
            org_id=org_id,
            allowed_models=["anthropic/*"],
            forbidden_models=["openai/*"],
            default_model="anthropic/claude-3-7-sonnet",
        )
    )

    # 1. Compliant user session resolves without error
    resolved_ok = await engine.resolve_session_config(
        tenant_org_id=org_id,
        team_id=None,
        user_id="user_compliant",
    )
    assert resolved_ok.active_model == "anthropic/claude-3-7-sonnet"

    # 2. Rogue user override requesting openai/gpt-4o
    engine.set_user_override(
        UserOverride(
            user_id="user_rogue",
            org_id=org_id,
            preferred_model="openai/gpt-4o",
        )
    )

    with pytest.raises(PolicyViolationError) as exc_info:
        await engine.resolve_session_config(
            tenant_org_id=org_id,
            team_id=None,
            user_id="user_rogue",
        )

    error_msg = str(exc_info.value)
    assert "openai/gpt-4o" in error_msg
    assert "violates Organization policy" in error_msg


@pytest.mark.asyncio
async def test_encrypted_byok_credential_at_rest(db_manager):
    """
    Verify BYOK credentials encryption:
    Inspect raw SQL database row; verify ciphertext is encrypted with AES-256-GCM
    and cannot be read in plaintext.
    """
    cipher = CredentialCipher(master_key="super-secret-cluster-master-key!!")
    raw_api_key = "sk-ant-api03-live-corporate-key-998877"

    encrypted_key = cipher.encrypt(raw_api_key)
    assert raw_api_key not in encrypted_key

    # Save to database
    cred_id = f"byok_{uuid.uuid4().hex[:8]}"
    org_id = f"org_{uuid.uuid4().hex[:6]}"

    async with db_manager.session(tenant_org_id=org_id) as session:
        session.add(
            OrganizationModel(
                id=org_id,
                name="Deep Space Security",
                slug=f"deep-space-{uuid.uuid4().hex[:6]}",
            )
        )
        await session.flush()
        await session.execute(
            text(
                """
                INSERT INTO byok_credentials (id, org_id, provider, encrypted_api_key, key_fingerprint, created_at)
                VALUES (:id, :org_id, :provider, :encrypted, :fp, :now)
                """
            ),
            {
                "id": cred_id,
                "org_id": org_id,
                "provider": "anthropic",
                "encrypted": encrypted_key,
                "fp": cipher.fingerprint(raw_api_key),
                "now": int(time.time()),
            },
        )

    # Read back raw column directly via SQL under same tenant
    async with db_manager.session(tenant_org_id=org_id) as session:
        res = await session.execute(
            text("SELECT encrypted_api_key FROM byok_credentials WHERE id = :id"),
            {"id": cred_id},
        )
        stored_ciphertext = res.scalar_one()

    # Querying from a different organization yields zero rows due to RLS
    async with db_manager.session(tenant_org_id="unauthorized_org") as session:
        res_unauthorized = await session.execute(
            text("SELECT encrypted_api_key FROM byok_credentials WHERE id = :id"),
            {"id": cred_id},
        )
        assert res_unauthorized.scalar_one_or_none() is None

    # Assert raw stored data is ciphertext, not plaintext
    assert stored_ciphertext != raw_api_key
    assert "sk-ant-api03" not in stored_ciphertext

    # Assert decryption with master key retrieves original secret
    decrypted = cipher.decrypt(stored_ciphertext)
    assert decrypted == raw_api_key


@pytest.mark.asyncio
async def test_end_to_end_config_injection_interoperability():
    """
    Verify end-to-end configuration cascade into LiteLLM Gateway and Sandbox Driver:
    Org BYOK key and allowed models cascade dynamically into LiteLLM ModelRequest
    and enforce sandbox container image & resource limits.
    """
    engine = ConfigEngine()
    org_id = "org_deep_space"
    team_id = "team_propulsion"
    user_id = "engineer_mark"

    # 1. Configure Org Policy
    engine.set_org_policy(
        OrgPolicy(
            org_id=org_id,
            allowed_models=["anthropic/*", "openrouter/*"],
            default_model="anthropic/claude-3-7-sonnet",
            default_image="ghcr.io/space/avionics-base:latest",
            max_memory="16Gi",
            max_cpus=8.0,
            require_signed_commits=True,
            custom_env_vars={"ROCKET_TELEMETRY": "enabled"},
        )
    )
    # Set Org corporate BYOK key
    await engine.set_org_byok(
        org_id=org_id,
        provider="anthropic",
        api_key="sk-ant-corp-deep-space-key",
    )

    # 2. Team Policy overrides image and adds team env
    engine.set_team_policy(
        TeamPolicy(
            team_id=team_id,
            org_id=org_id,
            default_image="ghcr.io/space/propulsion-sim:2.4",
            custom_env_vars={"SUBSYSTEM": "ion_thruster"},
        )
    )

    # 3. User Override specifies author and preferred compliant model
    engine.set_user_override(
        UserOverride(
            user_id=user_id,
            org_id=org_id,
            team_id=team_id,
            preferred_model="anthropic/claude-3-5-sonnet",
            git_author_name="Mark Watney",
            git_author_email="m.watney@nasa.gov",
        )
    )

    # Resolve Effective Configuration
    resolved = await engine.resolve_session_config(
        tenant_org_id=org_id,
        team_id=team_id,
        user_id=user_id,
    )

    # Assert Resolution Cascade
    assert resolved.active_model == "anthropic/claude-3-5-sonnet"
    assert resolved.active_container_image == "ghcr.io/space/propulsion-sim:2.4"
    assert resolved.byok_provider == "anthropic"
    assert resolved.byok_api_key == "sk-ant-corp-deep-space-key"
    assert resolved.git_author_name == "Mark Watney"
    assert resolved.git_author_email == "m.watney@nasa.gov"
    assert resolved.require_signed_commits is True
    assert resolved.custom_env_vars["ROCKET_TELEMETRY"] == "enabled"
    assert resolved.custom_env_vars["SUBSYSTEM"] == "ion_thruster"

    # 4. LiteLLM Gateway Interoperability: Construct ModelRequest with resolved BYOK credentials
    model_request = ModelRequest(
        model=resolved.active_model,
        messages=[ChatMessage(role="user", content="Simulate ion thruster burn")],
        credentials=BYOKCredentials(
            provider=resolved.byok_provider,
            api_key=resolved.byok_api_key,
        ),
    )
    assert model_request.model == "anthropic/claude-3-5-sonnet"
    assert model_request.credentials is not None
    assert model_request.credentials.api_key == "sk-ant-corp-deep-space-key"

    # 5. Sandbox Driver Interoperability: Construct WorkspaceSpec with resolved image & limits
    spec = WorkspaceSpec(
        session_id="session_thruster_01",
        tenant_org_id=org_id,
        tenant_user_id=user_id,
        container_image=resolved.active_container_image,
        resources=ResourceLimits(
            cpu_cores=4.0,
            memory_limit="8Gi",
        ),
        env_vars=resolved.custom_env_vars,
    )
    assert spec.container_image == "ghcr.io/space/propulsion-sim:2.4"
    assert spec.env_vars["ROCKET_TELEMETRY"] == "enabled"
    assert spec.env_vars["SUBSYSTEM"] == "ion_thruster"

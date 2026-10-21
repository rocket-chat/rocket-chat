"""SQLAlchemy 2.0 multi-tenant database models with Row-Level Security support."""

import time
from typing import Any, ClassVar

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import JSON


class Base(DeclarativeBase):
    """Base declarative class for all Rocket Chat database entities."""

    type_annotation_map: ClassVar[dict[Any, Any]] = {
        dict[str, Any]: JSON().with_variant(JSONB, "postgresql"),
        list[dict[str, Any]]: JSON().with_variant(JSONB, "postgresql"),
        list[str]: JSON().with_variant(JSONB, "postgresql"),
    }


class OrganizationModel(Base):
    """Enterprise tenant boundary."""

    __tablename__ = "organizations"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    created_at: Mapped[int] = mapped_column(
        BigInteger, default=lambda: int(time.time()), nullable=False
    )
    settings: Mapped[dict[str, Any]] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"), default=dict
    )

    teams: Mapped[list["TeamModel"]] = relationship(
        back_populates="organization", cascade="all, delete-orphan"
    )
    users: Mapped[list["UserModel"]] = relationship(
        back_populates="organization", cascade="all, delete-orphan"
    )
    sessions: Mapped[list["SessionModel"]] = relationship(
        back_populates="organization", cascade="all, delete-orphan"
    )


class TeamModel(Base):
    """Team grouping within an Organization, optionally bound to an IdP group."""

    __tablename__ = "teams"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    org_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(100), nullable=False)
    idp_group: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[int] = mapped_column(
        BigInteger, default=lambda: int(time.time()), nullable=False
    )
    settings: Mapped[dict[str, Any]] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"), default=dict
    )

    organization: Mapped["OrganizationModel"] = relationship(back_populates="teams")
    users: Mapped[list["UserModel"]] = relationship(back_populates="team")


class UserModel(Base):
    """User identity mapped from OIDC claims."""

    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    org_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    team_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("teams.id", ondelete="SET NULL"), nullable=True, index=True
    )
    email: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(50), default="developer", nullable=False)
    created_at: Mapped[int] = mapped_column(
        BigInteger, default=lambda: int(time.time()), nullable=False
    )
    settings: Mapped[dict[str, Any]] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"), default=dict
    )
    # Local (non-SSO) accounts: an argon2id hash, never the password. Null for SSO members.
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    password_updated_at: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    failed_login_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    locked_until: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    organization: Mapped["OrganizationModel"] = relationship(back_populates="users")
    team: Mapped["TeamModel | None"] = relationship(back_populates="users")
    sessions: Mapped[list["SessionModel"]] = relationship(back_populates="user")


class SessionModel(Base):
    """Agent coding session state. Protected by PostgreSQL Row-Level Security (RLS)."""

    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    org_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    source: Mapped[str] = mapped_column(String(50), default="web_ui", nullable=False)
    sandbox_status: Mapped[str] = mapped_column(String(50), default="non_existent", nullable=False)
    container_image: Mapped[str | None] = mapped_column(String(255), nullable=True)
    git_repo: Mapped[str | None] = mapped_column(String(255), nullable=True)
    git_branch: Mapped[str | None] = mapped_column(String(255), nullable=True)
    conversation_history: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"), default=list
    )
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"), default=dict
    )
    # Sidebar ergonomics for tenants with hundreds of sessions: pinning keeps
    # a session at the top, archiving hides it from the default listing.
    is_pinned: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_archived: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_processing: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[int] = mapped_column(
        BigInteger, default=lambda: int(time.time()), nullable=False
    )
    updated_at: Mapped[int] = mapped_column(
        BigInteger, default=lambda: int(time.time()), nullable=False
    )

    organization: Mapped["OrganizationModel"] = relationship(back_populates="sessions")
    user: Mapped["UserModel"] = relationship(back_populates="sessions")


class BYOKCredentialModel(Base):
    """Encrypted Bring-Your-Own-Key credentials stored at rest."""

    __tablename__ = "byok_credentials"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    org_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=True, index=True
    )
    user_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True
    )
    team_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("teams.id", ondelete="CASCADE"), nullable=True, index=True
    )
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    encrypted_api_key: Mapped[str] = mapped_column(Text, nullable=False)
    key_fingerprint: Mapped[str] = mapped_column(String(50), nullable=False)
    # Self-hosted or proxied endpoints (Ollama, vLLM, an OpenAI-compatible gateway).
    api_base: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[int] = mapped_column(
        BigInteger, default=lambda: int(time.time()), nullable=False
    )


class ApiTokenModel(Base):
    """A member's personal API token: only a hash of its secret is stored.

    Protected by PostgreSQL Row-Level Security (RLS); the token names its organization so it can
    be looked up under that organization's context.
    """

    __tablename__ = "api_tokens"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    org_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    secret_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[int] = mapped_column(
        BigInteger, default=lambda: int(time.time()), nullable=False
    )
    expires_at: Mapped[int] = mapped_column(BigInteger, nullable=False)
    last_used_at: Mapped[int | None] = mapped_column(BigInteger, nullable=True)


class RepositorySetupModel(Base):
    """How to prepare a folder of a repository before the agent works there.

    ``repository`` (``owner/name``) and ``folder`` (a path inside it, empty for its root) are
    shell-style patterns. Protected by PostgreSQL Row-Level Security (RLS).
    """

    __tablename__ = "repository_setups"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    org_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    repository: Mapped[str] = mapped_column(String(255), nullable=False)
    folder: Mapped[str] = mapped_column(String(255), nullable=False, default="", server_default="")
    command: Mapped[str] = mapped_column(Text, nullable=False)
    timeout_seconds: Mapped[int] = mapped_column(
        Integer, nullable=False, default=900, server_default="900"
    )
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[int] = mapped_column(
        BigInteger, default=lambda: int(time.time()), nullable=False
    )
    updated_at: Mapped[int] = mapped_column(
        BigInteger, default=lambda: int(time.time()), nullable=False
    )


class SessionQueuedMessageModel(Base):
    """A message sent while a turn ran: steering it, or queued for after it.

    ``status`` goes from ``pending`` to ``taken``, ``started`` or ``cancelled``. Protected by
    PostgreSQL Row-Level Security (RLS).
    """

    __tablename__ = "session_queued_messages"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    session_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    org_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    author_user_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    delivery: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    created_at: Mapped[int] = mapped_column(
        BigInteger, default=lambda: int(time.time()), nullable=False
    )


class PolicyModel(Base):
    """Hierarchical policy definitions for Org, Team, or User."""

    __tablename__ = "policies"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    org_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    team_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("teams.id", ondelete="CASCADE"), nullable=True, index=True
    )
    user_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True
    )
    policy_type: Mapped[str] = mapped_column(String(50), nullable=False)  # 'org', 'team', 'user'
    policy_data: Mapped[dict[str, Any]] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"), default=dict
    )
    updated_at: Mapped[int] = mapped_column(
        BigInteger, default=lambda: int(time.time()), nullable=False
    )


class OrgSettingModel(Base):
    """Organization-level configuration defaults and lockouts for a domain."""

    __tablename__ = "org_settings"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    org_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    domain: Mapped[str] = mapped_column(String(64), nullable=False)
    config: Mapped[dict[str, Any]] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"), default=dict
    )
    locked_keys: Mapped[list[str]] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"), default=list
    )
    created_at: Mapped[int] = mapped_column(
        BigInteger, default=lambda: int(time.time()), nullable=False
    )
    updated_at: Mapped[int] = mapped_column(
        BigInteger, default=lambda: int(time.time()), nullable=False
    )


class UserSettingModel(Base):
    """User-specific overrides for a configuration domain."""

    __tablename__ = "user_settings"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    org_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    domain: Mapped[str] = mapped_column(String(64), nullable=False)
    overrides: Mapped[dict[str, Any]] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"), default=dict
    )
    created_at: Mapped[int] = mapped_column(
        BigInteger, default=lambda: int(time.time()), nullable=False
    )
    updated_at: Mapped[int] = mapped_column(
        BigInteger, default=lambda: int(time.time()), nullable=False
    )


class UserCredentialModel(Base):
    """Encrypted user credentials (OAuth tokens, personal keys)."""

    __tablename__ = "user_credentials"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    org_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    encrypted_secret: Mapped[str] = mapped_column(Text, nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"), default=dict
    )
    created_at: Mapped[int] = mapped_column(
        BigInteger, default=lambda: int(time.time()), nullable=False
    )
    updated_at: Mapped[int] = mapped_column(
        BigInteger, default=lambda: int(time.time()), nullable=False
    )


class KnowledgeBaseModel(Base):
    """PGVector semantic knowledge collection."""

    __tablename__ = "knowledge_bases"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    org_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True
    )
    scope: Mapped[str] = mapped_column(String(20), default="org", nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    vector_index: Mapped[str] = mapped_column(String(100), default="pgvector_hnsw_default")
    embedding_model: Mapped[str] = mapped_column(
        String(100), default="text-embedding-3-small (1536d)"
    )
    hybrid_search: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[int] = mapped_column(
        BigInteger, default=lambda: int(time.time()), nullable=False
    )
    updated_at: Mapped[int] = mapped_column(
        BigInteger, default=lambda: int(time.time()), nullable=False
    )

    documents: Mapped[list["KnowledgeDocumentModel"]] = relationship(
        back_populates="knowledge_base", cascade="all, delete-orphan"
    )
    chunks: Mapped[list["KnowledgeChunkModel"]] = relationship(
        back_populates="knowledge_base", cascade="all, delete-orphan"
    )


class KnowledgeDocumentModel(Base):
    """Individual document ingested into a Knowledge Base."""

    __tablename__ = "knowledge_documents"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    kb_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("knowledge_bases.id", ondelete="CASCADE"), nullable=False, index=True
    )
    org_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    source_type: Mapped[str] = mapped_column(String(50), default="text")
    content: Mapped[str] = mapped_column(Text, nullable=False)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"), default=dict
    )
    created_at: Mapped[int] = mapped_column(
        BigInteger, default=lambda: int(time.time()), nullable=False
    )

    knowledge_base: Mapped["KnowledgeBaseModel"] = relationship(back_populates="documents")
    chunks: Mapped[list["KnowledgeChunkModel"]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )


class KnowledgeChunkModel(Base):
    """Vectorized text chunk for semantic similarity search."""

    __tablename__ = "knowledge_chunks"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    doc_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("knowledge_documents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    kb_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("knowledge_bases.id", ondelete="CASCADE"), nullable=False, index=True
    )
    org_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[Any] = mapped_column(Vector(1536), nullable=False)
    token_count: Mapped[int] = mapped_column(Integer, default=0)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"), default=dict
    )
    created_at: Mapped[int] = mapped_column(
        BigInteger, default=lambda: int(time.time()), nullable=False
    )

    knowledge_base: Mapped["KnowledgeBaseModel"] = relationship(back_populates="chunks")
    document: Mapped["KnowledgeDocumentModel"] = relationship(back_populates="chunks")


class CustomSkillModel(Base):
    """Custom prompt and operational skills loadable by agents."""

    __tablename__ = "custom_skills"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    org_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True
    )
    scope: Mapped[str] = mapped_column(String(20), default="org", nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    body: Mapped[str] = mapped_column(Text, default="", nullable=False)
    runtime: Mapped[str | None] = mapped_column(String(50), default="text", nullable=True)
    signature: Mapped[str | None] = mapped_column(String(255), default="", nullable=True)
    code: Mapped[str | None] = mapped_column(Text, default="", nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[int] = mapped_column(
        BigInteger, default=lambda: int(time.time()), nullable=False
    )
    updated_at: Mapped[int] = mapped_column(
        BigInteger, default=lambda: int(time.time()), nullable=False
    )


class McpServerEntityModel(Base):
    """Registered external and built-in Model Context Protocol servers."""

    __tablename__ = "mcp_servers"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    org_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True
    )
    scope: Mapped[str] = mapped_column(String(20), default="org", nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    transport: Mapped[str] = mapped_column(String(50), default="stdio")
    endpoint_or_command: Mapped[str] = mapped_column(Text, nullable=False)
    guidance: Mapped[str] = mapped_column(Text, default="")
    auth_type: Mapped[str] = mapped_column(String(50), default="none")
    encrypted_auth: Mapped[dict[str, Any]] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"), default=dict
    )
    tools: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"), default=list
    )
    status: Mapped[str] = mapped_column(String(50), default="connected")
    last_probed: Mapped[str] = mapped_column(String(100), default="Just now")
    is_builtin: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[int] = mapped_column(
        BigInteger, default=lambda: int(time.time()), nullable=False
    )
    updated_at: Mapped[int] = mapped_column(
        BigInteger, default=lambda: int(time.time()), nullable=False
    )


class McpMemberTokenModel(Base):
    """One member's encrypted OAuth tokens for an organization MCP server."""

    __tablename__ = "mcp_member_tokens"

    server_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("mcp_servers.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    org_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    tokens: Mapped[dict[str, Any]] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"), nullable=False, default=dict
    )
    updated_at: Mapped[int] = mapped_column(BigInteger, nullable=False)


class AgentPersonaEntityModel(Base):
    """Database entity for custom agent personas."""

    __tablename__ = "agent_personas"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    org_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True
    )
    scope: Mapped[str] = mapped_column(String(20), default="org", nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    role_title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    system_prompt: Mapped[str] = mapped_column(Text, nullable=False)
    model: Mapped[str] = mapped_column(String(255), nullable=False)
    whitelisted_tools: Mapped[list[str]] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"), default=list
    )
    skill_ids: Mapped[list[str]] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"), default=list
    )
    knowledge_base_ids: Mapped[list[str]] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"), default=list
    )
    icon: Mapped[str] = mapped_column(String(50), default="bot")
    sandbox_image: Mapped[str] = mapped_column(String(255), default="python:3.12-slim")
    sandbox_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    cpu_limit: Mapped[str] = mapped_column(String(50), default="")
    memory_limit: Mapped[str] = mapped_column(String(50), default="")
    temperature: Mapped[float | None] = mapped_column(Float, nullable=True)
    thinking_budget_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reasoning_effort: Mapped[str | None] = mapped_column(String(50), nullable=True)
    max_turns: Mapped[int | None] = mapped_column(Integer, nullable=True)
    active_tools: Mapped[list[str]] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"), default=list
    )
    # The built-in agent this row replaces for the organization (None for an ordinary agent).
    overrides_builtin: Mapped[str | None] = mapped_column(String(64), nullable=True)
    enabled: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true", nullable=False
    )
    default_permission_mode: Mapped[str | None] = mapped_column(String(20), nullable=True)
    created_at: Mapped[int] = mapped_column(
        BigInteger, default=lambda: int(time.time()), nullable=False
    )
    updated_at: Mapped[int] = mapped_column(
        BigInteger, default=lambda: int(time.time()), nullable=False
    )


class SubagentConfigEntityModel(Base):
    """Database entity for specialized subagent configs."""

    __tablename__ = "subagent_configs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    org_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    role_title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    system_prompt: Mapped[str] = mapped_column(Text, nullable=False)
    model: Mapped[str | None] = mapped_column(String(255), nullable=True)
    max_turns: Mapped[int] = mapped_column(Integer, default=5)
    whitelisted_tools: Mapped[list[str]] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"), default=lambda: ["*"]
    )
    temperature: Mapped[float] = mapped_column(Float, default=0.2)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[int] = mapped_column(
        BigInteger, default=lambda: int(time.time()), nullable=False
    )
    updated_at: Mapped[int] = mapped_column(
        BigInteger, default=lambda: int(time.time()), nullable=False
    )


class TurnTraceModel(Base):
    """Durable trace of one driver turn: tools, diffs, approvals, tokens.

    A turn inserts a ``running`` row up front and the same row is updated to
    ``completed``/``error`` when the stream ends, so a crash mid-turn leaves a recoverable
    marker rather than silence. Protected by PostgreSQL Row-Level Security (RLS).
    """

    __tablename__ = "turn_traces"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    session_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    org_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    turn_index: Mapped[int] = mapped_column(Integer, nullable=False)
    started_at: Mapped[int] = mapped_column(BigInteger, nullable=False)
    ended_at: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    reasoning_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    tool_calls: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"), default=list
    )
    diff_paths: Mapped[list[str]] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"), default=list
    )
    approvals: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"), default=list
    )
    tokens: Mapped[dict[str, Any]] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"), default=dict
    )
    # Files the user attached to this turn (id, name, mime, size); the bytes stay in the
    # attachments table, the trace only needs to point at them.
    attachments: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"), default=list, server_default="[]"
    )
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # The replica running the turn and its latest sign of life. A replica that dies stops
    # beating, so its turns can be recovered by a peer without touching healthy replicas' work.
    owner_instance: Mapped[str | None] = mapped_column(String(64), nullable=True)
    heartbeat_at: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    # How many times this task has been resumed after an interruption, and the replica that
    # claimed the resume of this (interrupted) row, so exactly one replica resumes it.
    resume_attempt: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    resumed_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # The question this turn is paused on while it waits for a person. It lives on the row, not
    # only in a replica's memory, so a restart cannot make the question disappear.
    pending_decision: Mapped[dict[str, Any] | None] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"), nullable=True
    )


class SessionMessageModel(Base):
    """One message of a session's durable LLM transcript.

    Append-only and ordered by ``seq``; unlike ``sessions.conversation_history`` (what the
    cockpit renders) it keeps assistant tool calls and tool results exactly as the model
    saw them, so an agent turn can resume after a restart, a fork or on another replica.
    """

    __tablename__ = "session_messages"
    __table_args__ = (UniqueConstraint("session_id", "seq", name="uq_session_messages_seq"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    session_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    org_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str | None] = mapped_column(Text, nullable=True)
    tool_calls: Mapped[list[dict[str, Any]] | None] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"), nullable=True
    )
    tool_call_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    reasoning: Mapped[str | None] = mapped_column(Text, nullable=True)
    reasoning_blocks: Mapped[list[dict[str, Any]] | None] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"), nullable=True
    )
    created_at: Mapped[int] = mapped_column(BigInteger, nullable=False)


class SessionCompactionModel(Base):
    """Compaction checkpoints for crash-resilient session rehydration.

    Preserves the compacted prefix (system prompt, anchor user request, summary bridge)
    up to ``boundary_seq`` so that sessions with hundreds of steps can rehydrate instantly
    without losing history or recompacting from scratch. Full raw messages remain in
    ``session_messages``.
    """

    __tablename__ = "session_compactions"
    __table_args__ = (
        UniqueConstraint("session_id", "boundary_seq", name="uq_session_compactions_boundary"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    session_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    org_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    boundary_seq: Mapped[int] = mapped_column(Integer, nullable=False)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    # model, model_retry or digest (see agent_core.compaction_summary); legacy before 049.
    summary_status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="legacy", server_default="legacy"
    )
    compacted_messages: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"), nullable=False
    )
    tokens_before: Mapped[int] = mapped_column(Integer, nullable=False)
    tokens_after: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[int] = mapped_column(BigInteger, nullable=False)


class SessionGoalModel(Base):
    """Goals and safety budgets for autonomous multi-turn continuations.

    Persists the overarching goal, completion status, safety caps (wall time, tokens, cost),
    and execution tallies across automatic turn continuations.
    """

    __tablename__ = "session_goals"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    session_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    org_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    description: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    max_wall_time_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_total_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    consumed_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    consumed_cost_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    continuation_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_continuations: Mapped[int] = mapped_column(Integer, nullable=False, default=10)
    stop_condition: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[int] = mapped_column(BigInteger, nullable=False)
    updated_at: Mapped[int] = mapped_column(BigInteger, nullable=False)
    completed_at: Mapped[int | None] = mapped_column(BigInteger, nullable=True)


class SubagentTranscriptModel(Base):
    """Subagent execution transcripts and structured delegation results.

    Persists delegated subagent messages, tool calls, results, changed files,
    artefacts, and token/cost metrics with Row-Level Security.
    """

    __tablename__ = "subagent_transcripts"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    session_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    org_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    subagent_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(64), nullable=False)
    task: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="completed")
    turns_taken: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    tool_calls_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    changed_files: Mapped[list[Any]] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"), nullable=False, default=list
    )
    artefacts: Mapped[list[Any]] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"), nullable=False, default=list
    )
    messages: Mapped[list[Any]] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"), nullable=False, default=list
    )
    created_at: Mapped[int] = mapped_column(BigInteger, nullable=False)


class SessionArtifactModel(Base):
    """Durable session artifact deliverables (markdown, diffs, specs, diagrams).

    Stored independently of sandbox pod lifecycles with forced Row-Level Security.
    """

    __tablename__ = "session_artifacts"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    session_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    org_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    artifact_type: Mapped[str] = mapped_column(String(32), nullable=False, default="markdown")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB().with_variant(JSON(), "sqlite"), nullable=False, default=dict
    )
    created_at: Mapped[int] = mapped_column(BigInteger, nullable=False)
    updated_at: Mapped[int] = mapped_column(BigInteger, nullable=False)


class SessionArtifactVersionModel(Base):
    """Every saved version of a session artifact, so an edit never loses what it replaced.

    Forced Row-Level Security by organization, like the artifacts they belong to.
    """

    __tablename__ = "session_artifact_versions"
    __table_args__ = (UniqueConstraint("artifact_id", "version", name="uq_artifact_version"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    artifact_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("session_artifacts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    session_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    org_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[int] = mapped_column(BigInteger, nullable=False)


class SessionWaitModel(Base):
    """Durable session wait requests (timers, background process exits, GitHub CI/reviews).

    Persisted with forced Row-Level Security so wait state and scheduled resumptions
    survive container reboots, crashes, and multi-replica leader handovers.
    """

    __tablename__ = "session_waits"
    __table_args__ = (Index("ix_session_waits_status_deadline", "status", "deadline"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    session_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    org_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    target: Mapped[str] = mapped_column(String(255), nullable=False)
    deadline: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="waiting")
    wake_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB().with_variant(JSON(), "sqlite"), nullable=False, default=dict
    )
    created_at: Mapped[int] = mapped_column(BigInteger, nullable=False)
    claimed_at: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    completed_at: Mapped[int | None] = mapped_column(BigInteger, nullable=True)


class AttachmentModel(Base):
    """A file uploaded into a session, addressed by its content hash.

    ``sha256`` is indexed so the upload service can dedupe identical bytes within a
    session instead of streaming the same blob twice. RLS scopes every row to its
    ``org_id``, matching the rest of the tenant-owned tables.
    """

    __tablename__ = "attachments"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    session_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    org_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    mime: Mapped[str] = mapped_column(String(255), nullable=False)
    size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    workspace_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    created_at: Mapped[int] = mapped_column(
        BigInteger, default=lambda: int(time.time()), nullable=False
    )


class MemoryModel(Base):
    """Deliberately stored long-term knowledge.

    ``embedding`` is the same 1536-dim component ``knowledge_chunks`` uses, so ``recall``
    can reuse the existing pgvector cosine-search helpers. ``last_used_at`` drives decay
    ranking and ``confidence`` lets the writer down-weight uncertain memories.
    ``user_id`` stays nullable so an org-scoped memory needs no single owner.
    """

    __tablename__ = "memories"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    org_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True
    )
    scope: Mapped[str] = mapped_column(String(20), default="user", nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(20), default="fact", nullable=False, index=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[Any] = mapped_column(Vector(1536), nullable=False)
    source_session_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("sessions.id", ondelete="SET NULL"), nullable=True, index=True
    )
    confidence: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
    created_at: Mapped[int] = mapped_column(
        BigInteger, default=lambda: int(time.time()), nullable=False
    )
    last_used_at: Mapped[int | None] = mapped_column(BigInteger, nullable=True)


class SessionSummaryModel(Base):
    """A distilled summary of a completed turn range.

    Written on turn completion by a cheap model; the embedding lets a new session find
    related past work. ``turn_start``/``turn_end`` record which turns the summary covers
    so a summary is never mistaken for the full history.
    """

    __tablename__ = "session_summaries"

    session_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("sessions.id", ondelete="CASCADE"), primary_key=True
    )
    org_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[Any] = mapped_column(Vector(1536), nullable=False)
    turn_start: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    turn_end: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    updated_at: Mapped[int] = mapped_column(
        BigInteger, default=lambda: int(time.time()), nullable=False
    )


class SlackWorkspaceModel(Base):
    """Binding of a Slack workspace (team) to the organization that owns it.

    Deliberately not row-level-secured: the lookup happens before any tenant is known (the
    Slack event only carries the team id). Writes go through the admin-only API.
    """

    __tablename__ = "slack_workspaces"

    team_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    org_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    bound_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[int] = mapped_column(BigInteger, nullable=False)


class SlackBotModel(Base):
    """One Slack bot of an organization: its own Slack app, agent or router, and policy.

    Tokens are sealed with ``CredentialCipher`` and never leave the API. Protected by RLS; the
    bot manager lists every organization's enabled bots on the schema-owner connection.
    """

    __tablename__ = "slack_bots"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    org_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    bot_token_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    app_token_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    bot_token_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    app_token_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Discovered with auth.test when the tokens are saved.
    team_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    team_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    bot_user_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    app_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    handle: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # The bot's slash command; NULL names it after the handle.
    slash_command: Mapped[str | None] = mapped_column(String(32), nullable=True)
    agent_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    response_mode: Mapped[str] = mapped_column(String(16), nullable=False, default="thread")
    respond_in_dms: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    listen_channels: Mapped[list[str]] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"), nullable=False, default=list
    )
    private_reaction: Mapped[str] = mapped_column(
        String(64), nullable=False, default="white_check_mark"
    )
    private_dm_result: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    private_dm_updates: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # The router (classifier agent, routes, dismiss, fallback); NULL answers with ``agent_id``.
    router: Mapped[dict[str, Any] | None] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"), nullable=True
    )
    # Overrides of the organization's ``slack`` settings, key by key.
    policy: Mapped[dict[str, Any]] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"), nullable=False, default=dict
    )
    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[int] = mapped_column(BigInteger, nullable=False)
    updated_at: Mapped[int] = mapped_column(BigInteger, nullable=False)


class SlackRouteDecisionModel(Base):
    """What a bot's router decided for one message, for the decision log. Protected by RLS."""

    __tablename__ = "slack_route_decisions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    org_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    bot_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("slack_bots.id", ondelete="CASCADE"), nullable=False
    )
    channel_id: Mapped[str] = mapped_column(String(64), nullable=False)
    thread_ts: Mapped[str] = mapped_column(String(64), nullable=False)
    slack_user_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    excerpt: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # What the classifier answered, and the route that applied after threshold and fallback.
    route: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    applied: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    model: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[int] = mapped_column(BigInteger, nullable=False)

    __table_args__ = (Index("ix_slack_route_decisions_bot_time", "bot_id", "created_at"),)


class ScheduledMissionModel(Base):
    """A prompt the platform runs on a cron schedule as a normal session.

    ``next_run_at`` doubles as the claim token for the scheduler. Protected by RLS.
    """

    __tablename__ = "scheduled_missions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    org_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    cron: Mapped[str] = mapped_column(String(120), nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="UTC")
    agent_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    git_repo: Mapped[str | None] = mapped_column(String(255), nullable=True)
    git_branch: Mapped[str | None] = mapped_column(String(255), nullable=True)
    permission_mode: Mapped[str | None] = mapped_column(String(16), nullable=True)
    slack_channel: Mapped[str | None] = mapped_column(String(64), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    next_run_at: Mapped[int | None] = mapped_column(BigInteger, nullable=True, index=True)
    last_run_at: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    last_session_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[int] = mapped_column(BigInteger, nullable=False)


class SessionTemplateModel(Base):
    """A reusable way to start a session; ``user_id`` NULL means organization-wide."""

    __tablename__ = "session_templates"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    org_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    agent_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    git_repo: Mapped[str | None] = mapped_column(String(255), nullable=True)
    git_branch: Mapped[str | None] = mapped_column(String(255), nullable=True)
    permission_mode: Mapped[str | None] = mapped_column(String(16), nullable=True)
    created_at: Mapped[int] = mapped_column(BigInteger, nullable=False)


class SandboxProcessModel(Base):
    """A background process a session started in its sandbox."""

    __tablename__ = "sandbox_processes"

    session_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("sessions.id", ondelete="CASCADE"), primary_key=True
    )
    process_id: Mapped[str] = mapped_column(String(16), primary_key=True)
    org_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    command: Mapped[str] = mapped_column(Text, nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    port: Mapped[int | None] = mapped_column(Integer, nullable=True)
    exit_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    started_at: Mapped[int] = mapped_column(BigInteger, nullable=False)
    ended_at: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    updated_at: Mapped[int] = mapped_column(BigInteger, nullable=False)


class SandboxCatalogueModel(Base):
    """An image an organization offers for sessions, with what it can do."""

    __tablename__ = "sandbox_catalogue"
    __table_args__ = (UniqueConstraint("org_id", "name", name="uq_sandbox_catalogue_org_name"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    org_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    image: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    capabilities: Mapped[list[str]] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"), nullable=False, default=list
    )
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[int] = mapped_column(BigInteger, nullable=False)
    profile: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    cpu_limit: Mapped[str] = mapped_column(String(16), nullable=False, default="")
    memory_limit: Mapped[str] = mapped_column(String(16), nullable=False, default="")
    disk_size: Mapped[str] = mapped_column(String(16), nullable=False, default="")
    release_after_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # [{name, value, secret}]; a secret's value is sealed with the credential cipher.
    env: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"), nullable=False, default=list
    )


class GithubWatchModel(Base):
    """A session following a repository's pull request (or branch) for GitHub events."""

    __tablename__ = "github_watches"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    org_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    session_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    repo: Mapped[str] = mapped_column(String(200), nullable=False)
    pr_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    branch: Mapped[str | None] = mapped_column(String(255), nullable=True)
    wake: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    wakes_used: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_by: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[int] = mapped_column(BigInteger, nullable=False)
    # Events that arrived while the agent was not asked to wake: shown to it on its next turn.
    pending_events: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"), nullable=False, default=list
    )


class GithubInstallationModel(Base):
    """A GitHub App installation mapped to the organization that may use it.

    Not row-level-secured, like ``slack_workspaces``: the installation id arrives on a GitHub
    redirect before any tenant is known. Writes go through the admin-only, state-signed flow.
    """

    __tablename__ = "github_installations"

    installation_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    org_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    account_login: Mapped[str] = mapped_column(String(255), nullable=False)
    bound_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[int] = mapped_column(BigInteger, nullable=False)


class SlackWorkspaceQuotaModel(Base):
    """Per-workspace caps and counters for the unauthenticated Slack path.

    ``team_id`` is the natural key (one row per Slack workspace). The ``*_cap`` columns hold
    the configured limits; the remaining counters and ``hour_window_start`` are the mutable
    state the pre-dispatch quota check reads and updates.
    """

    __tablename__ = "slack_workspace_quotas"

    team_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    org_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    monthly_usd_cap: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    turns_per_hour: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_concurrent_sessions: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    current_month_spend_usd: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    turns_this_hour: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    hour_window_start: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[int] = mapped_column(
        BigInteger, default=lambda: int(time.time()), nullable=False
    )
    updated_at: Mapped[int] = mapped_column(
        BigInteger, default=lambda: int(time.time()), nullable=False
    )


class AuditEventModel(Base):
    """Durable governance audit event.

    One row per tool execution, approval request/decision, model call, credential change
    and settings mutation, so "who ran what, when, with what result" is answerable after
    the fact. ``approved_by`` carries the approver attribution the spec's approval trail
    requires. ``redacted``/``redaction_count`` are the durable marker that a
    redaction happened, without ever storing the removed value. Protected by RLS.
    """

    __tablename__ = "audit_events"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    org_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    actor_user_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    session_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    event_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    tool_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    risk_level: Mapped[str | None] = mapped_column(String(20), nullable=True)
    approved_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    result_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    redacted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    redaction_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"), default=dict
    )


# Indexes for rapid tenant lookups
Index("ix_sessions_org_created", SessionModel.org_id, SessionModel.created_at.desc())
Index(
    "ix_turn_traces_session_turn",
    TurnTraceModel.session_id,
    TurnTraceModel.turn_index,
)
Index("ix_policies_org_type", PolicyModel.org_id, PolicyModel.policy_type)
Index("uq_org_settings_domain", OrgSettingModel.org_id, OrgSettingModel.domain, unique=True)
Index("uq_user_settings_domain", UserSettingModel.user_id, UserSettingModel.domain, unique=True)
Index(
    "uq_user_credentials_provider",
    UserCredentialModel.user_id,
    UserCredentialModel.provider,
    unique=True,
)
Index(
    "ix_custom_skills_org_user",
    CustomSkillModel.org_id,
    CustomSkillModel.user_id,
    CustomSkillModel.name,
)
Index(
    "ix_mcp_servers_org_user",
    McpServerEntityModel.org_id,
    McpServerEntityModel.user_id,
    McpServerEntityModel.name,
)
Index("ix_knowledge_chunks_kb", KnowledgeChunkModel.kb_id, KnowledgeChunkModel.chunk_index)
# Dedupe point for uploads: the same bytes in one session must map to one stored blob.
Index(
    "uq_attachments_session_sha256",
    AttachmentModel.session_id,
    AttachmentModel.sha256,
    unique=True,
)
Index(
    "ix_memories_org_user_scope",
    MemoryModel.org_id,
    MemoryModel.user_id,
    MemoryModel.scope,
)
# Admin audit listing and the retention sweep both scan one org newest-first over
# (created_at, id); the composite index keeps that a keyset lookup, not a sort.
Index(
    "ix_audit_events_org_created",
    AuditEventModel.org_id,
    AuditEventModel.created_at.desc(),
    AuditEventModel.id.desc(),
)


class SessionEventCounterModel(Base):
    """Per-session event sequence shared by every API replica.

    Replicas publishing events for the same session must agree on ordering, so the next
    sequence number comes from this row. It holds only an opaque session id and a counter -
    no tenant data - and is read before a tenant context exists, so it has no row-level
    security.
    """

    __tablename__ = "session_event_counters"

    session_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    last_seq: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    updated_at: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)


class SlackPairingCodeModel(Base):
    """A one-time code a Slack user got from a bot's ``pair`` command.

    Stored in PostgreSQL so the member can finish pairing on any API replica. Only a hash of
    the code is kept, and like ``slack_workspaces`` the table has no row-level security: the
    code is claimed before the claimant's organization is known to the Slack side.
    """

    __tablename__ = "slack_pairing_codes"

    code_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    slack_user_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    team_id: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)


class UsageEventModel(Base):
    """One model call's tokens and cost: the persisted usage ledger.

    Append-only. Budgets, dashboards and the Slack workspace spend are all computed from
    these rows, so they survive restarts and agree across replicas. Protected by RLS.
    """

    __tablename__ = "usage_events"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    org_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Not a foreign key: shared Slack threads are billed to a synthetic member
    # ("slack_global") that has no users row.
    user_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    session_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    model: Mapped[str] = mapped_column(String(255), nullable=False)
    prompt_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cache_read_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cache_creation_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    key_source: Mapped[str] = mapped_column(String(16), nullable=False, default="platform")
    created_at: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)

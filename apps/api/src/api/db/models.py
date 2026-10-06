"""SQLAlchemy 2.0 multi-tenant database models with Row-Level Security support."""

import time
from typing import Any, ClassVar

from sqlalchemy import BigInteger, ForeignKey, Index, String, Text
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


# Indexes for rapid tenant lookups
Index("ix_sessions_org_created", SessionModel.org_id, SessionModel.created_at.desc())
Index("ix_policies_org_type", PolicyModel.org_id, PolicyModel.policy_type)
Index("uq_org_settings_domain", OrgSettingModel.org_id, OrgSettingModel.domain, unique=True)
Index("uq_user_settings_domain", UserSettingModel.user_id, UserSettingModel.domain, unique=True)
Index(
    "uq_user_credentials_provider",
    UserCredentialModel.user_id,
    UserCredentialModel.provider,
    unique=True,
)

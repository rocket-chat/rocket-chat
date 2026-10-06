"""Hierarchical Configuration Engine resolving System Defaults -> Org -> Team -> User overrides."""

import fnmatch
import logging

from specifications.interfaces.config import (
    ConfigEngineProtocol,
    ResolvedConfig,
)

from .crypto import CredentialCipher
from .exceptions import PolicyViolationError
from .models import (
    OrgPolicy,
    TeamPolicy,
    UserOverride,
)

logger = logging.getLogger(__name__)

SYSTEM_DEFAULT_MODEL = "openrouter/anthropic/claude-3.7-sonnet"
SYSTEM_DEFAULT_IMAGE = "ghcr.io/platform/dev-base:latest"
SYSTEM_DEFAULT_IDLE_TIMEOUT = 30
SYSTEM_DEFAULT_GIT_NAME = "Rocket Agent"
SYSTEM_DEFAULT_GIT_EMAIL = "agent@rocket-chat.internal"


class ConfigEngine(ConfigEngineProtocol):
    """Calculates effective session configurations by cascading hierarchical tiers and enforcing policy boundaries."""

    def __init__(self, cipher: CredentialCipher | None = None) -> None:
        self.cipher = cipher or CredentialCipher()
        self._org_policies: dict[str, OrgPolicy] = {}
        self._team_policies: dict[str, TeamPolicy] = {}
        self._user_overrides: dict[str, UserOverride] = {}

    def set_org_policy(self, policy: OrgPolicy) -> None:
        """Register or update an Organization policy."""
        self._org_policies[policy.org_id] = policy

    def set_team_policy(self, policy: TeamPolicy) -> None:
        """Register or update a Team policy."""
        self._team_policies[policy.team_id] = policy

    def set_user_override(self, override: UserOverride) -> None:
        """Register or update a User override."""
        self._user_overrides[override.user_id] = override

    async def set_user_byok(
        self,
        user_id: str,
        provider: str,
        api_key: str,
    ) -> None:
        """Encrypt and persist a user's personal BYOK credential."""
        encrypted = self.cipher.encrypt(api_key)
        override = self._user_overrides.get(user_id)
        if override is None:
            override = UserOverride(
                user_id=user_id,
                org_id="default",
                byok_provider=provider,
                byok_api_key_encrypted=encrypted,
            )
            self._user_overrides[user_id] = override
        else:
            override.byok_provider = provider
            override.byok_api_key_encrypted = encrypted

    async def set_org_byok(
        self,
        org_id: str,
        provider: str,
        api_key: str,
    ) -> None:
        """Encrypt and persist an Organization's master BYOK credential."""
        encrypted = self.cipher.encrypt(api_key)
        policy = self._org_policies.get(org_id)
        if policy is None:
            policy = OrgPolicy(
                org_id=org_id,
                byok_provider=provider,
                byok_api_key_encrypted=encrypted,
            )
            self._org_policies[org_id] = policy
        else:
            policy.byok_provider = provider
            policy.byok_api_key_encrypted = encrypted

    def _matches_any_pattern(self, candidate: str, patterns: list[str]) -> bool:
        """Evaluate if a candidate string matches any wildcard pattern (e.g. 'anthropic/*')."""
        return any(fnmatch.fnmatchcase(candidate, pattern) for pattern in patterns)

    def _validate_model_policy(self, model: str, org_policy: OrgPolicy) -> None:
        """Ensure active model satisfies organization whitelist and blacklist constraints."""
        if org_policy.forbidden_models:
            if self._matches_any_pattern(model, org_policy.forbidden_models):
                raise PolicyViolationError(
                    f"Model '{model}' violates Organization policy (forbidden by policy: {org_policy.forbidden_models})"
                )

        if org_policy.allowed_models:
            if not self._matches_any_pattern(model, org_policy.allowed_models):
                raise PolicyViolationError(
                    f"Model '{model}' violates Organization policy (not in allowed list: {org_policy.allowed_models})"
                )

    def _validate_image_policy(self, image: str, org_policy: OrgPolicy) -> None:
        """Ensure active container image satisfies organization whitelist."""
        if org_policy.allowed_images:
            if not self._matches_any_pattern(image, org_policy.allowed_images):
                raise PolicyViolationError(
                    f"Container image '{image}' violates Organization policy (allowed: {org_policy.allowed_images})"
                )

    async def resolve_session_config(
        self,
        tenant_org_id: str,
        team_id: str | None,
        user_id: str,
    ) -> ResolvedConfig:
        """
        Calculate effective runtime configuration cascading through:
        System Defaults -> Org Policy -> Team Policy -> User Override.

        Raises PolicyViolationError if a team or user override violates organization boundaries.
        """
        org_policy = self._org_policies.get(tenant_org_id) or OrgPolicy(org_id=tenant_org_id)
        team_policy = self._team_policies.get(team_id) if team_id else None
        user_override = self._user_overrides.get(user_id)

        # 1. Active Model Resolution
        active_model = SYSTEM_DEFAULT_MODEL
        if org_policy.default_model:
            active_model = org_policy.default_model
        if team_policy and team_policy.default_model:
            active_model = team_policy.default_model
        if user_override and user_override.preferred_model:
            active_model = user_override.preferred_model

        # Validate model against Organization constraints
        self._validate_model_policy(active_model, org_policy)

        # 2. Container Image Resolution
        active_image = SYSTEM_DEFAULT_IMAGE
        if org_policy.default_image:
            active_image = org_policy.default_image
        if team_policy and team_policy.default_image:
            active_image = team_policy.default_image
        if user_override and user_override.preferred_image:
            active_image = user_override.preferred_image

        # Validate image against Organization constraints
        self._validate_image_policy(active_image, org_policy)

        # 3. BYOK Credential Resolution
        byok_provider = "openrouter"
        byok_api_key = ""

        # Org tier
        if org_policy.byok_provider and org_policy.byok_api_key_encrypted:
            byok_provider = org_policy.byok_provider
            byok_api_key = self.cipher.decrypt(org_policy.byok_api_key_encrypted)

        # Team tier
        if team_policy and team_policy.byok_provider and team_policy.byok_api_key_encrypted:
            byok_provider = team_policy.byok_provider
            byok_api_key = self.cipher.decrypt(team_policy.byok_api_key_encrypted)

        # User tier
        if user_override and user_override.byok_provider and user_override.byok_api_key_encrypted:
            byok_provider = user_override.byok_provider
            byok_api_key = self.cipher.decrypt(user_override.byok_api_key_encrypted)

        # 4. Git Author Credentials
        git_name = SYSTEM_DEFAULT_GIT_NAME
        git_email = SYSTEM_DEFAULT_GIT_EMAIL
        if user_override and user_override.git_author_name:
            git_name = user_override.git_author_name
        if user_override and user_override.git_author_email:
            git_email = user_override.git_author_email

        # 5. Security & Idle Settings
        require_signed = org_policy.require_signed_commits
        idle_timeout = org_policy.idle_timeout_minutes or SYSTEM_DEFAULT_IDLE_TIMEOUT

        # 6. Custom Environment Variables (Merged across tiers)
        custom_env: dict[str, str] = {}
        custom_env.update(org_policy.custom_env_vars)
        if team_policy:
            custom_env.update(team_policy.custom_env_vars)
        if user_override:
            custom_env.update(user_override.custom_env_vars)

        return ResolvedConfig(
            active_model=active_model,
            active_container_image=active_image,
            byok_provider=byok_provider,
            byok_api_key=byok_api_key,
            git_author_name=git_name,
            git_author_email=git_email,
            require_signed_commits=require_signed,
            idle_timeout_minutes=idle_timeout,
            custom_env_vars=custom_env,
        )

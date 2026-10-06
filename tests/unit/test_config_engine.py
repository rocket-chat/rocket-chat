"""Unit tests for ConfigEngine verifying resolution cascade, policy enforcement, and BYOK encryption."""

import pytest
from config_engine import (
    ConfigEngine,
    CredentialCipher,
    CredentialDecryptionError,
    OrgPolicy,
    PolicyViolationError,
    TeamPolicy,
    UserOverride,
)


def test_credential_cipher_aes256_gcm_roundtrip():
    cipher = CredentialCipher(master_key="test-master-key-32-bytes-long!!")
    secret = "sk-ant-api03-very-secret-production-key"

    encrypted = cipher.encrypt(secret)
    assert encrypted != secret
    assert len(encrypted) > 20

    decrypted = cipher.decrypt(encrypted)
    assert decrypted == secret

    # Fingerprint check
    fp = cipher.fingerprint(secret)
    assert fp.startswith("sk-")
    assert fp.endswith("-key")
    assert "very-secret" not in fp


def test_credential_cipher_tamper_detection():
    cipher = CredentialCipher(master_key="test-master-key-32-bytes-long!!")
    encrypted = cipher.encrypt("secret-token")

    # Tamper with the base64 string
    tampered = encrypted[:-4] + "AAAA"
    with pytest.raises(CredentialDecryptionError):
        cipher.decrypt(tampered)

    # Different key fails decryption
    other_cipher = CredentialCipher(master_key="different-master-key-32-bytes!!")
    with pytest.raises(CredentialDecryptionError):
        other_cipher.decrypt(encrypted)


@pytest.mark.asyncio
async def test_system_defaults_resolution():
    engine = ConfigEngine()
    resolved = await engine.resolve_session_config(
        tenant_org_id="org_alpha",
        team_id=None,
        user_id="user_1",
    )

    assert resolved.active_model == "openrouter/anthropic/claude-3.7-sonnet"
    assert resolved.active_container_image == "ghcr.io/platform/dev-base:latest"
    assert resolved.byok_api_key == ""
    assert resolved.idle_timeout_minutes == 30
    assert resolved.require_signed_commits is False


@pytest.mark.asyncio
async def test_org_policy_overrides_system_defaults():
    engine = ConfigEngine()
    engine.set_org_policy(
        OrgPolicy(
            org_id="org_cyber",
            default_model="anthropic/claude-3-5-sonnet",
            default_image="registry.acme.com/dev:2.0",
            idle_timeout_minutes=15,
            require_signed_commits=True,
            custom_env_vars={"ORG_LEVEL": "true"},
        )
    )

    resolved = await engine.resolve_session_config(
        tenant_org_id="org_cyber",
        team_id=None,
        user_id="user_1",
    )

    assert resolved.active_model == "anthropic/claude-3-5-sonnet"
    assert resolved.active_container_image == "registry.acme.com/dev:2.0"
    assert resolved.idle_timeout_minutes == 15
    assert resolved.require_signed_commits is True
    assert resolved.custom_env_vars == {"ORG_LEVEL": "true"}


@pytest.mark.asyncio
async def test_team_policy_overrides_org_policy():
    engine = ConfigEngine()
    engine.set_org_policy(
        OrgPolicy(
            org_id="org_corp",
            default_model="anthropic/claude-3-5-sonnet",
            default_image="corp/base:1.0",
            custom_env_vars={"ENV": "corp"},
        )
    )
    engine.set_team_policy(
        TeamPolicy(
            team_id="team_infra",
            org_id="org_corp",
            default_model="anthropic/claude-3-opus",
            default_image="corp/infra:2.0",
            custom_env_vars={"ENV": "team_infra", "TEAM": "infra"},
        )
    )

    resolved = await engine.resolve_session_config(
        tenant_org_id="org_corp",
        team_id="team_infra",
        user_id="user_2",
    )

    assert resolved.active_model == "anthropic/claude-3-opus"
    assert resolved.active_container_image == "corp/infra:2.0"
    assert resolved.custom_env_vars["ENV"] == "team_infra"
    assert resolved.custom_env_vars["TEAM"] == "infra"


@pytest.mark.asyncio
async def test_user_override_overrides_team_and_org():
    engine = ConfigEngine()
    engine.set_org_policy(
        OrgPolicy(
            org_id="org_space",
            default_model="anthropic/claude-3-5-sonnet",
        )
    )
    engine.set_team_policy(
        TeamPolicy(
            team_id="team_orbit",
            org_id="org_space",
            default_model="anthropic/claude-3-opus",
        )
    )
    engine.set_user_override(
        UserOverride(
            user_id="astronaut_1",
            org_id="org_space",
            team_id="team_orbit",
            preferred_model="anthropic/claude-3-haiku",
            git_author_name="Neil Armstrong",
            git_author_email="neil@nasa.gov",
        )
    )

    resolved = await engine.resolve_session_config(
        tenant_org_id="org_space",
        team_id="team_orbit",
        user_id="astronaut_1",
    )

    assert resolved.active_model == "anthropic/claude-3-haiku"
    assert resolved.git_author_name == "Neil Armstrong"
    assert resolved.git_author_email == "neil@nasa.gov"


@pytest.mark.asyncio
async def test_forbidden_models_policy_violation():
    engine = ConfigEngine()
    engine.set_org_policy(
        OrgPolicy(
            org_id="org_strict",
            forbidden_models=["openai/*", "meta-llama/*"],
        )
    )
    # User attempts to use forbidden model
    engine.set_user_override(
        UserOverride(
            user_id="user_rebel",
            org_id="org_strict",
            preferred_model="openai/gpt-4o",
        )
    )

    with pytest.raises(PolicyViolationError) as exc_info:
        await engine.resolve_session_config(
            tenant_org_id="org_strict",
            team_id=None,
            user_id="user_rebel",
        )

    assert "openai/gpt-4o" in str(exc_info.value)
    assert "violates Organization policy" in str(exc_info.value)


@pytest.mark.asyncio
async def test_allowed_models_whitelist_enforcement():
    engine = ConfigEngine()
    engine.set_org_policy(
        OrgPolicy(
            org_id="org_anthropic_only",
            allowed_models=["anthropic/*"],
            default_model="anthropic/claude-3-5-sonnet",
        )
    )

    # Valid model succeeds
    resolved = await engine.resolve_session_config(
        tenant_org_id="org_anthropic_only",
        team_id=None,
        user_id="user_compliant",
    )
    assert resolved.active_model == "anthropic/claude-3-5-sonnet"

    # Unauthorized model raises PolicyViolationError
    engine.set_user_override(
        UserOverride(
            user_id="user_non_compliant",
            org_id="org_anthropic_only",
            preferred_model="google/gemini-1.5-pro",
        )
    )
    with pytest.raises(PolicyViolationError) as exc_info:
        await engine.resolve_session_config(
            tenant_org_id="org_anthropic_only",
            team_id=None,
            user_id="user_non_compliant",
        )
    assert "google/gemini-1.5-pro" in str(exc_info.value)


@pytest.mark.asyncio
async def test_byok_encryption_and_cascade():
    engine = ConfigEngine()

    # 1. Org BYOK
    await engine.set_org_byok(
        org_id="org_byok",
        provider="anthropic",
        api_key="sk-ant-org-corp-master-key",
    )

    resolved_org = await engine.resolve_session_config(
        tenant_org_id="org_byok",
        team_id=None,
        user_id="user_no_key",
    )
    assert resolved_org.byok_provider == "anthropic"
    assert resolved_org.byok_api_key == "sk-ant-org-corp-master-key"

    # Verify at rest is encrypted
    stored_org_key = engine._org_policies["org_byok"].byok_api_key_encrypted
    assert stored_org_key != "sk-ant-org-corp-master-key"
    assert engine.cipher.decrypt(stored_org_key) == "sk-ant-org-corp-master-key"

    # 2. User personal BYOK overrides Org BYOK
    await engine.set_user_byok(
        user_id="user_with_personal_key",
        provider="openrouter",
        api_key="sk-or-personal-user-key",
    )
    resolved_user = await engine.resolve_session_config(
        tenant_org_id="org_byok",
        team_id=None,
        user_id="user_with_personal_key",
    )
    assert resolved_user.byok_provider == "openrouter"
    assert resolved_user.byok_api_key == "sk-or-personal-user-key"

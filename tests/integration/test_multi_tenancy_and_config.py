"""Alias integration test file conforming to sessions/08_MULTI_TENANCY_SSO_AND_CONFIG_ENGINE.md."""

from tests.integration.test_multi_tenancy_interoperability import (
    db_manager,
    test_encrypted_byok_credential_at_rest,
    test_end_to_end_config_injection_interoperability,
    test_policy_violation_enforcement,
    test_tenant_data_isolation_row_level_security,
)

__all__ = [
    "db_manager",
    "test_encrypted_byok_credential_at_rest",
    "test_end_to_end_config_injection_interoperability",
    "test_policy_violation_enforcement",
    "test_tenant_data_isolation_row_level_security",
]

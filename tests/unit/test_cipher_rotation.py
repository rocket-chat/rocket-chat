"""Credential cipher key rotation and production fail-closed behaviour."""

import pytest
from config_engine.crypto import CredentialCipher
from config_engine.exceptions import CredentialDecryptionError


def test_old_ciphertext_stays_readable_after_rotation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ENCRYPTION_PREVIOUS_KEYS", raising=False)
    sealed = CredentialCipher("old-key-0123456789abcdef0123456789").encrypt("sk-secret")

    monkeypatch.setenv("ENCRYPTION_PREVIOUS_KEYS", "old-key-0123456789abcdef0123456789")
    rotated = CredentialCipher("new-key-0123456789abcdef0123456789")
    assert rotated.decrypt(sealed) == "sk-secret"
    # New writes use the primary key only.
    resealed = rotated.encrypt("sk-secret")
    monkeypatch.delenv("ENCRYPTION_PREVIOUS_KEYS")
    assert CredentialCipher("new-key-0123456789abcdef0123456789").decrypt(resealed) == "sk-secret"


def test_unknown_key_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ENCRYPTION_PREVIOUS_KEYS", raising=False)
    sealed = CredentialCipher("key-a-0123456789abcdef0123456789").encrypt("x")
    with pytest.raises(CredentialDecryptionError):
        CredentialCipher("key-b-0123456789abcdef0123456789").decrypt(sealed)


def test_production_refuses_the_development_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.delenv("ENCRYPTION_MASTER_KEY", raising=False)
    monkeypatch.delenv("ROCKET_ENCRYPTION_KEY", raising=False)
    with pytest.raises(CredentialDecryptionError):
        CredentialCipher()

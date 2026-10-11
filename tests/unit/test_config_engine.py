"""Unit tests for the credential cipher (AES-256-GCM at rest)."""

import pytest
from config_engine import CredentialCipher, CredentialDecryptionError


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

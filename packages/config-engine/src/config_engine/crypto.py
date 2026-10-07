"""AES-256-GCM encryption and decryption utility for BYOK credentials at rest."""

import base64
import os
import secrets
from hashlib import sha256

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .exceptions import CredentialDecryptionError


class CredentialCipher:
    """Provides cryptographic protection for third-party API keys using AES-256-GCM."""

    def __init__(self, master_key: str | bytes | None = None) -> None:
        """Initialize cipher with a 256-bit key or read from ENCRYPTION_MASTER_KEY / ROCKET_ENCRYPTION_KEY env var."""
        raw_key = (
            master_key or os.getenv("ENCRYPTION_MASTER_KEY") or os.getenv("ROCKET_ENCRYPTION_KEY")
        )
        if not raw_key:
            # Generate deterministic fallback for development if none configured
            raw_key = "rocket-chat-default-development-encryption-key-32bytes!!"

        if isinstance(raw_key, str):
            # Ensure key is exactly 32 bytes using SHA-256 derivation
            self._key = sha256(raw_key.encode("utf-8")).digest()
        else:
            if len(raw_key) != 32:
                self._key = sha256(raw_key).digest()
            else:
                self._key = raw_key

        self._aesgcm = AESGCM(self._key)

    def encrypt(self, plaintext: str) -> str:
        """Encrypt plaintext string using AES-256-GCM, returning base64-encoded nonce + ciphertext."""
        nonce = secrets.token_bytes(12)  # 96-bit nonce standard for GCM
        data = plaintext.encode("utf-8")
        ciphertext = self._aesgcm.encrypt(nonce, data, None)
        combined = nonce + ciphertext
        return base64.b64encode(combined).decode("ascii")

    def decrypt(self, encrypted_b64: str) -> str:
        """Decrypt base64-encoded nonce + ciphertext payload; raises CredentialDecryptionError on tamper/mismatch."""
        try:
            combined = base64.b64decode(encrypted_b64.encode("ascii"))
            if len(combined) < 28:  # 12-byte nonce + 16-byte minimum tag
                raise ValueError("Encrypted payload too short")

            nonce = combined[:12]
            ciphertext = combined[12:]
            decrypted = self._aesgcm.decrypt(nonce, ciphertext, None)
            return decrypted.decode("utf-8")
        except Exception as err:
            raise CredentialDecryptionError(f"Failed decrypting credential: {err}") from err

    @staticmethod
    def fingerprint(api_key: str) -> str:
        """Generate a secure preview fingerprint for logging/UI display (e.g. sk-...abcd)."""
        if not api_key or len(api_key) <= 8:
            return "***"
        prefix = api_key[:3]
        suffix = api_key[-4:]
        return f"{prefix}...{suffix}"

    @classmethod
    def mask_secret(cls, value: str) -> str:
        """Return masked representation for write-only credentials."""
        return cls.fingerprint(value)

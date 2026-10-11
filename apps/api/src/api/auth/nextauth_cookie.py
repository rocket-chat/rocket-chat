"""NextAuth session cookie decryption and RequestContext extraction."""

import base64
import json
import logging
import os
import time
from typing import Any

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from starlette.requests import HTTPConnection

from .context import RequestContext

logger = logging.getLogger(__name__)

DEFAULT_NEXTAUTH_SECRET = "rocket-chat-development-nextauth-secret-key-32-chars-min"


def _b64url_decode(s: str) -> bytes:
    """Safely decode base64url string with required padding."""
    rem = len(s) % 4
    if rem > 0:
        s += "=" * (4 - rem)
    return base64.urlsafe_b64decode(s)


def decode_nextauth_token(token_str: str, secret: str) -> dict[str, Any]:
    """
    Decode and decrypt a NextAuth JWT/JWE token.
    Supports both 5-segment JWE (AES-256-GCM encrypted via HKDF derived key)
    and 3-segment signed JWT (HS256/HS512).
    """
    parts = token_str.split(".")
    if len(parts) == 5:
        header_b64, _, iv_b64, ciphertext_b64, tag_b64 = parts
        iv = _b64url_decode(iv_b64)
        ciphertext = _b64url_decode(ciphertext_b64)
        tag = _b64url_decode(tag_b64)
        aad = header_b64.encode("ascii")

        hkdf = HKDF(
            algorithm=hashes.SHA256(),
            length=32,
            salt=b"",
            info=b"NextAuth.js Generated Encryption Key",
        )
        derived_key = hkdf.derive(secret.encode("utf-8"))
        aesgcm = AESGCM(derived_key)
        decrypted = aesgcm.decrypt(iv, ciphertext + tag, aad)
        data: dict[str, Any] = json.loads(decrypted.decode("utf-8"))

        exp = data.get("exp")
        if exp and exp < time.time():
            raise ValueError("NextAuth session token has expired")
        return data

    if len(parts) == 3:
        import jwt

        decoded: dict[str, Any] = jwt.decode(token_str, secret, algorithms=["HS256", "HS512"])
        return decoded

    raise ValueError(f"Unsupported NextAuth token segments: {len(parts)}")


def extract_nextauth_context(request: HTTPConnection) -> RequestContext | None:
    """
    Inspect incoming request cookies for NextAuth session token (__Secure-next-auth.session-token
    or next-auth.session-token) and return an authenticated RequestContext if valid.
    """
    token_str = request.cookies.get("__Secure-next-auth.session-token") or request.cookies.get(
        "next-auth.session-token"
    )
    if not token_str:
        return None

    secret = os.getenv("NEXTAUTH_SECRET")
    if not secret:
        if os.getenv("ENVIRONMENT", "").strip().lower() == "production":
            return None
        secret = DEFAULT_NEXTAUTH_SECRET

    try:
        claims = decode_nextauth_token(token_str, secret)
        user_id = (
            claims.get("userId") or claims.get("sub") or claims.get("user_id") or claims.get("uid")
        )
        if not user_id:
            return None

        org_id = claims.get("orgId") or claims.get("org_id") or "default_org"
        email = claims.get("email") or f"{user_id}@{org_id}.internal"
        name = claims.get("name")
        roles = claims.get("roles") or ["developer"]
        if not isinstance(roles, list):
            roles = [str(roles)]

        return RequestContext(
            user_id=str(user_id),
            org_id=str(org_id),
            email=str(email),
            name=name,
            roles=roles,
            groups=[],
        )
    except Exception as err:
        logger.debug("Failed to decode NextAuth session cookie: %s", err)
        return None

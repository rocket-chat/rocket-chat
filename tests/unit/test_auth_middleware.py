"""Unit tests for OIDC/SSO JWT verification and AuthMiddleware."""

import time
from typing import Any

import jwt
import pytest
from api.auth import JWTValidationError, JWTValidator, RequestContext
from api.middleware.auth import AuthMiddleware
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient


@pytest.fixture
def rsa_key_pair():
    """Generate ephemeral RSA private and public keys for RS256 testing."""
    private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
    )
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("utf-8")

    public_pem = (
        private_key.public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode("utf-8")
    )

    return private_pem, public_pem


def _create_test_jwt(
    private_pem: str,
    sub: str = "usr_pilot_01",
    org_id: str = "org_satellite_corp",
    team_id: str | None = "team_avionics",
    email: str = "pilot@satellite.corp",
    groups: list[str] | None = None,
    expires_in: int = 3600,
    **kwargs: Any,
) -> str:
    now = int(time.time())
    payload = {
        "sub": sub,
        "org_id": org_id,
        "team_id": team_id,
        "email": email,
        "groups": groups or ["team_avionics"],
        "iat": now,
        "exp": now + expires_in,
        **kwargs,
    }
    return jwt.encode(payload, private_pem, algorithm="RS256")


def test_jwt_validator_valid_token(rsa_key_pair):
    private_pem, public_pem = rsa_key_pair
    validator = JWTValidator(public_key=public_pem)

    token = _create_test_jwt(private_pem)
    ctx = validator.validate_token(token)

    assert isinstance(ctx, RequestContext)
    assert ctx.user_id == "usr_pilot_01"
    assert ctx.org_id == "org_satellite_corp"
    assert ctx.team_id == "team_avionics"
    assert ctx.email == "pilot@satellite.corp"
    assert "team_avionics" in ctx.groups


def test_jwt_validator_expired_token(rsa_key_pair):
    private_pem, public_pem = rsa_key_pair
    validator = JWTValidator(public_key=public_pem)

    expired_token = _create_test_jwt(private_pem, expires_in=-100)
    with pytest.raises(JWTValidationError) as exc:
        validator.validate_token(expired_token)
    assert "expired" in str(exc.value).lower()


def test_jwt_validator_missing_org_claim(rsa_key_pair):
    private_pem, public_pem = rsa_key_pair
    validator = JWTValidator(public_key=public_pem)

    token = _create_test_jwt(private_pem, org_id="")
    with pytest.raises(JWTValidationError) as exc:
        validator.validate_token(token)
    assert "org_id" in str(exc.value)


def test_auth_middleware_flow(rsa_key_pair, monkeypatch):
    monkeypatch.delenv("DEV_AUTH_BYPASS", raising=False)
    monkeypatch.delenv("ROCKET_TRUST_PROXY_HEADERS", raising=False)
    private_pem, public_pem = rsa_key_pair
    validator = JWTValidator(public_key=public_pem)

    app = FastAPI()
    app.add_middleware(AuthMiddleware, validator=validator)

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    @app.get("/api/mission")
    async def mission(req: Request):
        ctx: RequestContext = req.state.context
        return {
            "user": ctx.user_id,
            "org": ctx.org_id,
            "team": ctx.team_id,
        }

    client = TestClient(app)

    # 1. Exempt path succeeds with no auth
    res_health = client.get("/health")
    assert res_health.status_code == 200

    # 2. Protected path fails without token (401)
    res_no_auth = client.get("/api/mission")
    assert res_no_auth.status_code == 401
    assert res_no_auth.json()["detail"] == "Authentication required"

    # 3. Protected path succeeds with valid Bearer token
    token = _create_test_jwt(
        private_pem,
        sub="astronaut_42",
        org_id="space_agency",
        team_id="rover_ops",
    )
    res_auth = client.get("/api/mission", headers={"Authorization": f"Bearer {token}"})
    assert res_auth.status_code == 200
    assert res_auth.json() == {
        "user": "astronaut_42",
        "org": "space_agency",
        "team": "rover_ops",
    }


def test_auth_middleware_with_nextauth_cookie():
    import base64
    import json

    from api.auth.nextauth_cookie import DEFAULT_NEXTAUTH_SECRET
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF

    app = FastAPI()
    app.add_middleware(AuthMiddleware, validator=JWTValidator())

    @app.get("/api/mission")
    async def mission(req: Request):
        ctx: RequestContext = req.state.context
        return {
            "user": ctx.user_id,
            "org": ctx.org_id,
        }

    # Synthesize valid NextAuth JWE cookie
    header_json = json.dumps({"alg": "dir", "enc": "A256GCM"})
    header_b64 = base64.urlsafe_b64encode(header_json.encode("utf-8")).rstrip(b"=").decode("ascii")
    payload = json.dumps(
        {"userId": "cookie_user_99", "orgId": "cookie_org", "exp": int(time.time()) + 3600}
    )

    hkdf = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=b"",
        info=b"NextAuth.js Generated Encryption Key",
    )
    derived_key = hkdf.derive(DEFAULT_NEXTAUTH_SECRET.encode("utf-8"))
    aesgcm = AESGCM(derived_key)
    iv = b"\x00" * 12
    ciphertext_and_tag = aesgcm.encrypt(iv, payload.encode("utf-8"), header_b64.encode("ascii"))
    ciphertext = ciphertext_and_tag[:-16]
    tag = ciphertext_and_tag[-16:]

    def b64url(b: bytes) -> str:
        return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")

    token_jwe = f"{header_b64}..{b64url(iv)}.{b64url(ciphertext)}.{b64url(tag)}"

    client = TestClient(app)
    client.cookies.set("__Secure-next-auth.session-token", token_jwe)
    res = client.get("/api/mission")
    assert res.status_code == 200
    assert res.json() == {"user": "cookie_user_99", "org": "cookie_org"}

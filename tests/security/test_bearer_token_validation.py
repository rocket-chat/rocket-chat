"""API bearer tokens are verified with keys found through OIDC discovery."""

from __future__ import annotations

import json
import time
from typing import Any

import httpx
import jwt
import pytest
from api.auth import jwt_validator
from api.auth.jwt_validator import JWTValidationError, JWTValidator
from cryptography.hazmat.primitives.asymmetric import rsa

ISSUER = "https://login.corp.test/realms/acme"


@pytest.fixture
def key_pair(monkeypatch: pytest.MonkeyPatch) -> Any:
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(private.public_key()))
    jwk.update({"kid": "k1", "use": "sig", "alg": "RS256"})
    discovery_calls: list[str] = []

    def fake_get(url: str, **_: object) -> httpx.Response:
        discovery_calls.append(url)
        return httpx.Response(
            200,
            json={"issuer": ISSUER, "jwks_uri": f"{ISSUER}/keys"},
            request=httpx.Request("GET", url),
        )

    monkeypatch.setattr(jwt_validator.httpx, "get", fake_get)
    monkeypatch.setattr(jwt_validator.PyJWKClient, "fetch_data", lambda self: {"keys": [jwk]})
    jwt_validator._discovered_jwks_uris.clear()
    monkeypatch.delenv("OIDC_JWKS_URL", raising=False)
    monkeypatch.delenv("OIDC_PUBLIC_KEY", raising=False)
    return private, discovery_calls


def _token(private: Any, **overrides: Any) -> str:
    claims = {
        "iss": ISSUER,
        "sub": "u1",
        "org_id": "org_a",
        "exp": int(time.time()) + 300,
        **overrides,
    }
    return jwt.encode(claims, private, algorithm="RS256", headers={"kid": "k1"})


def test_keys_come_from_discovery_when_only_the_issuer_is_configured(key_pair: Any) -> None:
    private, calls = key_pair
    validator = JWTValidator(issuer=ISSUER)

    ctx = validator.validate_token(_token(private))

    assert ctx.user_id == "u1" and ctx.org_id == "org_a"
    assert calls == [f"{ISSUER}/.well-known/openid-configuration"]


def test_tokens_from_another_issuer_or_key_are_rejected(key_pair: Any) -> None:
    private, _ = key_pair
    validator = JWTValidator(issuer=ISSUER)

    with pytest.raises(JWTValidationError):
        validator.validate_token(_token(private, iss="https://evil.test"))
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    with pytest.raises(JWTValidationError):
        validator.validate_token(_token(other))


def test_hmac_tokens_are_refused_whatever_the_secret(key_pair: Any) -> None:
    validator = JWTValidator(issuer=ISSUER)
    forged = jwt.encode(
        {"iss": ISSUER, "sub": "x", "org_id": "o", "exp": int(time.time()) + 60},
        "k" * 40,
        algorithm="HS256",
        headers={"kid": "k1"},
    )

    with pytest.raises(JWTValidationError):
        validator.validate_token(forged)


def test_a_discovery_document_for_a_different_issuer_is_rejected(
    key_pair: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    private, _ = key_pair
    monkeypatch.setattr(
        jwt_validator.httpx,
        "get",
        lambda url, **_: httpx.Response(
            200,
            json={"issuer": "https://impostor.test", "jwks_uri": "https://impostor.test/keys"},
            request=httpx.Request("GET", url),
        ),
    )

    with pytest.raises(JWTValidationError, match="does not match"):
        JWTValidator(issuer=ISSUER).validate_token(_token(private))

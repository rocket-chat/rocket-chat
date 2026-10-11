"""An in-process OpenID Connect provider with Azure-style (non-Keycloak) endpoints.

It issues authorization codes, enforces PKCE and client authentication at the token
endpoint, and signs ID tokens with a real RSA key published at ``jwks_uri`` - so the code under
test performs genuine discovery, token exchange and JWKS verification, with no network.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
from fastapi import FastAPI, Form, Header, HTTPException, Request
from joserfc import jwt
from joserfc.jwk import KeySet, RSAKey

ISSUER = "https://login.idp.test/tenant-42/v2.0"
CLIENT_ID = "rocket-client"
CLIENT_SECRET = "rocket-secret"


@dataclass
class _Grant:
    nonce: str
    challenge: str
    redirect_uri: str
    subject: str
    claims: dict[str, Any]


@dataclass
class FakeProvider:
    key: RSAKey = field(
        default_factory=lambda: RSAKey.generate_key(2048, {"kid": "k1", "use": "sig"})
    )
    grants: dict[str, _Grant] = field(default_factory=dict)
    issuer: str = ISSUER
    audience: str = CLIENT_ID
    discovery_issuer: str | None = None
    id_token_overrides: dict[str, Any] = field(default_factory=dict)
    nonce_override: str | None = None
    signing_key: RSAKey | None = None
    userinfo: dict[str, Any] = field(default_factory=dict)
    token_calls: int = 0
    raw_id_token: str | None = None

    def app(self) -> FastAPI:
        app = FastAPI()
        base = "/tenant-42/v2.0"

        @app.get(f"{base}/.well-known/openid-configuration")
        async def discovery() -> dict[str, Any]:
            return {
                "issuer": self.discovery_issuer or self.issuer,
                "authorization_endpoint": "https://login.idp.test/tenant-42/oauth2/v2.0/authorize",
                "token_endpoint": "https://login.idp.test/tenant-42/oauth2/v2.0/token",
                "jwks_uri": "https://login.idp.test/tenant-42/discovery/v2.0/keys",
                "userinfo_endpoint": "https://login.idp.test/oidc/userinfo",
                "token_endpoint_auth_methods_supported": [
                    "client_secret_post",
                    "client_secret_basic",
                ],
            }

        @app.get("/tenant-42/discovery/v2.0/keys")
        async def keys() -> dict[str, Any]:
            return KeySet([self.key]).as_dict(private=False)

        @app.get("/oidc/userinfo")
        async def userinfo(authorization: str = Header(default="")) -> dict[str, Any]:
            if not authorization.startswith("Bearer "):
                raise HTTPException(401)
            return self.userinfo

        @app.post("/tenant-42/oauth2/v2.0/token")
        async def token(
            request: Request,
            grant_type: str = Form(...),
            code: str = Form(...),
            redirect_uri: str = Form(...),
            code_verifier: str = Form(default=""),
            client_id: str = Form(default=""),
            client_secret: str = Form(default=""),
        ) -> dict[str, Any]:
            self.token_calls += 1
            basic = request.headers.get("authorization", "")
            if basic.startswith("Basic "):
                client_id, _, client_secret = base64.b64decode(basic[6:]).decode().partition(":")
            if (client_id, client_secret) != (CLIENT_ID, CLIENT_SECRET):
                raise HTTPException(401, "invalid_client")
            grant = self.grants.pop(code, None)
            if grant is None or grant_type != "authorization_code":
                raise HTTPException(400, "invalid_grant")
            expected = (
                base64.urlsafe_b64encode(hashlib.sha256(code_verifier.encode()).digest())
                .decode()
                .rstrip("=")
            )
            if expected != grant.challenge or redirect_uri != grant.redirect_uri:
                raise HTTPException(400, "invalid_grant")
            return {
                "access_token": "at-" + secrets.token_hex(4),
                "token_type": "Bearer",
                "expires_in": 3600,
                "id_token": self.raw_id_token or self._id_token(grant),
            }

        return app

    def transport(self) -> httpx.ASGITransport:
        return httpx.ASGITransport(app=self.app())

    def oauth_transport(self) -> Any:
        """The same app behind the HTTP library Authlib's client uses."""
        from authlib.integrations.httpx_client._compat import httpx2

        return httpx2.ASGITransport(app=self.app())

    def _id_token(self, grant: _Grant) -> str:
        now = int(time.time())
        claims: dict[str, Any] = {
            "iss": self.issuer,
            "aud": self.audience,
            "sub": grant.subject,
            "iat": now,
            "exp": now + 300,
            "nonce": self.nonce_override or grant.nonce,
            **grant.claims,
            **self.id_token_overrides,
        }
        key = self.signing_key or self.key
        return jwt.encode({"alg": "RS256", "kid": key.kid}, claims, key)

    def approve(
        self, authorization_url: str, subject: str = "idp-user-1", **claims: Any
    ) -> tuple[str, dict[str, list[str]]]:
        """Simulate the user consenting at the provider: returns ``(code, query)``."""
        query = parse_qs(urlparse(authorization_url).query)
        code = secrets.token_urlsafe(16)
        self.grants[code] = _Grant(
            nonce=query["nonce"][0],
            challenge=query["code_challenge"][0],
            redirect_uri=query["redirect_uri"][0],
            subject=subject,
            claims={"email": "alice@acme.test", "email_verified": True, "name": "Alice", **claims},
        )
        return code, query


def forge_hs256_token(secret: str, claims: dict[str, Any]) -> str:
    """A token an attacker could sign with the (shared) client secret."""
    import jwt as pyjwt

    return pyjwt.encode(claims, secret, algorithm="HS256")

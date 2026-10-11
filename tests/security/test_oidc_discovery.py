"""OIDC login works against any compliant provider and rejects bad identity tokens.

The fixture provider uses Azure-style endpoints (nothing like Keycloak's
``/protocol/openid-connect/*``) and a real RSA key, so these tests exercise discovery, PKCE,
nonce handling and JWKS verification for real.
"""

from __future__ import annotations

import time
from collections.abc import Iterator

import httpx
import pytest
from api.auth import oidc
from api.auth.oidc import OidcConfig, OidcError, begin_login, complete_login
from fake_oidc_provider import CLIENT_ID, CLIENT_SECRET, ISSUER, FakeProvider, forge_hs256_token
from joserfc.jwk import RSAKey

REDIRECT = "https://rocket.example/api/auth/oidc/callback"
CONFIG = OidcConfig(issuer=ISSUER, client_id=CLIENT_ID, client_secret=CLIENT_SECRET)


@pytest.fixture
def idp() -> Iterator[FakeProvider]:
    provider = FakeProvider()
    oidc.reset_caches()
    app = provider.app()
    oidc._transport_override = httpx.ASGITransport(app=app)
    oidc._oauth_transport_override = provider.oauth_transport()
    yield provider
    oidc._transport_override = None
    oidc._oauth_transport_override = None
    oidc.reset_caches()


async def _login(idp: FakeProvider, **claims: object):
    start = await begin_login(CONFIG, redirect_uri=REDIRECT, state="st")
    code, _ = idp.approve(start.url, **claims)
    return start, code


@pytest.mark.asyncio
async def test_login_uses_the_endpoints_the_provider_advertises(idp: FakeProvider) -> None:
    start = await begin_login(CONFIG, redirect_uri=REDIRECT, state="st-1")

    assert start.url.startswith("https://login.idp.test/tenant-42/oauth2/v2.0/authorize?")
    assert "protocol/openid-connect" not in start.url
    query = dict(pair.split("=", 1) for pair in start.url.split("?", 1)[1].split("&"))
    assert query["code_challenge_method"] == "S256" and query["state"] == "st-1"
    assert query["nonce"] == start.nonce and query["response_type"] == "code"
    assert start.code_verifier not in start.url, "the PKCE verifier must stay off the front channel"


@pytest.mark.asyncio
async def test_a_full_login_returns_the_verified_identity(idp: FakeProvider) -> None:
    start, code = await _login(idp, name="Alice Liddell")

    who = await complete_login(
        CONFIG,
        code=code,
        redirect_uri=REDIRECT,
        code_verifier=start.code_verifier,
        nonce=start.nonce,
    )

    assert (who.subject, who.email, who.email_verified) == ("idp-user-1", "alice@acme.test", True)
    assert who.name == "Alice Liddell"


@pytest.mark.asyncio
async def test_pkce_is_enforced_by_the_exchange(idp: FakeProvider) -> None:
    start, code = await _login(idp)

    with pytest.raises(OidcError, match="token exchange failed"):
        await complete_login(
            CONFIG,
            code=code,
            redirect_uri=REDIRECT,
            code_verifier="not-the-verifier",
            nonce=start.nonce,
        )


@pytest.mark.asyncio
async def test_a_replayed_or_foreign_code_is_rejected(idp: FakeProvider) -> None:
    start, code = await _login(idp)
    await complete_login(
        CONFIG,
        code=code,
        redirect_uri=REDIRECT,
        code_verifier=start.code_verifier,
        nonce=start.nonce,
    )

    with pytest.raises(OidcError):
        await complete_login(
            CONFIG,
            code=code,
            redirect_uri=REDIRECT,
            code_verifier=start.code_verifier,
            nonce=start.nonce,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("override", "reason"),
    [
        ({"iss": "https://evil.example"}, "could not be verified"),
        ({"aud": "someone-elses-client"}, "different application"),
        ({"exp": int(time.time()) - 3600}, "could not be verified"),
        ({"aud": [CLIENT_ID, "other"]}, "different application"),  # several audiences, no azp
    ],
)
async def test_bad_id_token_claims_are_rejected(
    idp: FakeProvider, override: dict[str, object], reason: str
) -> None:
    idp.id_token_overrides = override
    start, code = await _login(idp)

    with pytest.raises(OidcError, match=reason):
        await complete_login(
            CONFIG,
            code=code,
            redirect_uri=REDIRECT,
            code_verifier=start.code_verifier,
            nonce=start.nonce,
        )


@pytest.mark.asyncio
async def test_a_token_for_a_different_login_attempt_is_rejected(idp: FakeProvider) -> None:
    idp.nonce_override = "replayed-nonce"
    start, code = await _login(idp)

    with pytest.raises(OidcError, match="could not be verified"):
        await complete_login(
            CONFIG,
            code=code,
            redirect_uri=REDIRECT,
            code_verifier=start.code_verifier,
            nonce=start.nonce,
        )


@pytest.mark.asyncio
async def test_a_token_signed_by_an_unknown_key_is_rejected(idp: FakeProvider) -> None:
    idp.signing_key = RSAKey.generate_key(2048, {"kid": "attacker"})
    start, code = await _login(idp)

    with pytest.raises(OidcError, match="could not be verified"):
        await complete_login(
            CONFIG,
            code=code,
            redirect_uri=REDIRECT,
            code_verifier=start.code_verifier,
            nonce=start.nonce,
        )


@pytest.mark.asyncio
async def test_a_key_rotation_is_picked_up_without_a_restart(idp: FakeProvider) -> None:
    first, code = await _login(idp)
    await complete_login(
        CONFIG,
        code=code,
        redirect_uri=REDIRECT,
        code_verifier=first.code_verifier,
        nonce=first.nonce,
    )

    idp.key = RSAKey.generate_key(2048, {"kid": "k2", "use": "sig"})  # the provider rotates
    second, code2 = await _login(idp)
    who = await complete_login(
        CONFIG,
        code=code2,
        redirect_uri=REDIRECT,
        code_verifier=second.code_verifier,
        nonce=second.nonce,
    )

    assert who.subject == "idp-user-1"


@pytest.mark.asyncio
async def test_an_hmac_token_signed_with_the_client_secret_is_rejected(idp: FakeProvider) -> None:
    start, code = await _login(idp)
    now = int(time.time())
    idp.raw_id_token = forge_hs256_token(
        CLIENT_SECRET * 4,
        {
            "iss": ISSUER,
            "aud": CLIENT_ID,
            "sub": "attacker",
            "exp": now + 300,
            "nonce": start.nonce,
            "email": "admin@acme.test",
        },
    )

    with pytest.raises(OidcError, match="could not be verified"):
        await complete_login(
            CONFIG,
            code=code,
            redirect_uri=REDIRECT,
            code_verifier=start.code_verifier,
            nonce=start.nonce,
        )


@pytest.mark.asyncio
async def test_a_discovery_document_for_another_issuer_is_refused(idp: FakeProvider) -> None:
    idp.discovery_issuer = "https://other-issuer.example"

    with pytest.raises(OidcError, match="does not match the configured issuer"):
        await begin_login(CONFIG, redirect_uri=REDIRECT, state="s")


@pytest.mark.asyncio
async def test_profile_claims_missing_from_the_token_come_from_userinfo(idp: FakeProvider) -> None:
    idp.userinfo = {"sub": "idp-user-1", "email": "bob@acme.test", "name": "Bob"}
    start = await begin_login(CONFIG, redirect_uri=REDIRECT, state="s")
    code, _ = idp.approve(start.url, email=None, name=None)
    idp.grants[code].claims.pop("email")
    idp.grants[code].claims.pop("name")

    who = await complete_login(
        CONFIG,
        code=code,
        redirect_uri=REDIRECT,
        code_verifier=start.code_verifier,
        nonce=start.nonce,
    )

    assert (who.email, who.name) == ("bob@acme.test", "Bob")


@pytest.mark.asyncio
async def test_userinfo_for_a_different_subject_is_ignored(idp: FakeProvider) -> None:
    idp.userinfo = {"sub": "someone-else", "email": "victim@acme.test"}
    start = await begin_login(CONFIG, redirect_uri=REDIRECT, state="s")
    code, _ = idp.approve(start.url)
    idp.grants[code].claims.pop("email")

    who = await complete_login(
        CONFIG,
        code=code,
        redirect_uri=REDIRECT,
        code_verifier=start.code_verifier,
        nonce=start.nonce,
    )

    assert who.email == ""

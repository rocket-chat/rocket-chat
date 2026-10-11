"""SSO org mapping / domain policy and live role resolution."""

from __future__ import annotations

import pytest
from api.auth.context import RequestContext
from api.auth.roles import clear_role_cache, invalidate_role, with_live_roles
from api.auth.sso_policy import domain_permitted, map_org


def test_single_org_mode_is_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ROCKET_SSO_ORG_MODE", raising=False)
    monkeypatch.delenv("ROCKET_DEFAULT_ORG_ID", raising=False)
    assert map_org("a@acme.io") == "default_org"
    assert map_org("a@gmail.com") == "default_org"


def test_domain_mode_never_groups_public_mail_domains(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ROCKET_SSO_ORG_MODE", "domain")
    assert map_org("a@acme.io") == "acme_io"
    assert map_org("a@gmail.com") == "default_org"


def test_idp_asserted_org_wins() -> None:
    assert map_org("a@acme.io", "org_from_idp") == "org_from_idp"


def test_deployment_domain_allowlist(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SSO_ALLOWED_DOMAINS", "acme.io, corp.com")
    assert domain_permitted("a@acme.io")
    assert not domain_permitted("a@evil.com")


def test_org_policy_requires_enforce_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SSO_ALLOWED_DOMAINS", raising=False)
    monkeypatch.delenv("GOOGLE_ALLOWED_DOMAIN", raising=False)
    policy = {"sso_allowed_domains": "acme.io", "sso_enforce_domain_match": False}
    assert domain_permitted("a@evil.com", policy)
    assert not domain_permitted("a@evil.com", {**policy, "sso_enforce_domain_match": True})
    assert domain_permitted("a@acme.io", {**policy, "sso_enforce_domain_match": True})


class _User:
    def __init__(self, role: str) -> None:
        self.role = role


class _Session:
    def __init__(self, role: str | None) -> None:
        self._role = role

    async def __aenter__(self) -> _Session:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def get(self, _model: object, _user_id: str) -> _User | None:
        return None if self._role is None else _User(self._role)


class _Db:
    session_factory = object()

    def __init__(self, role: str | None) -> None:
        self.role = role
        self.lookups = 0

    def session(self) -> _Session:
        self.lookups += 1
        return _Session(self.role)


def _ctx(source: str = "session", roles: list[str] | None = None) -> RequestContext:
    return RequestContext(
        user_id="u1", org_id="o", email="u@o", roles=roles or ["admin", "developer"], source=source
    )


@pytest.mark.asyncio
async def test_demoted_admin_loses_rights_before_token_expiry() -> None:
    clear_role_cache()
    db = _Db("developer")
    live = await with_live_roles(_ctx(), db)
    assert live.roles == ["developer"]


@pytest.mark.asyncio
async def test_roles_are_cached_and_invalidated_on_change() -> None:
    clear_role_cache()
    db = _Db("developer")
    await with_live_roles(_ctx(), db)
    await with_live_roles(_ctx(), db)
    assert db.lookups == 1
    db.role = "admin"
    invalidate_role("u1")
    assert "admin" in (await with_live_roles(_ctx(), db)).roles


@pytest.mark.asyncio
async def test_only_session_identities_are_overridden() -> None:
    clear_role_cache()
    db = _Db("readonly")
    oidc = _ctx(source="oidc")
    assert (await with_live_roles(oidc, db)).roles == oidc.roles
    assert db.lookups == 0


@pytest.mark.asyncio
async def test_unknown_user_keeps_token_roles() -> None:
    clear_role_cache()
    assert (await with_live_roles(_ctx(), _Db(None))).roles == ["admin", "developer"]

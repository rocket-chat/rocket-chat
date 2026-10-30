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
    def __init__(self, role: str, org_id: str = "o") -> None:
        self.role = role
        self.org_id = org_id


class _Session:
    def __init__(self, role: str | None, org_id: str = "o") -> None:
        self._role = role
        self._org_id = org_id

    async def __aenter__(self) -> _Session:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def get(self, _model: object, _user_id: str) -> _User | None:
        return None if self._role is None else _User(self._role, self._org_id)

    async def execute(self, _statement: object) -> _NoMembership:
        # No ``memberships`` row: the role comes from the person's home organization.
        return _NoMembership()


class _NoMembership:
    def scalar_one_or_none(self) -> None:
        return None


class _Db:
    session_factory = object()

    def __init__(self, role: str | None, org_id: str = "o") -> None:
        self.role = role
        self.org_id = org_id
        self.lookups = 0

    def session(self, **_: object) -> _Session:
        self.lookups += 1
        return _Session(self.role, self.org_id)


def _ctx(source: str = "session", roles: list[str] | None = None) -> RequestContext:
    return RequestContext(
        user_id="u1", org_id="o", email="u@o", roles=roles or ["admin", "developer"], source=source
    )


@pytest.mark.asyncio
async def test_demoted_admin_loses_rights_before_token_expiry() -> None:
    clear_role_cache()
    db = _Db("developer")
    live = await with_live_roles(_ctx(), db)
    assert live.roles == ["member"]


@pytest.mark.asyncio
async def test_a_role_stored_in_another_organization_grants_nothing_here() -> None:
    clear_role_cache()
    live = await with_live_roles(_ctx(), _Db("admin", org_id="other_org"))
    assert live.roles == [], "no membership here: no role at all"


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


@pytest.mark.asyncio
async def test_a_human_guest_is_held_to_read_only() -> None:
    clear_role_cache()
    assert (await with_live_roles(_ctx(), _Db("guest"))).roles == ["viewer"]

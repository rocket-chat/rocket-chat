"""Importing a GPG signing key: validation, what the page may see, roles and isolation."""

from __future__ import annotations

import pathlib

import pytest
from api.main import app
from api.services import credentials
from api.services.git_signing import gpg_available
from api.services.settings import save_org_config
from httpx import ASGITransport, AsyncClient

from tests.support.gpg_keys import make_key

pytestmark = pytest.mark.skipif(not gpg_available(), reason="needs GnuPG")

ORG = "signing_api_org"
ALICE = {"X-Tenant-Org-Id": ORG, "X-Tenant-User-Id": "alice", "X-User-Role": "member"}
BOB = {"X-Tenant-Org-Id": ORG, "X-Tenant-User-Id": "bob", "X-User-Role": "member"}
# Long and distinctive: a short one turns up by chance in the armored public key.
SECRET_PASSPHRASE = "correct-horse-battery-staple"
VIEWER = {"X-Tenant-Org-Id": ORG, "X-Tenant-User-Id": "viewer", "X-User-Role": "viewer"}


@pytest.fixture(autouse=True)
def in_memory(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(app.state, "db_manager", None, raising=False)
    credentials._in_memory_credentials.clear()


@pytest.fixture
async def api() -> AsyncClient:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client


async def test_an_imported_key_is_shown_without_its_private_half(
    api: AsyncClient, tmp_path: pathlib.Path
) -> None:
    private, _ = make_key(tmp_path, "Alice <alice@example.com>", passphrase=SECRET_PASSPHRASE)

    saved = await api.put(
        "/v1/user/git-signing",
        json={"private_key": private, "passphrase": SECRET_PASSPHRASE},
        headers=ALICE,
    )

    assert saved.status_code == 200, saved.text
    body = saved.json()
    assert body["configured"] is True and body["commit_email"] == "alice@example.com"
    assert body["has_passphrase"] is True and len(body["fingerprint"]) == 40
    assert "BEGIN PGP PUBLIC KEY BLOCK" in body["public_key"]
    assert "PRIVATE" not in saved.text and SECRET_PASSPHRASE not in saved.text
    assert body["available"] is True and body["mode"] == "github"

    await save_org_config(None, ORG, "github", {"signing": "member_gpg"}, [])
    assert (await api.get("/v1/user/git-signing", headers=ALICE)).json()["mode"] == "member_gpg"


async def test_a_bad_key_or_passphrase_is_explained(
    api: AsyncClient, tmp_path: pathlib.Path
) -> None:
    private, _ = make_key(tmp_path, "Alice <alice@example.com>", passphrase="right")

    wrong = await api.put(
        "/v1/user/git-signing", json={"private_key": private, "passphrase": "nope"}, headers=ALICE
    )
    other_email = await api.put(
        "/v1/user/git-signing",
        json={"private_key": private, "passphrase": "right", "commit_email": "x@y.test"},
        headers=ALICE,
    )

    assert wrong.status_code == 422 and "passphrase" in wrong.json()["detail"]
    assert other_email.status_code == 422 and "not an identity" in other_email.json()["detail"]
    assert (await api.get("/v1/user/git-signing", headers=ALICE)).json()["configured"] is False


async def test_keys_are_private_to_their_owner_and_can_be_removed(
    api: AsyncClient, tmp_path: pathlib.Path
) -> None:
    private, _ = make_key(tmp_path, "Alice <alice@example.com>")
    await api.put("/v1/user/git-signing", json={"private_key": private}, headers=ALICE)

    assert (await api.get("/v1/user/git-signing", headers=BOB)).json()["configured"] is False

    gone = await api.delete("/v1/user/git-signing", headers=ALICE)
    assert gone.json()["configured"] is False
    assert (await api.get("/v1/user/git-signing", headers=ALICE)).json()["configured"] is False


async def test_a_read_only_member_cannot_import_a_key(
    api: AsyncClient, tmp_path: pathlib.Path
) -> None:
    private, _ = make_key(tmp_path, "Viewer <v@example.com>")

    denied = await api.put("/v1/user/git-signing", json={"private_key": private}, headers=VIEWER)

    assert denied.status_code == 403

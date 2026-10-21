"""The running version: a tagged release reports its number, anything else reports `dev`."""

from __future__ import annotations

import pytest
from api.main import app
from httpx import ASGITransport, AsyncClient

MEMBER = {"X-Tenant-Org-Id": "version_org", "X-Tenant-User-Id": "alice", "X-User-Role": "developer"}


@pytest.fixture
async def api() -> AsyncClient:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client


@pytest.mark.parametrize(
    ("configured", "shown"),
    [("0.5.0", "0.5.0"), ("v1.2.3-rc.1", "1.2.3-rc.1"), ("", "dev"), ("  ", "dev")],
)
async def test_the_version_is_the_release_baked_into_the_image(
    api: AsyncClient, monkeypatch: pytest.MonkeyPatch, configured: str, shown: str
) -> None:
    monkeypatch.setenv("ROCKET_VERSION", configured)

    response = await api.get("/v1/version", headers=MEMBER)

    assert response.status_code == 200
    assert response.json() == {"version": shown}


async def test_a_build_without_a_release_number_is_dev(
    api: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ROCKET_VERSION", raising=False)

    assert (await api.get("/v1/version", headers=MEMBER)).json() == {"version": "dev"}

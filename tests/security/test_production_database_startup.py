"""In production the API never silently runs without its database."""

from __future__ import annotations

import pytest
from api.main import app
from fastapi.testclient import TestClient


@pytest.fixture
def production_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("SESSION_SECRET", "s" * 40)
    monkeypatch.setenv("ENCRYPTION_MASTER_KEY", "k" * 40)
    for flag in (
        "DEV_AUTH_BYPASS",
        "ROCKET_DEV_LOGIN",
        "ALLOW_DEV_LOGIN",
        "ROCKET_AUTO_CREATE_SCHEMA",
    ):
        monkeypatch.delenv(flag, raising=False)
    monkeypatch.delenv("TEST_DATABASE_URL", raising=False)


def test_an_unreachable_database_stops_production_startup(
    monkeypatch: pytest.MonkeyPatch, production_env: None
) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://nobody:nope@127.0.0.1:9/none")

    with pytest.raises(Exception) as failure, TestClient(app):
        pass

    assert "insecure production configuration" not in str(failure.value)


def test_development_still_falls_back_to_memory_when_the_database_is_down(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    monkeypatch.delenv("TEST_DATABASE_URL", raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://nobody:nope@127.0.0.1:9/none")

    from api.store import InMemorySessionStore

    with TestClient(app):
        assert isinstance(app.state.session_store, InMemorySessionStore)

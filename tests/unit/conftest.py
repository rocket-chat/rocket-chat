"""Fixtures shared by the unit tests."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from api.main import app
from api.services.github_watches import GithubWatchStore
from api.services.missions import MissionStore
from api.services.process_tracker import ProcessStore
from api.services.sandbox_catalogue import SandboxCatalogue
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def _fresh_process_wide_memory_stores(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stores that fall back to a class-level dict without a database must not leak between tests."""
    monkeypatch.setattr(ProcessStore, "_MEMORY", {})
    monkeypatch.setattr(SandboxCatalogue, "_MEMORY", {})
    monkeypatch.setattr(GithubWatchStore, "_MEMORY", {})


@pytest.fixture
def client() -> Iterator[TestClient]:
    """The API with in-memory stores: no database, every store fresh."""
    with TestClient(app) as test_client:
        app.state.db_manager = None
        app.state.mission_store = MissionStore(None)
        yield test_client

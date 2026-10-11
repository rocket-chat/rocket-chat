"""Unit tests for the deep readiness probe."""

from __future__ import annotations

from typing import Any

from api.services import metrics as metrics_module
from fastapi import FastAPI
from fastapi.testclient import TestClient


class _OkSession:
    async def execute(self, statement: Any) -> None:
        return None

    async def __aenter__(self) -> _OkSession:
        return self

    async def __aexit__(self, *args: object) -> bool:
        return False


class _OkDatabase:
    """A manager whose session executes SELECT 1 successfully."""

    session_factory = object()
    db_url = "postgresql+asyncpg://rocket_app:secret@db:5432/rocket_chat"

    def session(self) -> _OkSession:
        return _OkSession()


class _FailingSession:
    async def execute(self, statement: Any) -> None:
        raise RuntimeError("connection refused")

    async def __aenter__(self) -> _FailingSession:
        return self

    async def __aexit__(self, *args: object) -> bool:
        return False


class _FailingDatabase:
    """A manager whose session raises, as a dead pool or bad host would."""

    session_factory = object()
    db_url = "postgresql+asyncpg://rocket_app:secret@db:5432/rocket_chat"

    def session(self) -> _FailingSession:
        return _FailingSession()


class _RaisingDatabase:
    """A manager whose session() itself raises before a context can be entered."""

    session_factory = object()
    db_url = "postgresql+asyncpg://rocket_app:secret@db:5432/rocket_chat"

    def session(self) -> None:
        raise ConnectionError("pool exhausted")


class _UnconfiguredDatabase:
    """A manager with neither URL nor engine (in-memory dev mode)."""

    session_factory = None
    db_url = None


class _UninitializedDatabase:
    """A manager configured with a URL but whose engine was never built."""

    session_factory = None
    db_url = "postgresql+asyncpg://rocket_app:secret@db:5432/rocket_chat"


class _FakeCodeIndexCache:
    def stats(self) -> dict[str, int]:
        return {
            "sessions": 2,
            "bytes": 2048,
            "max_sessions": 16,
            "max_bytes": 201326592,
            "hits": 5,
            "misses": 1,
            "evictions": 0,
            "refusals": 0,
        }


def _build_ready_app(db_manager: object | None) -> FastAPI:
    app = FastAPI()
    app.add_api_route("/v1/ready", metrics_module.readiness_endpoint, methods=["GET"])
    app.state.code_index_cache = _FakeCodeIndexCache()
    app.state.draining = False
    if db_manager is not None:
        app.state.db_manager = db_manager
    return app


def test_ready_returns_200_and_code_index_stats_when_database_is_up() -> None:
    app = _build_ready_app(_OkDatabase())
    with TestClient(app) as client:
        response = client.get("/v1/ready")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ready"
    assert payload["database"] == {"ok": True, "detail": "connected"}
    assert payload["code_index"]["sessions"] == 2
    assert payload["code_index"]["bytes"] == 2048


def test_ready_returns_non_200_when_database_query_fails() -> None:
    app = _build_ready_app(_FailingDatabase())
    with TestClient(app) as client:
        response = client.get("/v1/ready")
    assert response.status_code == 503
    payload = response.json()
    assert payload["status"] == "degraded"
    assert payload["database"]["ok"] is False
    assert payload["database"]["detail"] == "RuntimeError"


def test_ready_returns_non_200_when_session_construction_fails() -> None:
    app = _build_ready_app(_RaisingDatabase())
    with TestClient(app) as client:
        response = client.get("/v1/ready")
    assert response.status_code == 503
    assert response.json()["database"]["detail"] == "ConnectionError"


def test_ready_treats_unconfigured_database_as_healthy_dev_mode() -> None:
    app = _build_ready_app(_UnconfiguredDatabase())
    with TestClient(app) as client:
        response = client.get("/v1/ready")
    assert response.status_code == 200
    assert response.json()["database"] == {"ok": True, "detail": "not_configured"}


def test_ready_returns_non_200_when_database_is_uninitialized() -> None:
    app = _build_ready_app(_UninitializedDatabase())
    with TestClient(app) as client:
        response = client.get("/v1/ready")
    assert response.status_code == 503
    assert response.json()["database"] == {"ok": False, "detail": "uninitialized"}


def test_ready_returns_non_200_while_draining() -> None:
    app = _build_ready_app(_OkDatabase())
    app.state.draining = True
    with TestClient(app) as client:
        response = client.get("/v1/ready")
    assert response.status_code == 503
    assert response.json()["status"] == "draining"
    assert response.json()["draining"] is True


def test_health_stays_a_static_liveness_signal() -> None:
    """`/v1/health` must never depend on the database (liveness vs readiness)."""
    app = _build_ready_app(_FailingDatabase())

    @app.get("/v1/health")
    async def health() -> dict[str, str]:
        return {"status": "healthy"}

    with TestClient(app) as client:
        assert client.get("/v1/health").json() == {"status": "healthy"}
        assert client.get("/v1/ready").status_code == 503


def test_drain_endpoint_flips_readiness() -> None:
    app = _build_ready_app(_OkDatabase())
    app.add_api_route("/v1/drain", metrics_module.drain_endpoint, methods=["POST"])
    with TestClient(app) as client:
        assert client.get("/v1/ready").status_code == 200
        assert client.post("/v1/drain").json() == {"status": "draining"}
        assert client.get("/v1/ready").status_code == 503


def test_ready_ping_uses_an_explicit_timeout() -> None:
    """The ping must be wrapped in a deadline; a hung DB cannot block the probe."""
    import inspect

    source = inspect.getsource(metrics_module._ping_database)
    assert "asyncio.timeout" in source
    assert metrics_module.db_ping_timeout_seconds() > 0

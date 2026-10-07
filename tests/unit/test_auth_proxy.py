"""Tests for the fallback FastAPI /api/auth proxy route."""

import pytest
from api.main import app
from httpx import ASGITransport, AsyncClient


@pytest.mark.asyncio
async def test_api_auth_proxy_handles_unreachable_frontend_gracefully() -> None:
    """When frontend is not running on 3000, fallback proxy returns 502 with detail."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        res = await client.get("/api/auth/configured-providers")
        # Should return either 200 (if frontend happens to be running) or 502 gracefully
        assert res.status_code in (200, 502)
        assert "detail" in res.json() or "google" in res.json()

"""HTTP integration test for the attachment endpoints.

Exercises the real ASGI surface with the in-memory store: a multipart upload returns the
metadata the cockpit needs, signed retrieval serves the stored bytes, a rejected type is
a visible 422, and another tenant cannot download the session's attachment.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
from api.main import app
from api.routers.attachments import router as attachments_router
from api.services.attachments import AttachmentService
from api.store import InMemorySessionStore
from httpx import ASGITransport, AsyncClient

PNG_BYTES = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
    "890000000a49444154789c6360000002000100ffff03000006000557bfabd400"
    "00000049454e44ae426082"
)

HEADERS = {
    "X-Tenant-Org-Id": "default_org",
    "X-Tenant-User-Id": "dev_user",
    "X-User-Role": "developer",
}
OTHER_TENANT = {
    "X-Tenant-Org-Id": "test_org",
    "X-Tenant-User-Id": "test_user",
    "X-User-Role": "developer",
}


@pytest.fixture
async def client(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> AsyncIterator[AsyncClient]:
    monkeypatch.setenv("ROCKET_ATTACHMENTS_DIR", str(tmp_path))
    app.state.session_store = InMemorySessionStore()
    app.state.attachment_service = AttachmentService(None)
    if not any(
        getattr(route, "path", "") == "/v1/sessions/{session_id}/attachments"
        for route in app.routes
    ):
        # api.main wires this router separately; until that lands the endpoint would 404.
        app.include_router(attachments_router)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


async def _create_session(client: AsyncClient, headers: dict[str, str]) -> str:
    res = await client.post(
        "/v1/sessions", json={"title": "Attachment HTTP Mission"}, headers=headers
    )
    assert res.status_code in (200, 201), res.text
    return str(res.json()["session_id"])


@pytest.mark.asyncio
async def test_upload_returns_metadata_and_preview(client: AsyncClient) -> None:
    session_id = await _create_session(client, HEADERS)

    res = await client.post(
        f"/v1/sessions/{session_id}/attachments",
        files={"file": ("screenshot.png", PNG_BYTES, "image/png")},
        headers=HEADERS,
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["name"] == "screenshot.png"
    assert body["mime"] == "image/png"
    assert body["size"] == len(PNG_BYTES)
    assert body["workspace_path"].startswith("/workspace/.attachments/")
    assert body["preview"].startswith("data:image/png;base64,")

    # Signed retrieval serves the exact bytes back.
    download = await client.get(
        f"/v1/sessions/{session_id}/attachments/{body['attachment_id']}",
        params={"token": body["retrieval_token"]},
        headers=HEADERS,
    )
    assert download.status_code == 200
    assert download.content == PNG_BYTES

    # A wrong token is rejected.
    bad = await client.get(
        f"/v1/sessions/{session_id}/attachments/{body['attachment_id']}",
        params={"token": "forged"},
        headers=HEADERS,
    )
    assert bad.status_code == 403


@pytest.mark.asyncio
async def test_rejected_upload_is_a_visible_422(client: AsyncClient) -> None:
    session_id = await _create_session(client, HEADERS)
    res = await client.post(
        f"/v1/sessions/{session_id}/attachments",
        files={"file": ("payload.exe", b"MZ", "application/x-msdownload")},
        headers=HEADERS,
    )
    assert res.status_code == 422
    assert "Unsupported attachment type" in res.text


@pytest.mark.asyncio
async def test_other_tenant_cannot_download(client: AsyncClient) -> None:
    session_id = await _create_session(client, HEADERS)
    upload = await client.post(
        f"/v1/sessions/{session_id}/attachments",
        files={"file": ("screenshot.png", PNG_BYTES, "image/png")},
        headers=HEADERS,
    )
    body = upload.json()

    res = await client.get(
        f"/v1/sessions/{session_id}/attachments/{body['attachment_id']}",
        params={"token": body["retrieval_token"]},
        headers=OTHER_TENANT,
    )
    assert res.status_code in (403, 404)


@pytest.mark.asyncio
async def test_attachment_on_missing_session_returns_404(client: AsyncClient) -> None:
    res = await client.post(
        "/v1/sessions/sess_does_not_exist/attachments",
        files={"file": ("a.log", b"x", "text/plain")},
        headers=HEADERS,
    )
    assert res.status_code == 404

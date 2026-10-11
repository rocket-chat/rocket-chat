"""Integration test: an uploaded image and log reach the model (spec section 8.2).

This ties the whole web pipeline together without going through HTTP: the attachment
service stores the bytes, the orchestrator attaches the refs to the *current* turn, and
the gateway formats the request the provider would receive. ``SG7``'s web half is that
both an image becomes an ``image_url`` block and a log's text is inlined.
"""

from __future__ import annotations

import io
import os
import uuid
from collections.abc import AsyncIterator, Callable

import pytest
from agent_core.orchestrator import AsyncReActOrchestrator
from agent_core.tools.registry import ToolRegistry
from api.db.models import OrganizationModel, SessionModel, UserModel
from api.db.session import DatabaseSessionManager
from api.services.attachments import AttachmentError, AttachmentService
from fastapi import UploadFile
from llm_gateway.gateway import LiteLLMGateway
from sandbox_driver.fake_driver import FakeSandboxDriver
from sqlalchemy import text

from specifications.interfaces.llm import LLMGatewayProtocol, ModelRequest, StreamChunk


@pytest.fixture(autouse=True)
def _isolated_attachment_store(monkeypatch: pytest.MonkeyPatch, tmp_path: object) -> None:
    """Keep blob writes inside the test's temporary directory, never the shared /tmp."""
    monkeypatch.setenv("ROCKET_ATTACHMENTS_DIR", str(tmp_path))


# A 1x1 transparent PNG; the exact pixels do not matter, only that the bytes are binary.
_PNG_BYTES = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
    "890000000a49444154789c6360000002000100ffff03000006000557bfabd400"
    "00000049454e44ae426082"
)


class RecordingGateway(LLMGatewayProtocol):
    def __init__(self) -> None:
        self.requests: list[ModelRequest] = []

    async def chat_stream(self, request: ModelRequest) -> AsyncIterator[StreamChunk]:
        self.requests.append(request)
        yield StreamChunk(text_delta="ack")
        yield StreamChunk(
            is_finished=True,
            finish_reason="stop",
            token_usage={"prompt_tokens": 1, "completion_tokens": 1},
        )

    async def check_budget(self, tenant_org_id: str, tenant_user_id: str) -> bool:
        return True

    async def record_usage(
        self,
        tenant_org_id: str,
        tenant_user_id: str,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        cache_read_tokens: int = 0,
        cache_creation_tokens: int = 0,
    ) -> None:
        return None


def _upload(name: str, mime: str, data: bytes) -> UploadFile:
    from starlette.datastructures import Headers

    return UploadFile(
        file=io.BytesIO(data),
        filename=name,
        headers=Headers({"content-type": mime}),
    )


@pytest.mark.asyncio
async def test_image_and_log_attachments_reach_the_model() -> None:
    service = AttachmentService(None)
    session_id, org_id = "sess_attach_pipeline", "org_attach"

    image = await service.store(
        session_id=session_id,
        org_id=org_id,
        file=_upload("screenshot.png", "image/png", _PNG_BYTES),
    )
    log = await service.store(
        session_id=session_id,
        org_id=org_id,
        file=_upload("server.log", "text/plain", b"ERROR boom\nTraceback line\n"),
    )

    refs = await service.resolve_refs(session_id, org_id, [image.id, log.id])
    assert len(refs) == 2
    assert any(ref.is_image and ref.data for ref in refs)
    assert any((not ref.is_image) and ref.text and "ERROR boom" in ref.text for ref in refs)

    gateway = RecordingGateway()
    registry = ToolRegistry(driver=FakeSandboxDriver(), session_id=session_id)
    orchestrator = AsyncReActOrchestrator(gateway=gateway, registry=registry)
    async for _ in orchestrator.process_user_turn(
        session_id, "What is failing in this screenshot and log?", attachments=refs
    ):
        pass

    assert len(gateway.requests) == 1
    formatted = LiteLLMGateway()._format_request_messages(
        gateway.requests[0].messages, "openrouter/anthropic/claude-3.7-sonnet"
    )
    user_entry = next(entry for entry in formatted if entry["role"] == "user")
    blocks = user_entry["content"]
    assert isinstance(blocks, list)
    assert any(block["type"] == "image_url" for block in blocks)
    inlined = " ".join(block.get("text", "") for block in blocks)
    assert "ERROR boom" in inlined and "server.log" in inlined


@pytest.mark.asyncio
async def test_unsupported_and_oversized_uploads_are_rejected() -> None:
    service = AttachmentService(None, max_bytes=10)

    with pytest.raises(AttachmentError):
        await service.store(
            session_id="s",
            org_id="o",
            file=_upload("payload.exe", "application/x-msdownload", b"MZ"),
        )

    with pytest.raises(AttachmentError, match="exceeding"):
        await service.store(
            session_id="s",
            org_id="o",
            file=_upload("big.log", "text/plain", b"x" * 64),
        )


@pytest.mark.asyncio
async def test_same_bytes_dedupe_within_a_session() -> None:
    service = AttachmentService(None)
    first = await service.store(
        session_id="sess_dedupe",
        org_id="org_a",
        file=_upload("a.log", "text/plain", b"same bytes"),
    )
    second = await service.store(
        session_id="sess_dedupe",
        org_id="org_a",
        file=_upload("b.log", "text/plain", b"same bytes"),
    )
    assert first.id == second.id
    assert first.sha256 == second.sha256


@pytest.mark.asyncio
async def test_signed_retrieval_token_round_trip() -> None:
    service = AttachmentService(None)
    record = await service.store(
        session_id="sess_signed",
        org_id="org_a",
        file=_upload("a.log", "text/plain", b"hello"),
    )
    token = service.issue_token(record.id, record.org_id)
    assert service.verify_token(token, record.id, record.org_id) is True
    assert service.verify_token(token, record.id, "org_other") is False
    assert service.verify_token("not-a-token", record.id, record.org_id) is False


@pytest.mark.asyncio
async def test_retrieval_is_tenant_scoped() -> None:
    """A row stored for one org is invisible to another (in-memory mirror of RLS)."""
    service = AttachmentService(None)
    record = await service.store(
        session_id="sess_tenant",
        org_id="org_a",
        file=_upload("a.log", "text/plain", b"tenant data"),
    )
    assert await service.get(record.id, "org_a") is not None
    assert await service.get(record.id, "org_b") is None


@pytest.mark.asyncio
async def test_database_persistence_round_trip(
    require_database: Callable[[Exception, str], None],
) -> None:
    """The PostgreSQL path persists a row that survives a fresh service instance."""
    admin_url = os.getenv("ADMIN_DATABASE_URL", "")
    if not admin_url:
        pytest.skip("ADMIN_DATABASE_URL not configured")
    manager = DatabaseSessionManager(admin_url)
    try:
        async with manager.session() as session:
            await session.execute(text("SELECT 1"))
    except Exception as err:
        require_database(err, admin_url)
    try:
        await manager.create_tables()
        suffix = uuid.uuid4().hex[:6]
        org_id, user_id = f"org_attach_{suffix}", f"user_attach_{suffix}"
        session_id = f"sess_attach_{suffix}"
        async with manager.session() as session:
            session.add(OrganizationModel(id=org_id, name=org_id, slug=f"org-attach-{suffix}"))
            session.add(
                UserModel(id=user_id, org_id=org_id, email=f"{user_id}@rocket.chat", name=user_id)
            )
            await session.flush()
            session.add(
                SessionModel(id=session_id, org_id=org_id, user_id=user_id, title=session_id)
            )

        service = AttachmentService(manager)
        record = await service.store(
            session_id=session_id,
            org_id=org_id,
            file=_upload("persisted.log", "text/plain", b"persisted bytes"),
        )

        # A second instance (cache cold) reads the row straight back from PostgreSQL.
        reloaded = await AttachmentService(manager).get(record.id, org_id)
        assert reloaded is not None
        assert reloaded.sha256 == record.sha256
        listed = await service.list_for_session(session_id, org_id)
        assert [item.id for item in listed] == [record.id]

        # Duplicate bytes in the same session dedupe against the stored row.
        duplicate = await service.store(
            session_id=session_id,
            org_id=org_id,
            file=_upload("again.log", "text/plain", b"persisted bytes"),
        )
        assert duplicate.id == record.id
    finally:
        await manager.close()

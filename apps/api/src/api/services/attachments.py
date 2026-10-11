"""Attachment ingestion for the web upload path.

One uploaded file is validated (MIME + per-org size cap), content-addressed by its
SHA-256, streamed into the session workspace and persisted as an ``attachments`` row so
it survives the request that carried it. Text-like files are also inlined under a byte
budget; images stay binary and reach the model as ``image_url`` blocks.

The service is deliberately storage-pluggable: with PostgreSQL configured it writes the
``attachments`` table under the tenant RLS context, and without a database (E2E runs)
it falls back to an in-memory registry, mirroring the session store. Either way a
failure raises a typed error so the caller can surface it instead of silently dropping
the file.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import os
import re
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import UploadFile
from sqlalchemy import select

from api.db.models import AttachmentModel
from api.db.session import DatabaseSessionManager
from specifications.interfaces.llm import AttachmentRef
from specifications.interfaces.sandbox import SandboxDriverProtocol

logger = logging.getLogger(__name__)

ATTACHMENTS_DIR_ENV = "ROCKET_ATTACHMENTS_DIR"
MAX_BYTES_ENV = "ROCKET_ATTACHMENT_MAX_BYTES"
INLINE_BYTES_ENV = "ROCKET_ATTACHMENT_INLINE_BYTES"
SIGNING_KEY_ENV = "ROCKET_ATTACHMENT_SIGNING_KEY"

DEFAULT_MAX_BYTES = 25 * 1024 * 1024
DEFAULT_INLINE_BYTES = 64 * 1024
# Tokens are short-lived: the cockpit fetches a preview immediately after upload, not
# long after, so a narrow window keeps a leaked URL from staying useful.
TOKEN_TTL_SECONDS = 15 * 60

IMAGE_MIME_TYPES = frozenset({"image/png", "image/jpeg", "image/gif", "image/webp", "image/bmp"})
# Documents whose bytes the model cannot usefully receive as text are still accepted and
# referenced by workspace path so Tier-2 tools can open them.
BINARY_DOCUMENT_MIME_TYPES = frozenset(
    {
        "application/pdf",
        "application/octet-stream",
        "application/vnd.ms-excel",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    }
)
TEXT_MIME_TYPES = frozenset(
    {"application/json", "application/xml", "application/x-yaml", "application/yaml"}
)
TEXT_EXTENSIONS = frozenset(
    {
        ".log",
        ".txt",
        ".md",
        ".markdown",
        ".csv",
        ".tsv",
        ".json",
        ".diff",
        ".patch",
        ".yaml",
        ".yml",
        ".xml",
        ".ini",
        ".cfg",
        ".conf",
        ".toml",
    }
)

_UNSAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


class AttachmentError(Exception):
    """A user-facing attachment failure (unsupported type, too large, missing)."""


@dataclass
class AttachmentRecord:
    """A persisted attachment row, decoupled from the ORM entity."""

    id: str
    session_id: str
    org_id: str
    name: str
    mime: str
    size: int
    sha256: str
    workspace_path: str
    created_at: int


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw and raw.strip().isdigit():
        return int(raw.strip())
    return default


def _attachments_root() -> Path:
    configured = os.getenv(ATTACHMENTS_DIR_ENV)
    if configured:
        return Path(configured)
    return Path(os.path.join(os.path.sep, "tmp", "rocket_chat_attachments"))


def _safe_name(name: str) -> str:
    """Reduce an uploaded filename to a single safe path segment."""
    basename = os.path.basename(name or "").strip() or "attachment"
    cleaned = _UNSAFE_NAME.sub("_", basename)
    # A leading dot would hide the file; leading dashes confuse shell tooling.
    return cleaned.lstrip(".").lstrip("-")[:255] or "attachment"


def _is_text(mime: str, name: str) -> bool:
    if mime.startswith("text/") or mime in TEXT_MIME_TYPES:
        return True
    return Path(name).suffix.lower() in TEXT_EXTENSIONS


def _validate_mime(mime: str, name: str) -> None:
    if mime in IMAGE_MIME_TYPES or mime in BINARY_DOCUMENT_MIME_TYPES or _is_text(mime, name):
        return
    raise AttachmentError(
        f"Unsupported attachment type '{mime or 'unknown'}'. "
        "Accepted: images, text files (.log, .txt, .md, .csv, .json, .diff) and PDF/office documents."
    )


class AttachmentService:
    """Validates, stores and dereferences session attachments."""

    def __init__(
        self,
        db_manager: DatabaseSessionManager | None,
        driver: SandboxDriverProtocol | None = None,
        *,
        max_bytes: int | None = None,
        inline_bytes: int | None = None,
    ) -> None:
        self._db = db_manager
        self._driver = driver
        self._max_bytes = max_bytes or _env_int(MAX_BYTES_ENV, DEFAULT_MAX_BYTES)
        self._inline_bytes = inline_bytes or _env_int(INLINE_BYTES_ENV, DEFAULT_INLINE_BYTES)
        self._root = _attachments_root()
        # E2E has no database: an in-memory registry keeps the upload round-trip working
        # exactly as the in-memory session store does.
        self._memory: dict[str, AttachmentRecord] = {}
        self._signing_key = self._resolve_signing_key()

    @staticmethod
    def _resolve_signing_key() -> bytes:
        configured = os.getenv(SIGNING_KEY_ENV)
        if configured:
            return configured.encode("utf-8")
        # Deriving from a process-local random secret means a token cannot be replayed
        # against another replica; signed retrieval is a preview convenience, not a
        # durability contract, so an ephemeral key is acceptable and avoids a weak default.
        return uuid.uuid4().bytes + uuid.uuid4().bytes

    # -- storage ---------------------------------------------------------------

    def _blob_path(self, sha256: str, name: str) -> Path:
        return self._root / sha256 / name

    async def store(
        self,
        *,
        session_id: str,
        org_id: str,
        file: UploadFile,
        name: str | None = None,
        org_cap_bytes: int | None = None,
    ) -> AttachmentRecord:
        """Validate and persist one uploaded file, deduping identical bytes per session."""
        filename = name or file.filename or "attachment"
        mime = (file.content_type or "application/octet-stream").split(";")[0].strip()
        _validate_mime(mime, filename)

        cap = org_cap_bytes or self._max_bytes
        data = await file.read()
        if len(data) > cap:
            raise AttachmentError(
                f"Attachment '{filename}' is {len(data)} bytes, exceeding the {cap} byte limit."
            )
        if not data:
            raise AttachmentError(f"Attachment '{filename}' is empty.")

        sha256 = hashlib.sha256(data).hexdigest()
        existing = await self._find_by_sha(session_id, org_id, sha256)
        if existing is not None:
            return existing

        safe = _safe_name(filename)
        blob = self._blob_path(sha256, safe)
        await self._write_blob(blob, data)

        workspace_path = f"/workspace/.attachments/{sha256}/{safe}"
        await self._mirror_to_workspace(session_id, mime, filename, workspace_path, data)

        record = AttachmentRecord(
            id=f"att_{uuid.uuid4().hex[:16]}",
            session_id=session_id,
            org_id=org_id,
            name=safe,
            mime=mime,
            size=len(data),
            sha256=sha256,
            workspace_path=workspace_path,
            created_at=int(time.time()),
        )
        await self._persist(record)
        return record

    async def store_bytes(
        self,
        *,
        session_id: str,
        org_id: str,
        user_id: str | None = None,
        name: str,
        mime: str,
        content: bytes,
    ) -> AttachmentRef:
        """Store non-HTTP bytes (e.g. a downloaded Slack file) through the same pipeline.

        This is the single ingestion entrypoint the Slack assistant injects, so web uploads
        and Slack files share one validation/dedupe/mirroring path. ``user_id``
        is accepted for signature parity with the web caller; the table is org/session scoped.
        """
        from io import BytesIO

        from starlette.datastructures import Headers

        upload = UploadFile(
            BytesIO(content),
            filename=name,
            headers=Headers({"content-type": mime}),
        )
        record = await self.store(session_id=session_id, org_id=org_id, file=upload, name=name)
        return await self.build_ref(record)

    async def _write_blob(self, path: Path, data: bytes) -> None:
        """Write bytes to the content-addressed host store without blocking the loop."""
        import anyio

        def _sync_write() -> None:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)

        try:
            await anyio.to_thread.run_sync(_sync_write)
        except OSError as err:
            raise AttachmentError(f"Failed to store attachment bytes: {err}") from err

    async def _read_blob(self, record: AttachmentRecord) -> bytes:
        import anyio

        path = self._blob_path(record.sha256, record.name)

        def _sync_read() -> bytes:
            return path.read_bytes()

        try:
            return await anyio.to_thread.run_sync(_sync_read)
        except OSError as err:
            raise AttachmentError(f"Attachment bytes are unavailable: {err}") from err

    async def _mirror_to_workspace(
        self,
        session_id: str,
        mime: str,
        name: str,
        workspace_path: str,
        data: bytes,
    ) -> None:
        """Best-effort copy into the sandbox workspace for Tier-2 tools.

        The driver protocol exposes only a text ``write_file``, so text-like files are
        mirrored there. Binary files (images, PDFs) still reach the model through the
        attachment ref and are logged rather than silently skipped when the workspace
        mirror is unavailable; a future ``write_bytes`` driver method removes the gap.
        """
        if self._driver is None:
            return
        if not _is_text(mime, name):
            write_bytes = getattr(self._driver, "write_bytes", None)
            if write_bytes is None:
                logger.info(
                    "Workspace mirror skipped for binary attachment %s: driver has no write_bytes",
                    workspace_path,
                )
                return
            try:
                await write_bytes(session_id, workspace_path, data)
            except Exception:
                logger.exception(
                    "Failed to mirror binary attachment to workspace: %s", workspace_path
                )
            return
        try:
            await self._driver.write_file(
                session_id, workspace_path, data.decode("utf-8", errors="replace")
            )
        except Exception:
            logger.exception("Failed to mirror attachment to workspace: %s", workspace_path)

    # -- persistence -----------------------------------------------------------

    def _model_to_record(self, model: AttachmentModel) -> AttachmentRecord:
        return AttachmentRecord(
            id=model.id,
            session_id=model.session_id,
            org_id=model.org_id,
            name=model.name,
            mime=model.mime,
            size=model.size,
            sha256=model.sha256,
            workspace_path=model.workspace_path,
            created_at=model.created_at,
        )

    async def _find_by_sha(
        self, session_id: str, org_id: str, sha256: str
    ) -> AttachmentRecord | None:
        if self._db is None:
            for record in self._memory.values():
                if (
                    record.session_id == session_id
                    and record.org_id == org_id
                    and record.sha256 == sha256
                ):
                    return record
            return None
        async with self._db.session(tenant_org_id=org_id) as session:
            result = await session.execute(
                select(AttachmentModel).where(
                    AttachmentModel.session_id == session_id,
                    AttachmentModel.sha256 == sha256,
                )
            )
            model = result.scalar_one_or_none()
            return self._model_to_record(model) if model else None

    async def _persist(self, record: AttachmentRecord) -> None:
        if self._db is None:
            self._memory[record.id] = record
            return
        async with self._db.session(tenant_org_id=record.org_id) as session:
            session.add(
                AttachmentModel(
                    id=record.id,
                    session_id=record.session_id,
                    org_id=record.org_id,
                    name=record.name,
                    mime=record.mime,
                    size=record.size,
                    sha256=record.sha256,
                    workspace_path=record.workspace_path,
                    created_at=record.created_at,
                )
            )

    async def get(self, attachment_id: str, org_id: str) -> AttachmentRecord | None:
        """Fetch one attachment, tenant-scoped by RLS (or the in-memory org key)."""
        if self._db is None:
            record = self._memory.get(attachment_id)
            if record is None or record.org_id != org_id:
                return None
            return record
        async with self._db.session(tenant_org_id=org_id) as session:
            model = await session.get(AttachmentModel, attachment_id)
            if model is None:
                return None
            return self._model_to_record(model)

    async def list_for_session(self, session_id: str, org_id: str) -> list[AttachmentRecord]:
        """List a session's attachments, newest last."""
        if self._db is None:
            return sorted(
                (
                    r
                    for r in self._memory.values()
                    if r.session_id == session_id and r.org_id == org_id
                ),
                key=lambda r: r.created_at,
            )
        async with self._db.session(tenant_org_id=org_id) as session:
            result = await session.execute(
                select(AttachmentModel)
                .where(AttachmentModel.session_id == session_id)
                .order_by(AttachmentModel.created_at)
            )
            return [self._model_to_record(model) for model in result.scalars().all()]

    # -- model dereferencing ---------------------------------------------------

    async def build_ref(self, record: AttachmentRecord) -> AttachmentRef:
        """Turn a stored row into the payload the gateway formats for the model.

        Images carry their bytes for an ``image_url`` block; text-like files carry their
        content, truncated to the inline budget so one large log cannot blow the context.
        """
        is_image = record.mime in IMAGE_MIME_TYPES
        if is_image:
            data = await self._read_blob(record)
            return AttachmentRef(
                name=record.name,
                mime=record.mime,
                attachment_id=record.id,
                sha256=record.sha256,
                size=record.size,
                workspace_path=record.workspace_path,
                is_image=True,
                data=data,
            )
        if _is_text(record.mime, record.name):
            data = await self._read_blob(record)
            text = data[: self._inline_bytes].decode("utf-8", errors="replace")
            if len(data) > self._inline_bytes:
                text += "\n… [attachment truncated at the inline byte budget]"
            return AttachmentRef(
                name=record.name,
                mime=record.mime,
                attachment_id=record.id,
                sha256=record.sha256,
                size=record.size,
                workspace_path=record.workspace_path,
                is_image=False,
                text=text,
            )
        # Binary document: referenced by workspace path, no bytes sent to the model.
        return AttachmentRef(
            name=record.name,
            mime=record.mime,
            sha256=record.sha256,
            size=record.size,
            workspace_path=record.workspace_path,
            is_image=False,
        )

    async def resolve_refs(
        self, session_id: str, org_id: str, attachment_ids: list[str]
    ) -> list[AttachmentRef]:
        """Resolve accepted attachment ids for the *current* turn.

        A missing id is skipped rather than failing the whole turn: the user may have
        pasted an id from another session, and the rest of the turn is still answerable.
        Tenant scoping is enforced by ``get`` so another org's id resolves to nothing.
        """
        refs: list[AttachmentRef] = []
        for attachment_id in attachment_ids:
            record = await self.get(attachment_id, org_id)
            if record is None or record.session_id != session_id:
                logger.warning(
                    "Ignoring unknown attachment id %s for session %s", attachment_id, session_id
                )
                continue
            refs.append(await self.build_ref(record))
        return refs

    # -- signed retrieval ------------------------------------------------------

    def issue_token(self, attachment_id: str, org_id: str) -> str:
        """Issue a short-lived HMAC token authorising a preview fetch."""
        expiry = int(time.time()) + TOKEN_TTL_SECONDS
        payload = f"{attachment_id}:{org_id}:{expiry}"
        signature = hmac.new(self._signing_key, payload.encode("utf-8"), hashlib.sha256).hexdigest()
        raw = f"{expiry}:{signature}"
        return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii")

    def verify_token(self, token: str, attachment_id: str, org_id: str) -> bool:
        """Constant-time validation of a retrieval token, rejecting expired ones."""
        try:
            decoded = base64.urlsafe_b64decode(token.encode("ascii")).decode("utf-8")
            expiry_str, signature = decoded.split(":", 1)
            expiry = int(expiry_str)
        except (ValueError, UnicodeDecodeError):
            return False
        if expiry < int(time.time()):
            return False
        payload = f"{attachment_id}:{org_id}:{expiry}"
        expected = hmac.new(self._signing_key, payload.encode("utf-8"), hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, signature)

    async def preview_data_url(self, record: AttachmentRecord) -> str | None:
        """A data URL for image previews; ``None`` when the bytes are not an image."""
        if record.mime not in IMAGE_MIME_TYPES:
            return None
        data = await self._read_blob(record)
        encoded = base64.b64encode(data).decode("ascii")
        return f"data:{record.mime};base64,{encoded}"

    async def read_bytes(self, record: AttachmentRecord) -> bytes:
        """Public accessor for an attachment's stored bytes (signed retrieval)."""
        return await self._read_blob(record)


def record_response(record: AttachmentRecord, preview: str | None) -> dict[str, Any]:
    """Serialise an attachment row for the REST response."""
    return {
        "attachment_id": record.id,
        "name": record.name,
        "mime": record.mime,
        "size": record.size,
        "sha256": record.sha256,
        "workspace_path": record.workspace_path,
        "preview": preview,
    }

"""Slack ``files[]`` ingestion into the shared attachment pipeline.

Slack delivers uploaded files as metadata on the ``app_mention`` / ``message`` event. The
bytes live behind ``url_private`` and require the bot token as a bearer credential, so they
must be downloaded before the model can see them.

This module deliberately does **not** re-implement storage or vision: it downloads the
bytes and delegates each file to the shared attachment service (S5's
``services/attachments.py``) through an injected ``ingest_attachment_fn``. That keeps a
single pipeline for web uploads and Slack uploads, as the spec requires. The exact call
the assistant makes is documented in the session report under "orchestrator changes
needed".
"""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from specifications.interfaces.llm import AttachmentRef

__all__ = [
    "FileDownloader",
    "IngestionResult",
    "SlackHttpFileDownloader",
    "SlackUploadedFile",
    "extract_uploaded_files",
    "ingest_slack_files",
]

# Text-like MIME types are inlined by the attachment service; images become vision blocks.
_IMAGE_MIME_PREFIX = "image/"


@dataclass(frozen=True)
class SlackUploadedFile:
    """The subset of a Slack ``file`` object the ingestion path needs."""

    file_id: str
    name: str
    mime: str
    size: int
    url_private: str
    url_private_download: str | None = None

    @property
    def is_image(self) -> bool:
        return self.mime.startswith(_IMAGE_MIME_PREFIX)


def extract_uploaded_files(event: Mapping[str, Any]) -> list[SlackUploadedFile]:
    """Extract uploadable Slack files from a Bolt event, ignoring malformed entries.

    Slack file IDs are prefixed ``F``. Entries without a private URL or a name are
    unusable (a deleted file, or an external upload) and are skipped rather than
    surfacing a half-built attachment.
    """
    raw_files = event.get("files")
    if not isinstance(raw_files, list):
        return []
    extracted: list[SlackUploadedFile] = []
    for raw in raw_files:
        if not isinstance(raw, Mapping):
            continue
        url = raw.get("url_private") or raw.get("url_private_download")
        file_id = str(raw.get("id") or "")
        name = str(raw.get("name") or "")
        if not url or not file_id or not name:
            continue
        extracted.append(
            SlackUploadedFile(
                file_id=file_id,
                name=name,
                mime=str(raw.get("mimetype") or "application/octet-stream"),
                size=int(raw.get("size") or 0),
                url_private=str(url),
                url_private_download=(
                    str(raw["url_private_download"]) if raw.get("url_private_download") else None
                ),
            )
        )
    return extracted


class FileDownloader(Protocol):
    """Downloads the bytes of one Slack file (bearer-authenticated)."""

    async def download(self, file: SlackUploadedFile) -> bytes: ...


@dataclass
class IngestionResult:
    """Outcome of one turn's file ingestion, including the bounded-overflow count."""

    attachments: list[AttachmentRef] = field(default_factory=list)
    ignored: int = 0
    failures: list[str] = field(default_factory=list)


class SlackHttpFileDownloader:
    """Fetches Slack-hosted files over authenticated HTTPS with an explicit timeout.

    ``aiohttp`` is already a declared dependency of this package, and the timeout is
    mandatory: an unbounded download in the request path would violate the async
    discipline invariant (no external call without a deadline).
    """

    def __init__(self, bot_token: str, timeout_seconds: float = 30.0) -> None:
        self._bot_token = bot_token
        self._timeout_seconds = timeout_seconds

    async def download(self, file: SlackUploadedFile) -> bytes:
        import aiohttp

        url = file.url_private_download or file.url_private
        headers = {"Authorization": f"Bearer {self._bot_token}"}
        timeout = aiohttp.ClientTimeout(total=self._timeout_seconds)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url, headers=headers) as response:
                response.raise_for_status()
                return await response.read()


IngestAttachmentFn = Callable[..., Awaitable[AttachmentRef] | AttachmentRef]


async def ingest_slack_files(
    *,
    files: Sequence[SlackUploadedFile],
    session_id: str,
    org_id: str,
    user_id: str,
    max_attachments: int,
    downloader: FileDownloader,
    ingest_attachment_fn: IngestAttachmentFn,
) -> IngestionResult:
    """Download up to ``max_attachments`` files and hand each to the shared service.

    Attachments beyond the per-turn cap are counted as ``ignored`` rather than silently
    dropped, so the assistant can tell the user why some files were not read. A single
    failed download never aborts the others.
    """
    bounded = max(0, max_attachments)
    selected = list(files[:bounded])
    result = IngestionResult(ignored=max(0, len(files) - len(selected)))
    for file in selected:
        try:
            content = await downloader.download(file)
            outcome = ingest_attachment_fn(
                session_id=session_id,
                org_id=org_id,
                user_id=user_id,
                name=file.name,
                mime=file.mime,
                content=content,
            )
            if inspect.isawaitable(outcome):
                outcome = await outcome
            result.attachments.append(outcome)
        except Exception as err:  # one bad file must not sink the turn
            result.failures.append(f"{file.name}: {err}")
    return result

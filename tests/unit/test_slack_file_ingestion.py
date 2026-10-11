"""Unit tests for Slack ``files[]`` ingestion."""

from slack_assistant.file_ingestion import (
    SlackUploadedFile,
    extract_uploaded_files,
    ingest_slack_files,
)

from specifications.interfaces.llm import AttachmentRef


class FakeDownloader:
    """Records requested files and returns deterministic bytes (no network)."""

    def __init__(self) -> None:
        self.requested: list[str] = []

    async def download(self, file: SlackUploadedFile) -> bytes:
        self.requested.append(file.file_id)
        return f"bytes-of-{file.file_id}".encode()


class FailingDownloader:
    async def download(self, file: SlackUploadedFile) -> bytes:
        raise RuntimeError("network down")


def _slack_file(file_id: str, name: str, mimetype: str) -> dict[str, object]:
    return {
        "id": file_id,
        "name": name,
        "mimetype": mimetype,
        "size": 12,
        "url_private": f"https://files.slack.com/{file_id}",
    }


def test_extract_uploaded_files_skips_malformed_entries() -> None:
    event = {
        "files": [
            _slack_file("F1", "server.log", "text/plain"),
            {"name": "no-url.txt"},
            {"id": "F3", "url_private": "https://files.slack.com/F3"},
            "not-a-dict",
        ]
    }
    files = extract_uploaded_files(event)
    assert [f.file_id for f in files] == ["F1"]
    assert files[0].mime == "text/plain"


def test_extract_uploaded_files_without_files_key() -> None:
    assert extract_uploaded_files({}) == []


def _ingest_fn_factory(recorded: list[dict[str, object]]):
    async def ingest_attachment_fn(**kwargs: object) -> AttachmentRef:
        recorded.append(kwargs)
        return AttachmentRef(
            name=str(kwargs["name"]),
            mime=str(kwargs["mime"]),
            sha256="deadbeef",
            size=len(kwargs["content"]) if isinstance(kwargs["content"], bytes) else 0,
            workspace_path=f"/workspace/.attachments/deadbeef/{kwargs['name']}",
            is_image=str(kwargs["mime"]).startswith("image/"),
        )

    return ingest_attachment_fn


async def test_ingest_delegates_every_file_to_the_shared_service() -> None:
    files = [
        SlackUploadedFile("F1", "server.log", "text/plain", 12, "https://files.slack.com/F1"),
        SlackUploadedFile("F2", "shot.png", "image/png", 12, "https://files.slack.com/F2"),
    ]
    downloader = FakeDownloader()
    recorded: list[dict[str, object]] = []

    result = await ingest_slack_files(
        files=files,
        session_id="slack_C1_1_0",
        org_id="acme",
        user_id="U1",
        max_attachments=5,
        downloader=downloader,
        ingest_attachment_fn=_ingest_fn_factory(recorded),
    )

    assert downloader.requested == ["F1", "F2"]
    assert [a.name for a in result.attachments] == ["server.log", "shot.png"]
    assert result.attachments[1].is_image is True
    assert result.ignored == 0
    assert result.failures == []
    assert recorded[0]["org_id"] == "acme"


async def test_ingest_counts_over_cap_files_as_ignored() -> None:
    files = [
        SlackUploadedFile(f"F{i}", f"file{i}.txt", "text/plain", 1, "https://x") for i in range(4)
    ]
    result = await ingest_slack_files(
        files=files,
        session_id="s",
        org_id="acme",
        user_id="U1",
        max_attachments=2,
        downloader=FakeDownloader(),
        ingest_attachment_fn=_ingest_fn_factory([]),
    )
    assert len(result.attachments) == 2
    assert result.ignored == 2


async def test_ingest_isolates_a_single_failed_download() -> None:
    files = [
        SlackUploadedFile("F1", "a.txt", "text/plain", 1, "https://x"),
        SlackUploadedFile("F2", "b.txt", "text/plain", 1, "https://y"),
    ]
    result = await ingest_slack_files(
        files=files,
        session_id="s",
        org_id="acme",
        user_id="U1",
        max_attachments=5,
        downloader=FailingDownloader(),
        ingest_attachment_fn=_ingest_fn_factory([]),
    )
    assert result.attachments == []
    assert len(result.failures) == 2

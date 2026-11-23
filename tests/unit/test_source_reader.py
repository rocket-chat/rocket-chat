"""Reading a GitHub repository for a source, and proposing a file change, over GitHub's REST API
(answered here by a mock transport)."""

from __future__ import annotations

import base64
import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from api.services.source_access import SourceAccess, normalise_repository
from git_engine import source_reader
from git_engine.source_reader import MAX_FILE_BYTES, GitHubSourceReader, GitHubSourceWriter

from specifications.interfaces.external_sources import ExternalSource, SourceFile, SourceReadError

API = "https://api.github.com"
Handler = Callable[[httpx.Request], httpx.Response]


def _github(monkeypatch: pytest.MonkeyPatch, routes: dict[str, Any]) -> list[httpx.Request]:
    seen: list[httpx.Request] = []
    real = httpx.AsyncClient

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        key = f"{request.method} {request.url.raw_path.decode()}"
        answer = routes.get(key)
        if answer is None:
            return httpx.Response(404, json={"message": "Not Found"})
        status, body = answer
        if isinstance(body, bytes):
            return httpx.Response(status, content=body)
        return httpx.Response(status, json=body)

    monkeypatch.setattr(
        source_reader.httpx,
        "AsyncClient",
        lambda **kwargs: real(transport=httpx.MockTransport(handler), **kwargs),
    )
    return seen


async def test_the_reader_resolves_lists_and_reads_with_the_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = _github(
        monkeypatch,
        {
            "GET /repos/acme/skills/commits/main": (200, {"sha": "abc"}),
            "GET /repos/acme/skills/git/trees/abc?recursive=1": (
                200,
                {
                    "truncated": False,
                    "tree": [
                        {"path": "a", "type": "tree", "sha": "t1"},
                        {
                            "path": "a/SKILL.md",
                            "type": "blob",
                            "sha": "b1",
                            "size": 5,
                            "mode": "100644",
                        },
                        {"path": "a/run", "type": "blob", "sha": "b2", "size": 2, "mode": "100755"},
                    ],
                },
            ),
            "GET /repos/acme/skills/git/blobs/b1": (200, b"hello"),
        },
    )
    reader = GitHubSourceReader(API, "https://github.com", "acme/skills", "tok")

    commit = await reader.resolve("main")
    files = await reader.list_files(commit)
    content = await reader.read(files[0])

    assert commit == "abc"
    assert files == [
        SourceFile("a/SKILL.md", "b1", 5, False),
        SourceFile("a/run", "b2", 2, True),
    ]
    assert content == b"hello"
    assert all(r.headers["Authorization"] == "Bearer tok" for r in seen)
    assert reader.web_url("main", "a") == "https://github.com/acme/skills/tree/main/a"


async def test_a_refusal_says_why_and_a_truncated_tree_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _github(
        monkeypatch,
        {
            "GET /repos/acme/skills/git/trees/abc?recursive=1": (
                200,
                {"truncated": True, "tree": []},
            )
        },
    )
    reader = GitHubSourceReader(API, "https://github.com", "acme/skills", None)

    with pytest.raises(SourceReadError, match="cannot see it, HTTP 404"):
        await reader.resolve("main")
    with pytest.raises(SourceReadError, match="more files than GitHub lists"):
        await reader.list_files("abc")


async def test_a_large_file_is_not_read() -> None:
    reader = GitHubSourceReader(API, "https://github.com", "acme/skills", None)

    with pytest.raises(SourceReadError, match="over the"):
        await reader.read(SourceFile("data.bin", "b", MAX_FILE_BYTES + 1))


async def test_the_writer_branches_commits_and_opens_a_pull_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = _github(
        monkeypatch,
        {
            "GET /repos/acme/skills/git/ref/heads/main": (200, {"object": {"sha": "base"}}),
            "GET /repos/acme/skills/contents/a/SKILL.md?ref=base": (200, {"sha": "b1"}),
            "POST /repos/acme/skills/git/refs": (201, {}),
            "PUT /repos/acme/skills/contents/a/SKILL.md": (200, {}),
            "POST /repos/acme/skills/pulls": (
                201,
                {"number": 7, "html_url": "https://github.com/acme/skills/pull/7"},
            ),
        },
    )
    writer = GitHubSourceWriter(API, "https://github.com", "acme/skills", "member-token")

    change = await writer.propose_file_change(
        base="main",
        path="a/SKILL.md",
        content="new",
        expected_blob_id="b1",
        branch="rocket/skill-a-123456",
        title="Update a",
        body="why",
    )

    assert (change.number, change.url) == (7, "https://github.com/acme/skills/pull/7")
    put = next(r for r in seen if r.method == "PUT")
    payload = json.loads(put.content)
    assert base64.b64decode(payload["content"]) == b"new"
    assert (payload["sha"], payload["branch"]) == ("b1", "rocket/skill-a-123456")


async def test_the_writer_refuses_when_the_file_moved_since_the_last_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = _github(
        monkeypatch,
        {
            "GET /repos/acme/skills/git/ref/heads/main": (200, {"object": {"sha": "base"}}),
            "GET /repos/acme/skills/contents/a/SKILL.md?ref=base": (200, {"sha": "newer"}),
        },
    )
    writer = GitHubSourceWriter(API, "https://github.com", "acme/skills", "member-token")

    with pytest.raises(SourceReadError, match="changed in acme/skills"):
        await writer.propose_file_change(
            base="main",
            path="a/SKILL.md",
            content="new",
            expected_blob_id="b1",
            branch="b",
            title="t",
            body="",
        )
    assert not any(r.method == "POST" for r in seen)


async def test_a_failed_commit_removes_the_branch_it_created(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = _github(
        monkeypatch,
        {
            "GET /repos/acme/skills/git/ref/heads/main": (200, {"object": {"sha": "base"}}),
            "GET /repos/acme/skills/contents/a/SKILL.md?ref=base": (200, {"sha": "b1"}),
            "POST /repos/acme/skills/git/refs": (201, {}),
            "PUT /repos/acme/skills/contents/a/SKILL.md": (403, {"message": "no push"}),
            "DELETE /repos/acme/skills/git/refs/heads/b": (204, {}),
        },
    )
    writer = GitHubSourceWriter(API, "https://github.com", "acme/skills", "member-token")

    with pytest.raises(SourceReadError, match="no push"):
        await writer.propose_file_change(
            base="main",
            path="a/SKILL.md",
            content="x",
            expected_blob_id="b1",
            branch="b",
            title="t",
            body="",
        )
    assert seen[-1].method == "DELETE"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("acme/skills", "acme/skills"),
        ("https://github.com/acme/skills.git", "acme/skills"),
        ("git@github.com:acme/skills.git", "acme/skills"),
        (" https://github.com/acme/skills/ ", "acme/skills"),
    ],
)
def test_a_repository_is_recognised_in_the_forms_people_paste(raw: str, expected: str) -> None:
    assert normalise_repository(raw) == expected


def test_something_that_is_not_a_repository_is_refused() -> None:
    with pytest.raises(ValueError, match="not a GitHub repository"):
        normalise_repository("just-a-name")


async def test_an_app_source_without_a_github_app_says_how_to_fix_it() -> None:
    source = ExternalSource(id="s", org_id="org", name="n", repository="acme/skills", ref="main")

    with pytest.raises(SourceReadError, match="install it on that repository"):
        await SourceAccess(None, None).reader(source)


async def test_a_member_source_without_a_linked_account_says_how_to_fix_it() -> None:
    source = ExternalSource(
        id="s",
        org_id="org",
        name="n",
        repository="acme/skills",
        ref="main",
        auth="member",
        scope="user",
        user_id="u_nolink",
    )

    with pytest.raises(SourceReadError, match="reconnect it"):
        await SourceAccess(None, None).reader(source)

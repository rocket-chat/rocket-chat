"""Reading a GitHub repository for an external source, and proposing a change to one of its files.

Both talk to GitHub's REST API from the platform with a token the caller resolved (the
organization's App installation, or a member's linked account); nothing runs in a sandbox and
nothing is cloned. A file is read by its blob id, so reading an unchanged file again is avoidable
by the caller and a commit's contents are read consistently.
"""

from __future__ import annotations

import base64
from typing import Any
from urllib.parse import quote

import httpx

from specifications.interfaces.external_sources import SourceFile, SourceReadError
from specifications.interfaces.git_host import ChangeRequest

GITHUB_TIMEOUT_SECONDS = 15.0
# A file larger than this is not read: these are instructions and small scripts, and a stray
# binary or dataset in a skill folder must not be pulled into the database.
MAX_FILE_BYTES = 1_000_000
_EXECUTABLE_MODE = "100755"
_ACCESS_HINT = {
    401: "the credential was refused (expired or revoked)",
    403: "the credential may not read it",
    404: "it does not exist, or the credential cannot see it",
}


def _refusal(response: httpx.Response, what: str) -> SourceReadError:
    hint = _ACCESS_HINT.get(response.status_code, "GitHub refused")
    detail = ""
    try:
        payload = response.json()
        detail = str(payload.get("message") or "") if isinstance(payload, dict) else ""
    except ValueError:
        detail = response.text[:200]
    suffix = f" ({detail})" if detail else ""
    return SourceReadError(f"{what}: {hint}, HTTP {response.status_code}{suffix}.")


class _GitHubClient:
    def __init__(self, api_url: str, repository: str, token: str | None) -> None:
        self._api = api_url.rstrip("/")
        self.repository = repository
        self._token = token

    def _headers(self, accept: str) -> dict[str, str]:
        headers = {
            "Accept": accept,
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "Rocket-Chat",
        }
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        return headers

    async def call(
        self,
        method: str,
        path: str,
        what: str,
        *,
        body: dict[str, Any] | None = None,
        accept: str = "application/vnd.github+json",
    ) -> httpx.Response:
        url = f"{self._api}/repos/{self.repository}{path}"
        try:
            async with httpx.AsyncClient(timeout=GITHUB_TIMEOUT_SECONDS) as client:
                response = await client.request(
                    method, url, json=body, headers=self._headers(accept)
                )
        except httpx.HTTPError as err:
            raise SourceReadError(f"{what}: GitHub could not be reached ({err}).") from err
        if response.status_code >= 400:
            raise _refusal(response, what)
        return response

    async def json(self, method: str, path: str, what: str, **kwargs: Any) -> dict[str, Any]:
        payload = (await self.call(method, path, what, **kwargs)).json()
        return payload if isinstance(payload, dict) else {}


class GitHubSourceReader:
    """``SourceReaderProtocol`` over GitHub's REST API."""

    def __init__(self, api_url: str, web_url: str, repository: str, token: str | None) -> None:
        self.repository = repository
        self._web = web_url.rstrip("/")
        self._client = _GitHubClient(api_url, repository, token)

    async def resolve(self, ref: str) -> str:
        found = await self._client.json(
            "GET", f"/commits/{quote(ref, safe='/')}", f"Branch '{ref}' of {self.repository}"
        )
        sha = str(found.get("sha") or "")
        if not sha:
            raise SourceReadError(f"Branch '{ref}' of {self.repository} has no commit.")
        return sha

    async def list_files(self, commit: str) -> list[SourceFile]:
        tree = await self._client.json(
            "GET", f"/git/trees/{commit}?recursive=1", f"The files of {self.repository}"
        )
        if tree.get("truncated"):
            raise SourceReadError(
                f"{self.repository} has more files than GitHub lists in one tree; "
                "connect a smaller repository."
            )
        return [
            SourceFile(
                path=str(item.get("path") or ""),
                blob_id=str(item.get("sha") or ""),
                size=int(item.get("size") or 0),
                executable=item.get("mode") == _EXECUTABLE_MODE,
            )
            for item in tree.get("tree") or []
            if isinstance(item, dict) and item.get("type") == "blob"
        ]

    async def read(self, file: SourceFile) -> bytes:
        if file.size > MAX_FILE_BYTES:
            raise SourceReadError(
                f"{file.path} is {file.size:,} bytes, over the {MAX_FILE_BYTES:,} read for a file."
            )
        response = await self._client.call(
            "GET", f"/git/blobs/{file.blob_id}", file.path, accept="application/vnd.github.raw"
        )
        return response.content

    def web_url(self, ref: str, path: str) -> str:
        return f"{self._web}/{self.repository}/tree/{quote(ref, safe='/')}/{quote(path, safe='/')}"


class GitHubSourceWriter:
    """``SourceWriterProtocol`` over GitHub's REST API, with a member's own token: the branch,
    the commit and the pull request are theirs, under their rights on the repository."""

    def __init__(self, api_url: str, web_url: str, repository: str, token: str) -> None:
        self._client = _GitHubClient(api_url, repository, token)
        self._repository = repository

    async def propose_file_change(
        self,
        *,
        base: str,
        path: str,
        content: str,
        expected_blob_id: str,
        branch: str,
        title: str,
        body: str,
    ) -> ChangeRequest:
        head = await self._client.json(
            "GET", f"/git/ref/heads/{quote(base, safe='/')}", f"Branch '{base}'"
        )
        base_sha = str((head.get("object") or {}).get("sha") or "")
        current = await self._client.json(
            "GET", f"/contents/{quote(path, safe='/')}?ref={base_sha}", path
        )
        if str(current.get("sha") or "") != expected_blob_id:
            raise SourceReadError(
                f"{path} changed in {self._repository} since it was last synchronised; "
                "synchronise the source, then propose the change again."
            )
        await self._client.json(
            "POST",
            "/git/refs",
            f"Creating branch '{branch}'",
            body={"ref": f"refs/heads/{branch}", "sha": base_sha},
        )
        try:
            return await self._commit_and_open(
                path, content, expected_blob_id, branch, base, title, body
            )
        except SourceReadError:
            await self._forget_branch(branch)
            raise

    async def _commit_and_open(
        self,
        path: str,
        content: str,
        blob_id: str,
        branch: str,
        base: str,
        title: str,
        body: str,
    ) -> ChangeRequest:
        await self._client.json(
            "PUT",
            f"/contents/{quote(path, safe='/')}",
            f"Committing {path}",
            body={
                "message": title,
                "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
                "sha": blob_id,
                "branch": branch,
            },
        )
        made = await self._client.json(
            "POST",
            "/pulls",
            "Opening the pull request",
            body={"title": title, "head": branch, "base": base, "body": body},
        )
        return ChangeRequest(
            self._repository, int(made.get("number") or 0), str(made.get("html_url") or "")
        )

    async def _forget_branch(self, branch: str) -> None:
        # Best effort: a branch left behind by a failed proposal is harmless but untidy.
        try:
            await self._client.call(
                "DELETE", f"/git/refs/heads/{quote(branch, safe='/')}", "Removing the branch"
            )
        except SourceReadError:
            return

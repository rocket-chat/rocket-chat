"""An in-memory repository standing in for GitHub behind external sources.

``FakeRepository`` holds commits of files; ``FakeSourceAccess`` hands out a reader and a writer on
it, and replaces ``app.state.source_access`` (``install``) so the API reads it instead of GitHub.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any

from api.services.source_access import NO_LINKED_ACCOUNT, SourceAccess

from specifications.interfaces.external_sources import (
    ExternalSource,
    SourceFile,
    SourceReadError,
)
from specifications.interfaces.git_host import ChangeRequest


def blob_id(content: bytes) -> str:
    """Git's blob id: equal contents, equal ids."""
    return hashlib.sha1(b"blob %d\0" % len(content) + content).hexdigest()


@dataclass
class ProposedChange:
    base: str
    path: str
    content: str
    branch: str
    title: str
    body: str
    user_id: str


@dataclass
class FakeRepository:
    repository: str = "acme/agent-skills"
    branch: str = "main"
    # path -> (content, executable)
    files: dict[str, tuple[bytes, bool]] = field(default_factory=dict)
    commits: dict[str, dict[str, tuple[bytes, bool]]] = field(default_factory=dict)
    head: str = ""
    reads: list[str] = field(default_factory=list)
    proposals: list[ProposedChange] = field(default_factory=list)
    error: str = ""

    def commit(self, files: dict[str, str | bytes], executable: tuple[str, ...] = ()) -> str:
        """Replace every file of the branch; returns the new commit."""
        self.files = {
            path: (text.encode("utf-8") if isinstance(text, str) else text, path in executable)
            for path, text in files.items()
        }
        self.head = f"c{len(self.commits) + 1:039d}"
        self.commits[self.head] = dict(self.files)
        return self.head

    def change(self, path: str, text: str | None) -> str:
        """Change (or with ``None`` remove) one file in a new commit."""
        files: dict[str, str | bytes] = {p: c for p, (c, _) in self.files.items()}
        executable = tuple(p for p, (_, x) in self.files.items() if x)
        if text is None:
            files.pop(path, None)
        else:
            files[path] = text
        return self.commit(files, executable)


class FakeReader:
    def __init__(self, repo: FakeRepository) -> None:
        self._repo = repo
        self.repository = repo.repository

    async def resolve(self, ref: str) -> str:
        if self._repo.error:
            raise SourceReadError(self._repo.error)
        if ref != self._repo.branch:
            raise SourceReadError(f"Branch '{ref}' of {self.repository}: it does not exist.")
        return self._repo.head

    async def list_files(self, commit: str) -> list[SourceFile]:
        return [
            SourceFile(path=p, blob_id=blob_id(c), size=len(c), executable=x)
            for p, (c, x) in sorted(self._repo.commits[commit].items())
        ]

    async def read(self, file: SourceFile) -> bytes:
        self._repo.reads.append(file.path)
        for files in self._repo.commits.values():
            for path, (content, _) in files.items():
                if path == file.path and blob_id(content) == file.blob_id:
                    return content
        raise SourceReadError(f"{file.path}: no such blob.")

    def web_url(self, ref: str, path: str) -> str:
        return f"https://github.com/{self.repository}/tree/{ref}/{path}"


class FakeWriter:
    def __init__(self, repo: FakeRepository, user_id: str) -> None:
        self._repo, self._user = repo, user_id

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
        current = self._repo.files.get(path)
        if current is None or blob_id(current[0]) != expected_blob_id:
            raise SourceReadError(f"{path} changed since it was last synchronised.")
        self._repo.proposals.append(
            ProposedChange(base, path, content, branch, title, body, self._user)
        )
        number = len(self._repo.proposals)
        return ChangeRequest(
            self._repo.repository,
            number,
            f"https://github.com/{self._repo.repository}/pull/{number}",
        )


class FakeSourceAccess(SourceAccess):
    """Every source reads ``repo``; members in ``linked`` have a GitHub account linked."""

    def __init__(self, repo: FakeRepository, linked: tuple[str, ...] = ()) -> None:
        super().__init__(None, None)
        self.repo, self.linked = repo, set(linked)

    async def reader(self, source: ExternalSource) -> FakeReader:
        if source.auth == "member" and source.user_id not in self.linked:
            raise SourceReadError("The linked GitHub account of this source's owner is missing.")
        return FakeReader(self.repo)

    async def writer(self, org_id: str, user_id: str, source: ExternalSource) -> FakeWriter:
        if user_id not in self.linked:
            raise SourceReadError(NO_LINKED_ACCOUNT)
        return FakeWriter(self.repo, user_id)


def install(app: Any, repo: FakeRepository, linked: tuple[str, ...] = ()) -> FakeSourceAccess:
    access = FakeSourceAccess(repo, linked)
    app.state.source_access = access
    return access


def skill_md(name: str, description: str, body: str = "Do the thing.") -> str:
    return f"---\nname: {name}\ndescription: {description}\n---\n\n{body}\n"


# The layout of birotaio/agent-skills after its plugin manifest: one plugin per team.
MARKETPLACE = """{
  "name": "fifteen",
  "plugins": [
    {"name": "data", "source": "./teams", "skills": ["./data"], "description": "Data team"},
    {"name": "backend", "source": "./teams", "skills": ["./backend"]},
    {"name": "remote", "source": {"source": "github", "repo": "acme/other"}}
  ]
}"""

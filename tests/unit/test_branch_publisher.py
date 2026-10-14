"""Publishing a branch with real git: a plain push, or GitHub-signed commits through the API.

A bare repository stands in for GitHub, and a small simulated GitHub creates the commits that
``createCommitOnBranch`` would, authored by the App, so the replay, the re-sync and the
refusals are exercised against real repositories rather than mocks of git.
"""

from __future__ import annotations

import base64
import json
import pathlib
import subprocess
from collections.abc import Iterator

import httpx
import pytest
from git_engine import publish as publish_module
from git_engine.publish import BranchPublisher, PublishError

from specifications.interfaces.git import PublishSpec, SigningMode
from tests.support.local_git_driver import LocalGitDriver

REPOSITORY = "acme/widgets"
APP_AUTHOR = "widgets-app[bot] <1+widgets-app[bot]@users.noreply.github.com>"


def git(cwd: pathlib.Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


class SimulatedGitHub:
    """The two endpoints the publisher uses, backed by a bare repository."""

    def __init__(self, bare: pathlib.Path, scratch: pathlib.Path) -> None:
        self.bare, self.scratch = bare, scratch
        self.tokens: list[str] = []
        subprocess.run(["git", "clone", "-q", str(bare), str(scratch)], check=True)
        git(scratch, "config", "user.name", "widgets-app[bot]")
        git(scratch, "config", "user.email", "1+widgets-app[bot]@users.noreply.github.com")

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.tokens.append(request.headers["authorization"])
        body = json.loads(request.content)
        if request.url.path.endswith("/git/refs"):
            git(self.bare, "update-ref", body["ref"], body["sha"])
            return httpx.Response(201, json={})
        return self._create_commit(body["variables"]["input"])

    def _create_commit(self, data: dict[str, object]) -> httpx.Response:
        branch = data["branch"]["branchName"]  # type: ignore[index]
        git(self.scratch, "fetch", "-q", "origin")
        remote_head = git(self.bare, "rev-parse", f"refs/heads/{branch}")
        if remote_head != data["expectedHeadOid"]:
            return httpx.Response(
                200, json={"errors": [{"message": "Expected branch to point to the head"}]}
            )
        git(self.scratch, "checkout", "-q", "-B", branch, remote_head)
        changes = data["fileChanges"]
        for added in changes["additions"]:  # type: ignore[index]
            target = self.scratch / added["path"]
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(base64.b64decode(added["contents"]))
        for removed in changes["deletions"]:  # type: ignore[index]
            (self.scratch / removed["path"]).unlink()
        git(self.scratch, "add", "-A")
        message = data["message"]
        text = message["headline"] + ("\n\n" + message["body"] if message["body"] else "")  # type: ignore[index]
        git(self.scratch, "commit", "-q", "--author", APP_AUTHOR, "-m", text)
        git(self.scratch, "push", "-q", "origin", f"HEAD:refs/heads/{branch}")
        oid = git(self.scratch, "rev-parse", "HEAD")
        return httpx.Response(
            200, json={"data": {"createCommitOnBranch": {"commit": {"oid": oid}}}}
        )


class Rig:
    def __init__(self, tmp: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        bare = tmp / "remote.git"
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(bare)], check=True)
        seed = tmp / "seed"
        subprocess.run(["git", "clone", "-q", str(bare), str(seed)], check=True)
        git(seed, "config", "user.name", "Seed")
        git(seed, "config", "user.email", "seed@example.com")
        (seed / "README.md").write_text("hello\n")
        (seed / "run.sh").write_text("#!/bin/sh\n")
        git(seed, "add", "-A")
        git(seed, "commit", "-q", "-m", "Initial commit")
        git(seed, "push", "-q", "origin", "HEAD:refs/heads/main")

        self.work = tmp / "work"
        subprocess.run(["git", "clone", "-q", str(bare), str(self.work)], check=True)
        git(self.work, "config", "user.name", "Rocket Bot")
        git(self.work, "config", "user.email", "bot@rocket.test")
        # The remote reads as GitHub's address, while real git talks to the local bare repo.
        github_url = f"https://github.com/{REPOSITORY}.git"
        git(self.work, "config", "remote.origin.url", github_url)
        git(self.work, "config", f"url.{bare}.insteadOf", github_url)

        self.github = SimulatedGitHub(bare, tmp / "scratch")
        real = httpx.AsyncClient
        monkeypatch.setattr(
            publish_module.httpx,
            "AsyncClient",
            lambda **kwargs: real(transport=httpx.MockTransport(self.github.handler), **kwargs),
        )
        self.driver = LocalGitDriver(self.work, tmp / "shim")
        self.bare = bare
        self.minted: list[tuple[str, int | None]] = []
        self.app_token: str | None = "ghs_app_installation"

    async def mint(self, repository: str, installation_id: int | None) -> str | None:
        self.minted.append((repository, installation_id))
        return self.app_token

    def publisher(self) -> BranchPublisher:
        return BranchPublisher(self.driver, "https://api.github.com", self.mint)  # type: ignore[arg-type]

    def commit(self, name: str, content: str, message: str) -> None:
        (self.work / name).write_text(content)
        git(self.work, "add", "-A")
        git(self.work, "commit", "-q", "-m", message)


@pytest.fixture
def rig(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Rig]:
    yield Rig(tmp_path, monkeypatch)


API = PublishSpec(signing_mode=SigningMode.GITHUB_APP, installations=((7, "acme"),))


async def test_new_commits_are_replayed_through_the_api_and_the_branch_follows(rig: Rig) -> None:
    git(rig.work, "checkout", "-q", "-b", "feat/rocket-x")
    rig.commit("a.txt", "one\n", "Add a\n\nCo-authored-by: Alice <alice@x.test>")
    (rig.work / "README.md").write_text("hello again\n")
    (rig.work / "a.txt").unlink()
    git(rig.work, "add", "-A")
    git(rig.work, "commit", "-q", "-m", "Change readme, drop a")
    (rig.work / "scratch.txt").write_text("uncommitted\n")

    result = await rig.publisher().publish("s1", API)

    assert result.via == "github_api" and result.commits == 2 and result.branch == "feat/rocket-x"
    remote = git(rig.bare, "log", "--format=%an|%s", "refs/heads/feat/rocket-x", "-3")
    assert remote.splitlines()[:2] == [
        "widgets-app[bot]|Change readme, drop a",
        "widgets-app[bot]|Add a",
    ]
    assert "Co-authored-by: Alice <alice@x.test>" in git(
        rig.bare, "log", "-1", "--format=%B", "refs/heads/feat/rocket-x~1"
    )
    assert git(rig.bare, "show", "refs/heads/feat/rocket-x:README.md") == "hello again"
    assert git(rig.work, "rev-parse", "HEAD") == git(
        rig.bare, "rev-parse", "refs/heads/feat/rocket-x"
    )
    assert (rig.work / "scratch.txt").read_text() == "uncommitted\n", "uncommitted work survives"
    assert result.head_sha == git(rig.work, "rev-parse", "HEAD")
    assert rig.minted == [(REPOSITORY, 7)] and set(rig.github.tokens) == {
        "Bearer ghs_app_installation"
    }


async def test_a_member_token_makes_the_commits_the_members(rig: Rig) -> None:
    git(rig.work, "checkout", "-q", "-b", "feat/own")
    rig.commit("b.txt", "b\n", "Add b")

    await rig.publisher().publish(
        "s1", PublishSpec(signing_mode=SigningMode.GITHUB_APP, token="gho_member")
    )

    assert rig.minted == [] and set(rig.github.tokens) == {"Bearer gho_member"}


async def test_a_second_publish_adds_to_the_existing_remote_branch(rig: Rig) -> None:
    git(rig.work, "checkout", "-q", "-b", "feat/again")
    rig.commit("a.txt", "1\n", "First")
    await rig.publisher().publish("s1", API)
    rig.commit("c.txt", "2\n", "Second")

    result = await rig.publisher().publish("s1", API)

    assert result.commits == 1
    assert git(rig.bare, "rev-list", "--count", "refs/heads/main..refs/heads/feat/again") == "2"
    assert await rig.publisher().publish("s1", API) is not None
    assert (await rig.publisher().publish("s1", API)).commits == 0, "nothing left to publish"


async def test_a_remote_branch_that_moved_on_is_not_overwritten(rig: Rig) -> None:
    git(rig.work, "checkout", "-q", "-b", "feat/diverged")
    rig.commit("a.txt", "1\n", "Local")
    git(rig.github.scratch, "fetch", "-q", "origin")
    git(rig.github.scratch, "checkout", "-q", "-B", "feat/diverged", "origin/main")
    (rig.github.scratch / "other.txt").write_text("remote\n")
    git(rig.github.scratch, "add", "-A")
    git(rig.github.scratch, "commit", "-q", "-m", "Remote only")
    git(rig.github.scratch, "push", "-q", "origin", "feat/diverged")

    with pytest.raises(PublishError, match="rebase"):
        await rig.publisher().publish("s1", API)


async def test_executable_files_publish_with_a_notice_and_symlinks_are_refused(rig: Rig) -> None:
    git(rig.work, "checkout", "-q", "-b", "feat/modes")
    script = rig.work / "tool.sh"
    script.write_text("#!/bin/sh\n")
    script.chmod(0o755)
    git(rig.work, "add", "-A")
    git(rig.work, "commit", "-q", "-m", "Add tool")
    notice = (await rig.publisher().publish("s1", API)).notices
    assert any("tool.sh" in line and "executable" in line for line in notice)

    (rig.work / "link").symlink_to("README.md")
    git(rig.work, "add", "-A")
    git(rig.work, "commit", "-q", "-m", "Add link")
    with pytest.raises(PublishError, match="symbolic link"):
        await rig.publisher().publish("s1", API)


async def test_merge_commits_are_refused_with_the_way_out(rig: Rig) -> None:
    git(rig.work, "checkout", "-q", "-b", "feat/merge")
    rig.commit("a.txt", "1\n", "On branch")
    git(rig.work, "checkout", "-q", "-b", "side", "main")
    rig.commit("b.txt", "2\n", "On side")
    git(rig.work, "checkout", "-q", "feat/merge")
    git(rig.work, "merge", "-q", "--no-ff", "-m", "Merge side", "side")

    with pytest.raises(PublishError, match="merge commits"):
        await rig.publisher().publish("s1", API)


async def test_without_any_write_credential_the_reason_is_given(rig: Rig) -> None:
    git(rig.work, "checkout", "-q", "-b", "feat/none")
    rig.commit("a.txt", "1\n", "First")
    rig.app_token = None

    with pytest.raises(PublishError, match="Contents: Read & write"):
        await rig.publisher().publish("s1", API)


async def test_a_plain_push_hands_the_token_to_that_command_only(rig: Rig) -> None:
    git(rig.work, "checkout", "-q", "-b", "feat/plain")
    rig.commit("a.txt", "1\n", "First")
    hook = rig.work / ".git" / "hooks" / "pre-push"
    seen = rig.work / "hook-env.txt"
    hook.write_text(f'#!/bin/sh\necho "$ROCKET_PUBLISH|$GITHUB_TOKEN" > {seen}\n')
    hook.chmod(0o755)

    result = await rig.publisher().publish(
        "s1", PublishSpec(signing_mode=SigningMode.NONE, token="gho_member")
    )

    assert result.via == "git_push"
    assert seen.read_text().strip() == "1|gho_member"
    assert git(rig.bare, "rev-parse", "refs/heads/feat/plain") == git(rig.work, "rev-parse", "HEAD")
    session_env = [env for _, env in rig.driver.calls if "GITHUB_TOKEN" in env]
    assert len(session_env) == 1 and session_env[0]["GITHUB_TOKEN"] == "gho_member"

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
from dataclasses import replace

import httpx
import pytest
from agent_core.tools.git_tools import describe_publication
from api.services.git_signing import gpg_available, inspect_private_key
from git_engine import publish as publish_module
from git_engine.publish import BranchPublisher, PublishError, repository_of

from specifications.interfaces.git import CoAuthor, PublishSpec, SigningKey, SigningMode
from tests.support.gpg_keys import make_key
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
        if "/git/refs/heads/" in request.url.path:
            branch = request.url.path.split("/git/refs/heads/", 1)[1]
            git(self.bare, "update-ref", f"refs/heads/{branch}", body["sha"])
            return httpx.Response(200, json={})
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
        # "GitHub" for the platform's push: <git_base_url>/acme/widgets.git, which serves
        # partial and by-commit fetches as GitHub does.
        self.git_base_url = f"file://{tmp}/remotes"
        bare = tmp / "remotes" / f"{REPOSITORY}.git"
        bare.parent.mkdir(parents=True)
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(bare)], check=True)
        git(bare, "config", "uploadpack.allowFilter", "true")
        git(bare, "config", "uploadpack.allowAnySHA1InWant", "true")
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
        return BranchPublisher(
            self.driver,  # type: ignore[arg-type]
            "https://api.github.com",
            self.mint,
            git_base_url=self.git_base_url,
        )

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
    published = git(rig.bare, "rev-list", "--reverse", "-2", "refs/heads/feat/rocket-x").split()
    assert [new for _, new in result.rewritten] == published, "each commit's replacement"
    assert all(old != new for old, new in result.rewritten)
    told = describe_publication(result)
    assert "hashes changed" in told and f"-> {published[-1][:12]}" in told
    assert rig.minted == [(REPOSITORY, 7)] and set(rig.github.tokens) == {
        "Bearer ghs_app_installation"
    }


async def test_the_member_is_credited_on_every_commit_the_app_re_creates(rig: Rig) -> None:
    # A commit made in the shell with plain `git commit` never went through git_commit.
    git(rig.work, "checkout", "-q", "-b", "feat/credit")
    rig.commit("c.txt", "c\n", "chore: empty commit to trigger CI")
    rig.commit("d.txt", "d\n", "Add d\n\nCo-authored-by: Nathan <nathan@x.test>")
    member = CoAuthor(name="Nathan", email="nathan@x.test")

    await rig.publisher().publish("s1", replace(API, co_authors=(member,)))

    messages = git(rig.bare, "log", "--format=%B%x00", "refs/heads/feat/credit", "-2").split("\0")
    credited = [m.count("Co-authored-by: Nathan <nathan@x.test>") for m in messages if m.strip()]
    assert credited == [1, 1], "added where missing, never twice"


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


async def test_a_plain_push_happens_on_the_platform_and_nothing_in_the_sandbox_sees_it(
    rig: Rig, tmp_path: pathlib.Path
) -> None:
    git(rig.work, "checkout", "-q", "-b", "feat/plain")
    rig.commit("a.txt", "1\n", "First")
    # Everything the agent could set up in its sandbox to catch or divert a push.
    evil = tmp_path / "evil.git"
    git(tmp_path, "init", "-q", "--bare", str(evil))
    seen = rig.work / "hook-env.txt"
    hooks = rig.work / ".git" / "hooks"
    (hooks / "pre-push").write_text(f"#!/bin/sh\nenv > {seen}\n")
    (hooks / "pre-push").chmod(0o755)
    git(rig.work, "config", "remote.origin.pushurl", str(evil))
    git(rig.work, "config", "credential.helper", f"!f() {{ cat > {seen}; }}; f")
    git(rig.work, "config", f"url.{evil}.pushInsteadOf", "https://github.com/")

    result = await rig.publisher().publish(
        "s1", PublishSpec(signing_mode=SigningMode.NONE, token="gho_member")
    )

    assert result.via == "git_push" and result.commits == 1
    assert git(rig.bare, "rev-parse", "refs/heads/feat/plain") == git(rig.work, "rev-parse", "HEAD")
    assert not seen.exists(), "no sandbox hook or helper took part in the push"
    assert git(evil, "for-each-ref") == "", "nothing was pushed where the sandbox pointed"
    assert not any(
        "gho_member" in str(env) or "gho_member" in command for command, env in rig.driver.calls
    ), "the token never reached the sandbox"
    assert git(rig.work, "rev-parse", "refs/remotes/origin/feat/plain") == result.head_sha


async def test_gpg_mode_signs_the_published_commits_with_the_members_key_on_the_platform(
    rig: Rig, tmp_path: pathlib.Path
) -> None:
    if not gpg_available():
        pytest.skip("needs GnuPG")
    private, public = make_key(tmp_path, "Octo Cat <octo@example.com>", passphrase="pw")
    info = await inspect_private_key(private, "pw")
    git(rig.work, "checkout", "-q", "-b", "feat/signed")
    rig.commit("a.txt", "1\n", "First")
    rig.commit("b.txt", "2\n", "Second")
    local = git(rig.work, "rev-list", "main..HEAD").split()
    (rig.work / "dirty.txt").write_text("uncommitted\n")

    result = await rig.publisher().publish(
        "s1",
        PublishSpec(
            signing_mode=SigningMode.GPG_KEY,
            token="gho_member",
            signing_key=SigningKey(info.fingerprint, private, "pw"),
        ),
    )

    remote = git(rig.bare, "rev-list", "main..refs/heads/feat/signed").split()
    assert len(remote) == 2 and set(remote).isdisjoint(local), "re-created, hashes changed"
    for sha in remote:
        assert "gpgsig" in git(rig.bare, "cat-file", "commit", sha)
    assert git(rig.bare, "log", "--format=%s|%an", "refs/heads/feat/signed", "-2").splitlines() == [
        "Second|Rocket Bot",
        "First|Rocket Bot",
    ], "messages and authorship are kept"
    assert git(rig.work, "rev-parse", "HEAD") == result.head_sha == remote[0]
    assert (rig.work / "dirty.txt").read_text() == "uncommitted\n"
    assert any("Signed with your GPG key" in note for note in result.notices)
    assert result.rewritten and result.rewritten[-1][1] == result.head_sha, "re-signed hashes"
    assert public, "the key pair is real"


async def test_force_with_lease_replaces_a_diverged_remote_branch_it_has_seen(rig: Rig) -> None:
    git(rig.work, "checkout", "-q", "-b", "feat/force")
    rig.commit("a.txt", "1\n", "Local")
    git(rig.github.scratch, "fetch", "-q", "origin")
    git(rig.github.scratch, "checkout", "-q", "-B", "feat/force", "origin/main")
    rig.github.scratch.joinpath("other.txt").write_text("remote\n")
    git(rig.github.scratch, "add", "-A")
    git(rig.github.scratch, "commit", "-q", "-m", "Remote only")
    git(rig.github.scratch, "push", "-q", "origin", "feat/force")
    plain = PublishSpec(signing_mode=SigningMode.NONE, token="t")

    with pytest.raises(PublishError, match="rebase"):
        await rig.publisher().publish("s1", plain)
    forced = PublishSpec(signing_mode=SigningMode.NONE, token="t", force_with_lease=True)
    result = await rig.publisher().publish("s1", forced)

    assert git(rig.bare, "log", "-1", "--format=%s", "refs/heads/feat/force") == "Local"
    assert result.commits == 1


async def test_force_with_lease_replays_through_github_app_mode(rig: Rig) -> None:
    git(rig.work, "checkout", "-q", "-b", "feat/force-api")
    rig.commit("a.txt", "1\n", "Initial local")
    # Publish initially
    await rig.publisher().publish("s1", API)

    # Now simulate divergence: someone pushes to remote or we rebase
    git(rig.github.scratch, "fetch", "-q", "origin")
    git(rig.github.scratch, "checkout", "-q", "feat/force-api")
    rig.github.scratch.joinpath("remote.txt").write_text("remote change\n")
    git(rig.github.scratch, "add", "-A")
    git(rig.github.scratch, "commit", "-q", "-m", "Remote commit on branch")
    git(rig.github.scratch, "push", "-q", "origin", "feat/force-api")

    # Local commit after rebase
    rig.commit("b.txt", "2\n", "Rebased local feature commit")

    # Publishing without force_with_lease should fail
    with pytest.raises(PublishError, match="rebase"):
        await rig.publisher().publish("s1", API)

    # Publishing with force_with_lease=True should succeed and reset remote ref
    forced = replace(API, force_with_lease=True)
    result = await rig.publisher().publish("s1", forced)

    assert result.via == "github_api"
    assert result.commits == 2
    assert "Remote commit on branch" not in git(
        rig.bare, "log", "--format=%s", "refs/heads/feat/force-api"
    )
    assert "Rebased local feature commit" in git(
        rig.bare, "log", "--format=%s", "refs/heads/feat/force-api"
    )


async def test_dynamic_base_detection_picks_closest_ancestor_branch(rig: Rig) -> None:
    # Create origin/beta with multiple commits and a merge commit
    git(rig.github.scratch, "fetch", "-q", "origin")
    git(rig.github.scratch, "checkout", "-q", "-b", "beta", "origin/main")
    rig.github.scratch.joinpath("beta1.txt").write_text("beta 1\n")
    git(rig.github.scratch, "add", "-A")
    git(rig.github.scratch, "commit", "-q", "-m", "Beta commit 1")

    # Add a merge commit on beta
    git(rig.github.scratch, "checkout", "-q", "-b", "temp-side", "beta")
    rig.github.scratch.joinpath("side.txt").write_text("side\n")
    git(rig.github.scratch, "add", "-A")
    git(rig.github.scratch, "commit", "-q", "-m", "Side commit")
    git(rig.github.scratch, "checkout", "-q", "beta")
    git(rig.github.scratch, "merge", "-q", "--no-ff", "-m", "Merge side into beta", "temp-side")
    git(rig.github.scratch, "push", "-q", "origin", "beta")

    # Local sandbox fetches beta and branches off beta
    git(rig.work, "fetch", "-q", "origin")
    git(rig.work, "checkout", "-q", "-b", "feat/from-beta", "origin/beta")
    rig.commit("feature.txt", "feat\n", "Feature commit only")

    # Publish with GITHUB_APP mode, default_branch is "main"
    # Even though default_branch is "main" and beta has a merge commit,
    # dynamic base detection must pick beta as the base, ensuring only the 1 feature commit is replayed.
    result = await rig.publisher().publish("s1", API)

    assert result.via == "github_api"
    assert result.commits == 1
    assert (
        git(rig.bare, "log", "-1", "--format=%s", "refs/heads/feat/from-beta")
        == "Feature commit only"
    )


async def test_a_branch_deleted_on_the_remote_is_published_again_as_new(rig: Rig) -> None:
    git(rig.work, "checkout", "-q", "-b", "feat/again")
    rig.commit("a.txt", "1\n", "First")
    plain = PublishSpec(signing_mode=SigningMode.NONE, token="t")
    await rig.publisher().publish("s1", plain)
    git(rig.bare, "update-ref", "-d", "refs/heads/feat/again")  # merged and deleted
    rig.commit("b.txt", "2\n", "Second")

    result = await rig.publisher().publish("s1", plain)

    assert result.commits == 2 and any("Created the branch" in n for n in result.notices)
    assert git(rig.bare, "rev-list", "--count", "main..refs/heads/feat/again") == "2"


@pytest.mark.parametrize(
    "url, expected",
    [
        ("https://github.com/acme/widgets.git", "acme/widgets"),
        ("https://github.com/acme/widgets", "acme/widgets"),
        ("git@github.com:acme/widgets.git", "acme/widgets"),
        ("ssh://git@github.com/acme/widgets", "acme/widgets"),
        ("https://evil.example/github.com/acme/widgets", None),
        ("https://github.com.evil.example/acme/widgets", None),
        ("https://token@github.com/acme/widgets", None),
        ("file:///tmp/github.com/acme/widgets", None),
        ("https://github.com/acme/widgets/extra", None),
    ],
)
def test_only_a_github_origin_on_the_configured_host_is_published_to(
    url: str, expected: str | None
) -> None:
    if expected is None:
        with pytest.raises(PublishError):
            repository_of(url)
    else:
        assert repository_of(url) == expected


async def test_a_repository_cloned_into_a_subfolder_is_found_and_published(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = Rig(tmp_path, monkeypatch)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (tmp_path / "work").rename(workspace / "backend-services")
    rig.driver.workdir = workspace  # /workspace now holds the clone, not a repository itself
    rig.work = workspace / "backend-services"
    git(rig.work, "checkout", "-q", "-b", "feat/sub")
    rig.commit("a.txt", "1\n", "First")

    result = await rig.publisher().publish("s1", API)

    assert result.commits == 1 and result.branch == "feat/sub"
    assert git(rig.bare, "rev-parse", "refs/heads/feat/sub") == git(rig.work, "rev-parse", "HEAD")


async def test_a_named_folder_decides_between_several_repositories(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = Rig(tmp_path, monkeypatch)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (tmp_path / "work").rename(workspace / "one")
    (workspace / "two").mkdir()
    git(workspace / "two", "init", "-q")
    rig.driver.workdir = workspace
    git(workspace / "one", "checkout", "-q", "-b", "feat/pick")
    (workspace / "one" / "a.txt").write_text("1\n")
    git(workspace / "one", "add", "-A")
    git(workspace / "one", "commit", "-q", "-m", "First")

    with pytest.raises(PublishError, match="/workspace/one, /workspace/two"):
        await rig.publisher().publish("s1", API)
    named = PublishSpec(
        signing_mode=SigningMode.GITHUB_APP, installations=((7, "acme"),), path="one"
    )
    assert (await rig.publisher().publish("s1", named)).commits == 1


async def test_without_a_repository_the_agent_is_told_to_clone_one(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = Rig(tmp_path, monkeypatch)
    empty = tmp_path / "empty"
    empty.mkdir()
    rig.driver.workdir = empty

    with pytest.raises(PublishError, match="clone one first"):
        await rig.publisher().publish("s1", API)


async def test_the_members_own_token_publishes_when_no_app_installation_can(rig: Rig) -> None:
    git(rig.work, "checkout", "-q", "-b", "feat/fallback")
    rig.commit("a.txt", "1\n", "First")
    rig.app_token = None

    result = await rig.publisher().publish(
        "s1",
        PublishSpec(signing_mode=SigningMode.GITHUB_APP, fallback_token="gho_member"),
    )

    assert set(rig.github.tokens) == {"Bearer gho_member"} and result.commits == 1
    assert any("Published with your GitHub account" in note for note in result.notices)


async def test_the_missing_credential_error_says_what_to_check(rig: Rig) -> None:
    git(rig.work, "checkout", "-q", "-b", "feat/nothing")
    rig.commit("a.txt", "1\n", "First")
    rig.app_token = None

    with pytest.raises(PublishError) as raised:
        await rig.publisher().publish("s1", API)

    assert "installation mapped" in str(raised.value) and "link one" in str(raised.value)


async def test_an_empty_repository_receives_the_whole_branch_or_a_clear_refusal(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = Rig(tmp_path, monkeypatch)
    empty = tmp_path / "remotes" / "acme" / "fresh.git"
    git(tmp_path, "init", "-q", "--bare", "-b", "main", str(empty))
    work = tmp_path / "fresh"
    git(tmp_path, "init", "-q", "-b", "main", str(work))
    git(work, "config", "user.name", "Rocket Bot")
    git(work, "config", "user.email", "bot@rocket.test")
    git(work, "config", "remote.origin.url", "https://github.com/acme/fresh.git")
    git(work, "config", f"url.{empty}.insteadOf", "https://github.com/acme/fresh.git")
    (work / "app.py").write_text("print('hi')\n")
    git(work, "add", "-A")
    git(work, "commit", "-q", "-m", "First commit")
    rig.driver.workdir = work

    with pytest.raises(PublishError, match="no main branch yet"):
        await rig.publisher().publish("s1", API)
    result = await rig.publisher().publish(
        "s1", PublishSpec(signing_mode=SigningMode.NONE, token="t")
    )

    assert result.commits == 1 and git(empty, "log", "--format=%s", "main") == "First commit"


def test_the_co_authored_policy_credits_the_member_on_app_commits_only() -> None:
    from agent_core.tools.git_tools import publish_spec_for_policy

    from specifications.interfaces.agent import GitPolicy

    member = GitPolicy(
        authorship="co_authored",
        signing_mode="github_app",
        user_name="Nathan",
        user_email="nathan@x.test",
    )

    assert publish_spec_for_policy(member).co_authors == (CoAuthor("Nathan", "nathan@x.test"),)
    assert publish_spec_for_policy(replace(member, authorship="bot_only")).co_authors == ()
    assert publish_spec_for_policy(replace(member, authorship="user_only")).co_authors == (), (
        "the member's own commits need no credit"
    )

"""The Changes view reads what a session changed with real git, and can undo one file."""

from __future__ import annotations

import pathlib
import subprocess
from collections.abc import Iterator

import pytest
from agent_core.tools import git_tools
from api.main import app
from api.services import workspace_changes as changes
from fastapi.testclient import TestClient

from specifications.interfaces.sandbox import SandboxStatus
from tests.support.local_git_driver import LocalGitDriver

SESSION = "s1"
REPO = "/workspace"


def git(cwd: pathlib.Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


class _Running(LocalGitDriver):
    async def get_status(self, session_id: str) -> SandboxStatus:
        return SandboxStatus.RUNNING


@pytest.fixture
def repo(tmp_path: pathlib.Path) -> Iterator[tuple[pathlib.Path, _Running]]:
    work = tmp_path / "work"
    work.mkdir()
    git(work, "init", "-q", "-b", "main")
    git(work, "config", "user.name", "Test")
    git(work, "config", "user.email", "t@example.com")
    (work / "keep.txt").write_text("one\ntwo\nthree\n")
    (work / "gone.txt").write_text("to be deleted\n")
    (work / "image.bin").write_bytes(b"\x00\x01\x02binary")
    git(work, "add", "-A")
    git(work, "commit", "-q", "-m", "base")
    yield work, _Running(work, tmp_path / "shim")


def _by_path(found: changes.RepositoryChanges) -> dict[str, changes.ChangedFile]:
    return {item.path: item for item in found.files}


async def test_added_modified_deleted_and_untracked_files_are_listed_with_counts(
    repo: tuple[pathlib.Path, _Running],
) -> None:
    work, driver = repo
    (work / "keep.txt").write_text("one\nTWO\nthree\nfour\n")
    (work / "gone.txt").unlink()
    (work / "new.txt").write_text("a\nb\n")
    (work / "image.bin").write_bytes(b"\x00\x09\x09changed")

    (found,) = await changes.session_changes(driver, SESSION, "session")  # type: ignore[arg-type]

    files = _by_path(found)
    assert set(files) == {"keep.txt", "gone.txt", "new.txt", "image.bin"}
    assert (files["keep.txt"].status, files["keep.txt"].additions, files["keep.txt"].deletions) == (
        "modified",
        2,
        1,
    )
    assert files["gone.txt"].status == "deleted" and files["gone.txt"].deletions == 1
    assert files["new.txt"].status == "added" and files["new.txt"].additions == 2
    assert files["image.bin"].binary is True
    assert found.name == "workspace" or found.path == REPO


async def test_a_clean_repository_has_no_changes(repo: tuple[pathlib.Path, _Running]) -> None:
    _, driver = repo

    (found,) = await changes.session_changes(driver, SESSION, "session")  # type: ignore[arg-type]

    assert found.files == []


async def test_the_turn_scope_only_shows_what_changed_since_the_latest_turn_snapshot(
    repo: tuple[pathlib.Path, _Running],
) -> None:
    work, driver = repo
    (work / "keep.txt").write_text("earlier edit\n")
    await git_tools.git_checkpoint(driver, SESSION, label="turn", workdir=REPO)  # type: ignore[arg-type]
    (work / "later.txt").write_text("this turn\n")

    (turn,) = await changes.session_changes(driver, SESSION, "turn")  # type: ignore[arg-type]
    (whole,) = await changes.session_changes(driver, SESSION, "session")  # type: ignore[arg-type]

    assert set(_by_path(turn)) == {"later.txt"}
    assert turn.base_label == "since the start of the latest turn"
    assert set(_by_path(whole)) == {"keep.txt", "later.txt"}


@pytest.fixture
def branched(tmp_path: pathlib.Path) -> tuple[pathlib.Path, _Running]:
    """A clone on a feature branch, one commit and one uncommitted file ahead of origin/main."""
    origin = tmp_path / "origin.git"
    seed = tmp_path / "seed"
    work = tmp_path / "work"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
    subprocess.run(["git", "clone", "-q", str(origin), str(seed)], check=True)
    git(seed, "config", "user.name", "T")
    git(seed, "config", "user.email", "t@example.com")
    (seed / "a.txt").write_text("base\n")
    git(seed, "add", "-A")
    git(seed, "commit", "-q", "-m", "base")
    git(seed, "push", "-q", "origin", "HEAD:main")
    subprocess.run(["git", "clone", "-q", str(origin), str(work)], check=True)
    git(work, "config", "user.name", "T")
    git(work, "config", "user.email", "t@example.com")
    git(work, "checkout", "-q", "-b", "feat/x")
    (work / "a.txt").write_text("base\nchanged and committed\n")
    git(work, "commit", "-qam", "work")
    (work / "b.txt").write_text("uncommitted\n")
    return work, _Running(work, tmp_path / "shim")


async def test_the_session_scope_is_measured_from_where_the_work_branched(
    branched: tuple[pathlib.Path, _Running],
) -> None:
    _, driver = branched

    (found,) = await changes.session_changes(driver, SESSION, "session")  # type: ignore[arg-type]

    assert set(_by_path(found)) == {"a.txt", "b.txt"}, "committed and uncommitted work"
    assert found.base_label == "since it branched from main"


async def test_the_session_scope_starts_where_the_session_started_not_at_the_default_branch(
    branched: tuple[pathlib.Path, _Running],
) -> None:
    work, driver = branched
    # The session cloned a branch that already differed from main by a.txt: that is not its work.
    git(work, "update-ref", git_tools.SESSION_BASE_REF, "HEAD")

    (found,) = await changes.session_changes(driver, SESSION, "session")  # type: ignore[arg-type]

    assert set(_by_path(found)) == {"b.txt"}, "only what changed after the session started"
    assert found.base_label == "since the session started"


async def test_the_base_is_recorded_once_and_never_moved(
    repo: tuple[pathlib.Path, _Running],
) -> None:
    work, driver = repo
    first = git(work, "rev-parse", "HEAD")
    await driver.exec_command(SESSION, git_tools.mark_session_base_command(), workdir=REPO)
    (work / "later.txt").write_text("x\n")
    git(work, "add", "-A")
    git(work, "commit", "-q", "-m", "later")
    await driver.exec_command(SESSION, git_tools.mark_session_base_command(), workdir=REPO)

    assert git(work, "rev-parse", git_tools.SESSION_BASE_REF) == first


async def test_a_file_is_returned_whole_at_the_base_and_as_it_is_now(
    repo: tuple[pathlib.Path, _Running],
) -> None:
    work, driver = repo
    (work / "keep.txt").write_text("one\nTWO\nthree\n")
    (work / "new.txt").write_text("fresh\n")
    (work / "gone.txt").unlink()

    kept = await changes.file_versions(driver, SESSION, REPO, "session", "keep.txt")  # type: ignore[arg-type]
    new = await changes.file_versions(driver, SESSION, REPO, "session", "new.txt")  # type: ignore[arg-type]
    gone = await changes.file_versions(driver, SESSION, REPO, "session", "gone.txt")  # type: ignore[arg-type]

    assert (kept.original, kept.modified) == ("one\ntwo\nthree\n", "one\nTWO\nthree\n")
    assert (new.original, new.modified) == (None, "fresh\n")
    assert (gone.original, gone.modified) == ("to be deleted\n", None)


async def test_a_binary_file_has_no_text_to_show(repo: tuple[pathlib.Path, _Running]) -> None:
    work, driver = repo
    (work / "image.bin").write_bytes(b"\x00changed\x00")

    versions = await changes.file_versions(driver, SESSION, REPO, "session", "image.bin")  # type: ignore[arg-type]

    assert versions.binary is True and versions.original is None and versions.modified is None


async def test_a_huge_file_is_cut_and_marked(
    repo: tuple[pathlib.Path, _Running], monkeypatch: pytest.MonkeyPatch
) -> None:
    work, driver = repo
    monkeypatch.setattr(changes, "MAX_FILE_BYTES", 100)
    (work / "keep.txt").write_text("x" * 500)

    versions = await changes.file_versions(driver, SESSION, REPO, "session", "keep.txt")  # type: ignore[arg-type]

    assert versions.truncated is True and len(versions.modified or "") == 100


@pytest.mark.parametrize("path", ["../outside.txt", "/etc/passwd", "a/../../b", "x\ny", ""])
async def test_a_path_outside_the_repository_is_refused(
    repo: tuple[pathlib.Path, _Running], path: str
) -> None:
    _, driver = repo
    with pytest.raises(changes.ChangesError):
        await changes.file_versions(driver, SESSION, REPO, "session", path)  # type: ignore[arg-type]
    with pytest.raises(changes.ChangesError):
        await changes.revert_file(driver, SESSION, REPO, "session", path)  # type: ignore[arg-type]


async def test_only_a_repository_of_the_session_can_be_named(
    repo: tuple[pathlib.Path, _Running],
) -> None:
    _, driver = repo
    with pytest.raises(changes.ChangesError):
        await changes.file_versions(driver, SESSION, "/etc", "session", "passwd")  # type: ignore[arg-type]


async def test_reverting_restores_a_modified_or_deleted_file_and_removes_an_added_one(
    repo: tuple[pathlib.Path, _Running],
) -> None:
    work, driver = repo
    (work / "keep.txt").write_text("edited\n")
    (work / "gone.txt").unlink()
    (work / "new.txt").write_text("fresh\n")

    for name in ("keep.txt", "gone.txt", "new.txt"):
        await changes.revert_file(driver, SESSION, REPO, "session", name)  # type: ignore[arg-type]

    assert (work / "keep.txt").read_text() == "one\ntwo\nthree\n"
    assert (work / "gone.txt").read_text() == "to be deleted\n"
    assert not (work / "new.txt").exists()
    (found,) = await changes.session_changes(driver, SESSION, "session")  # type: ignore[arg-type]
    assert found.files == []


async def test_reverting_to_the_turn_snapshot_keeps_the_earlier_work(
    repo: tuple[pathlib.Path, _Running],
) -> None:
    work, driver = repo
    (work / "keep.txt").write_text("before the turn\n")
    await git_tools.git_checkpoint(driver, SESSION, label="turn", workdir=REPO)  # type: ignore[arg-type]
    (work / "keep.txt").write_text("during the turn\n")

    await changes.revert_file(driver, SESSION, REPO, "turn", "keep.txt")  # type: ignore[arg-type]

    assert (work / "keep.txt").read_text() == "before the turn\n"


# -- the HTTP routes ---------------------------------------------------------------------

OWNER = {"X-Tenant-Org-Id": "test_org", "X-Tenant-User-Id": "chg_owner", "X-User-Role": "developer"}
VIEWER = {**OWNER, "X-Tenant-User-Id": "chg_viewer", "X-User-Role": "readonly"}
STRANGER = {**OWNER, "X-Tenant-User-Id": "chg_stranger"}


@pytest.fixture
def api_session(client: TestClient, repo: tuple[pathlib.Path, _Running]) -> str:
    work, driver = repo
    created = client.post(
        "/v1/sessions", json={"title": "changes", "is_shared": True}, headers=OWNER
    ).json()
    app.state.sandbox_driver = driver
    (work / "keep.txt").write_text("edited\n")
    return str(created["session_id"])


def test_the_changes_route_lists_files_and_reads_one(client: TestClient, api_session: str) -> None:
    listing = client.get(f"/v1/sessions/{api_session}/changes", headers=OWNER).json()

    assert listing["available"] is True and listing["scope"] == "session"
    repository = listing["repositories"][0]
    assert [f["path"] for f in repository["files"]] == ["keep.txt"]

    versions = client.get(
        f"/v1/sessions/{api_session}/changes/file",
        params={"repository": repository["path"], "path": "keep.txt"},
        headers=OWNER,
    ).json()
    assert versions["original"] == "one\ntwo\nthree\n" and versions["modified"] == "edited\n"


def test_without_a_running_sandbox_nothing_is_available(client: TestClient) -> None:
    created = client.post("/v1/sessions", json={"title": "idle"}, headers=OWNER).json()
    app.state.sandbox_driver = None

    body = client.get(f"/v1/sessions/{created['session_id']}/changes", headers=OWNER).json()

    assert body == {"available": False, "scope": "session", "repositories": []}


def test_a_read_only_member_may_look_but_not_revert(client: TestClient, api_session: str) -> None:
    assert client.get(f"/v1/sessions/{api_session}/changes", headers=VIEWER).status_code == 200

    denied = client.post(
        f"/v1/sessions/{api_session}/changes/revert",
        json={"repository": REPO, "path": "keep.txt"},
        headers=VIEWER,
    )

    assert denied.status_code == 403


def test_a_private_sessions_changes_are_invisible_to_others(
    client: TestClient, repo: tuple[pathlib.Path, _Running]
) -> None:
    _, driver = repo
    created = client.post(
        "/v1/sessions", json={"title": "private", "is_shared": False}, headers=OWNER
    ).json()
    app.state.sandbox_driver = driver

    response = client.get(f"/v1/sessions/{created['session_id']}/changes", headers=STRANGER)

    assert response.status_code == 403


def test_the_revert_route_undoes_one_file_and_refuses_bad_paths(
    client: TestClient, api_session: str, repo: tuple[pathlib.Path, _Running]
) -> None:
    work, _ = repo
    bad = client.post(
        f"/v1/sessions/{api_session}/changes/revert",
        json={"repository": REPO, "path": "../x"},
        headers=OWNER,
    )
    assert bad.status_code == 422

    ok = client.post(
        f"/v1/sessions/{api_session}/changes/revert",
        json={"repository": REPO, "path": "keep.txt"},
        headers=OWNER,
    )

    assert ok.status_code == 200 and (work / "keep.txt").read_text() == "one\ntwo\nthree\n"


async def test_tool_snapshot_hashes_modified_untracked_and_deleted_with_stdin_paths(
    repo: tuple[pathlib.Path, _Running],
) -> None:
    from agent_core.tools import workspace_changes as tool_ws_changes

    work, driver = repo
    before = await tool_ws_changes.snapshot(driver, SESSION, REPO)
    assert before[1] == {}

    (work / "keep.txt").write_text("one\nmodified\n")
    (work / "untracked.py").write_text("print(1)\n")
    (work / "gone.txt").unlink()

    after = await tool_ws_changes.snapshot(driver, SESSION, REPO)
    assert set(after[1]) == {"keep.txt", "untracked.py", "gone.txt"}
    assert after[1]["gone.txt"] == "gone"
    assert len(after[1]["keep.txt"]) == 40
    assert len(after[1]["untracked.py"]) == 40
    assert tool_ws_changes.changed_paths(before, after) == [
        "gone.txt",
        "keep.txt",
        "untracked.py",
    ]

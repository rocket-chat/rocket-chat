"""Platform parity: GitLab behind the git host contract, workspace snapshots, the stateful
browser, repository skills and dev container images, code embeddings, knowledge imports and
model-call tracing."""

from __future__ import annotations

import base64
import io
import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from agent_core.tools import browser as browser_tools
from agent_core.tools.git_tools import parse_pull_request_url
from api.services import knowledge_import, repository_skills, workspace_snapshots
from api.services.code_index import CodeEmbeddings, build_code_index
from api.services.git_gateway import GatewayRefusal, parse_repository_path
from api.services.git_gateway_env import sandbox_git_environment
from api.services.repo_setup import devcontainer_image
from git_engine.gitlab import GitLabHost, GitLabSettings
from git_engine.publish import PublishError, repository_of
from sandbox_driver.fake_driver import FakeSandboxDriver
from sandbox_driver.k8s_driver import parse_storage_bytes

from specifications.interfaces.sandbox import ExecResult, WorkspaceSnapshotter
from specifications.interfaces.session import SessionRecord

# -- GitLab --------------------------------------------------------------------------------------


def _gitlab(secret: str = "s") -> GitLabHost:
    return GitLabHost(GitLabSettings(url="https://gitlab.example.com", webhook_secret=secret))


def test_gitlab_owns_its_remotes_and_reads_nested_groups() -> None:
    host = _gitlab()
    assert host.owns("https://gitlab.example.com/group/sub/app.git")
    assert host.owns("git@gitlab.example.com:group/app.git")
    assert not host.owns("https://github.com/acme/app.git")
    assert host.repository_of("https://gitlab.example.com/group/sub/app.git") == "group/sub/app"
    with pytest.raises(PublishError):
        repository_of("https://gitlab.example.com/group/sub/app.git", "gitlab.example.com")


def test_gitlab_webhooks_need_the_secret_token() -> None:
    body = json.dumps({"object_kind": "merge_request"}).encode()
    with pytest.raises(ValueError, match="not configured"):
        _gitlab("").trigger_event(body, {"X-Gitlab-Event": "Merge Request Hook"})
    with pytest.raises(ValueError, match="Invalid"):
        _gitlab().trigger_event(
            body, {"X-Gitlab-Token": "wrong", "X-Gitlab-Event": "Merge Request Hook"}
        )


def _hook(kind: str, payload: dict[str, Any]) -> tuple[bytes, dict[str, str]]:
    body = {"project": {"path_with_namespace": "Group/App"}, "user": {"username": "ada"}, **payload}
    return json.dumps(body).encode(), {
        "X-Gitlab-Token": "s",
        "X-Gitlab-Event": kind,
        "X-Gitlab-Event-UUID": "u-1",
    }


def test_gitlab_merge_requests_and_pipelines_become_events() -> None:
    host = _gitlab()
    mr = _hook("Merge Request Hook", {"object_attributes": {"action": "open", "iid": 4, "source_branch": "feat",
              "target_branch": "main", "source_project_id": 1, "target_project_id": 1,
              "last_commit": {"id": "c1"}}})  # fmt: skip
    event = host.trigger_event(*mr)
    assert event is not None and (event.source, event.repository, event.number, event.trusted) == (
        "gitlab_mr",
        "group/app",
        4,
        True,
    )
    fork = _hook(
        "Merge Request Hook",
        {
            "object_attributes": {
                "action": "open",
                "iid": 5,
                "source_project_id": 2,
                "target_project_id": 1,
            }
        },
    )
    assert not host.trigger_event(*fork).trusted  # type: ignore[union-attr]
    pipeline = _hook("Pipeline Hook", {"object_attributes": {"status": "failed", "ref": "feat", "sha": "c1"},
                    "merge_request": {"iid": 4}})  # fmt: skip
    repo_event = host.repo_event(*pipeline)
    assert repo_event is not None and (
        repo_event.kind,
        repo_event.outcome,
        repo_event.pr_numbers,
    ) == ("check_suite.completed", "failure", (4,))


def test_a_merge_request_url_is_followed_like_a_pull_request() -> None:
    assert parse_pull_request_url(
        "Pull request opened: https://gitlab.example.com/group/sub/app/-/merge_requests/9"
    ) == ("group/sub/app", 9)


def test_the_sandbox_clones_gitlab_through_the_gateway() -> None:
    environment = sandbox_git_environment(
        "cap", "https://api.github.com", "https://gitlab.example.com"
    )
    values = [v for k, v in environment.items() if k.startswith("GIT_CONFIG_VALUE_")]
    assert "https://gitlab.example.com/" in values and "https://github.com/" in values
    assert parse_repository_path("group/sub/app.git", nested=True) == "group/sub/app"
    with pytest.raises(GatewayRefusal):
        parse_repository_path("group/sub/app.git")


# -- workspace snapshots ---------------------------------------------------------------------------


class _Store:
    def __init__(self, record: SessionRecord) -> None:
        self.record = record

    async def get_session(self, *_: Any, **__: Any) -> SessionRecord:
        return self.record

    async def mutate_session(self, _sid: str, _org: str, fn: Any) -> bool:
        fn(self.record)
        return True


def _record() -> SessionRecord:
    return SessionRecord(session_id="s1", tenant_org_id="org", tenant_user_id="u", title="t")


async def test_a_snapshot_restores_the_workspace() -> None:
    driver = FakeSandboxDriver(seed_files={"/workspace/a.txt": "one"})
    assert isinstance(driver, WorkspaceSnapshotter)
    await driver.start_sandbox("s1")
    await driver.write_file("s1", "/workspace/a.txt", "one")
    store = _Store(_record())
    snapshot = await workspace_snapshots.take_snapshot(driver, store, None, store.record, "before")  # type: ignore[arg-type]
    await driver.write_file("s1", "/workspace/a.txt", "two")
    await workspace_snapshots.restore_snapshot(driver, store.record, snapshot.id)
    assert await driver.read_file("s1", "/workspace/a.txt") == "one"


@pytest.mark.setting("sandboxes.workspace_snapshots_kept")
async def test_only_the_newest_snapshots_are_kept(monkeypatch: pytest.MonkeyPatch) -> None:
    driver = FakeSandboxDriver()
    store = _Store(_record())

    async def kept(*_: Any) -> dict[str, Any]:
        return {"workspace_snapshots_kept": 2}

    monkeypatch.setattr(workspace_snapshots, "effective_settings", kept)
    taken = [
        await workspace_snapshots.take_snapshot(driver, store, None, store.record, str(i))
        for i in range(3)
    ]  # type: ignore[arg-type]
    assert [s.id for s in workspace_snapshots.snapshots_of(store.record)] == [
        taken[1].id,
        taken[2].id,
    ]
    assert taken[0].location not in driver._snapshots


@pytest.mark.setting("sandboxes.snapshot_before_hibernate")
async def test_the_reaper_snapshots_before_pausing_when_the_organization_asks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    driver = FakeSandboxDriver()
    store = _Store(_record())
    settings = {"snapshot_before_hibernate": False, "workspace_snapshots_kept": 3}

    async def current(*_: Any) -> dict[str, Any]:
        return settings

    monkeypatch.setattr(workspace_snapshots, "effective_settings", current)
    hook = workspace_snapshots.before_pause_hook(driver, store, None)  # type: ignore[arg-type]
    await hook("s1")
    assert workspace_snapshots.snapshots_of(store.record) == []
    settings["snapshot_before_hibernate"] = True
    await hook("s1")
    assert len(workspace_snapshots.snapshots_of(store.record)) == 1


def test_a_snapshot_size_is_read_from_its_quantity() -> None:
    assert parse_storage_bytes("2Gi") == 2 * 1024**3
    assert parse_storage_bytes("") is None


# -- browser session ---------------------------------------------------------------------------


async def test_browser_actions_are_checked_and_screenshots_named_here() -> None:
    driver = FakeSandboxDriver()
    driver.script_command("rocket-browser-session", ExecResult(exit_code=0, stdout=json.dumps(
        {"results": [{"do": "navigate", "status": 200}, {"do": "screenshot", "screenshot": "/x.png"}],
         "url": "http://localhost:3000/"}), stderr="", duration_ms=1))  # fmt: skip
    out = await browser_tools.browser_session(
        driver,
        "s1",
        [
            {"do": "navigate", "url": "http://localhost:3000/"},
            {"do": "screenshot", "path": "/etc/passwd"},
        ],
    )
    command = driver.exec_calls[-1][1]
    assert "/etc/passwd" not in command and browser_tools.SCREENSHOT_DIR in command
    assert "Current page: http://localhost:3000/" in out
    assert (
        await browser_tools.browser_session(
            driver, "s1", [{"do": "navigate", "url": "file:///etc"}]
        )
    ).startswith("Error")
    too_many = [{"do": "wait"}] * (browser_tools.MAX_SESSION_ACTIONS + 1)
    assert (await browser_tools.browser_session(driver, "s1", too_many)).startswith("Error")


# -- repository skills and dev containers ----------------------------------------------------------


async def test_a_cloned_repository_offers_its_skills() -> None:
    driver = FakeSandboxDriver()
    await driver.start_sandbox("s1")
    await driver.write_file("s1", "/workspace/app/.rocket/skills/release/SKILL.md",
                            "---\nname: release\ndescription: Cut a release\n---\nRun make release.")  # fmt: skip
    driver.script_command(
        "find",
        ExecResult(
            exit_code=0,
            stdout="/workspace/app/.rocket/skills/release/SKILL.md\n",
            stderr="",
            duration_ms=1,
        ),
    )
    found = await repository_skills.discover(driver, "s1", "/workspace/app")
    assert found == {
        "release": {
            "path": "/workspace/app/.rocket/skills/release/SKILL.md",
            "description": "Cut a release",
        }
    }
    record = _record()
    repository_skills.remember(found)(record)
    assert await repository_skills.read_skill(driver, record, "release") == "Run make release."
    assert await repository_skills.read_skill(driver, record, "other") is None
    assert "release: Cut a release" in repository_skills.describe(found, "/workspace/app")


def test_a_dev_container_image_is_read() -> None:
    assert (
        devcontainer_image('{"image": "mcr.microsoft.com/devcontainers/go:1", // c\n}')
        == "mcr.microsoft.com/devcontainers/go:1"
    )
    assert devcontainer_image('{"build": {"dockerfile": "Dockerfile"}}') is None


# -- code embeddings ---------------------------------------------------------------------------------


async def test_an_unchanged_chunk_is_embedded_once(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    async def embed(text: str, model: str = "") -> list[float]:
        calls.append(text)
        return [1.0, 0.0]

    import api.services.code_index as code_index

    monkeypatch.setattr(code_index, "compute_embedding", embed)
    store = CodeEmbeddings()
    first = await store.embed("org", "m", ["a", "b"])
    second = await store.embed("org", "m", ["a", "b", "c"])
    assert first == [[1.0, 0.0], [1.0, 0.0]] and len(second) == 3
    assert calls == ["a", "b", "c"]
    await store.embed("org", "other-model", ["a"])
    assert calls[-1] == "a"


async def test_the_code_index_uses_the_organizations_model(monkeypatch: pytest.MonkeyPatch) -> None:
    import api.services.code_index as code_index

    used: list[str] = []

    async def embed(text: str, model: str = "") -> list[float]:
        used.append(model)
        return [0.5, 0.5]

    async def files(*_: Any, **__: Any) -> list[str]:
        return ["/workspace/a.py"]

    monkeypatch.setattr(code_index, "compute_embedding", embed)
    monkeypatch.setattr(code_index, "list_source_files", files)
    driver = SimpleNamespace(read_file=AsyncMock(return_value="def f():\n    return 1\n"))
    index = await build_code_index(driver, "s1", model="gemini/gemini-embedding-001")  # type: ignore[arg-type]
    await code_index.search_code_index(index, "f")
    assert set(used) == {"gemini/gemini-embedding-001"}


# -- knowledge imports ----------------------------------------------------------------------------


def test_a_pdf_becomes_text() -> None:
    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    buffer = io.BytesIO()
    writer.write(buffer)
    with pytest.raises(knowledge_import.KnowledgeImportError, match="no text"):
        knowledge_import.pdf_from_base64(base64.b64encode(buffer.getvalue()).decode(), "a.pdf")
    with pytest.raises(knowledge_import.KnowledgeImportError, match="base64"):
        knowledge_import.pdf_from_base64("not base64!", "a.pdf")


async def test_a_site_import_stays_under_its_address(monkeypatch: pytest.MonkeyPatch) -> None:
    pages = {
        "https://docs.example.com/guide/": b"<title>Guide</title><a href='a'>A</a><a href='/other'>O</a><script>x</script><p>Intro</p>",
        "https://docs.example.com/guide/a": b"<title>A</title><p>Page A</p>",
    }
    fetched: list[str] = []

    async def fetch(url: str) -> tuple[str, bytes]:
        fetched.append(url)
        return "text/html", pages[url]

    monkeypatch.setattr(knowledge_import, "_fetch", fetch)
    documents = await knowledge_import.import_site("https://docs.example.com/guide/")
    assert [d.title for d in documents] == ["Guide", "A"]
    assert "https://docs.example.com/other" not in fetched
    assert "x" not in documents[0].content and "Intro" in documents[0].content


# -- tracing ------------------------------------------------------------------------------------------


def test_a_model_call_span_carries_gen_ai_attributes_and_no_content() -> None:
    from llm_gateway import gateway
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    from specifications.interfaces.llm import ModelRequest, ModelScope

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("test")
    original = gateway._tracer
    gateway._tracer = tracer
    try:
        request = ModelRequest(model="anthropic/claude-sonnet-5-5", messages=[])
        span = gateway._model_call_span(
            request, ModelScope(org_id="org", user_id="u", agent_id="a1")
        )
        gateway._end_model_call_span(
            span, {"prompt_tokens": 10, "completion_tokens": 3}, "stop", 0.01, False, "org"
        )
    finally:
        gateway._tracer = original
    attributes = dict(exporter.get_finished_spans()[0].attributes or {})
    assert attributes["gen_ai.system"] == "anthropic"
    assert attributes["gen_ai.usage.input_tokens"] == 10 and attributes["rocket.agent_id"] == "a1"
    assert not any("content" in key or "prompt" in key for key in attributes)


# -- what a clone brings, by setting ---------------------------------------------------------------


async def _clone_extras(
    monkeypatch: pytest.MonkeyPatch, sandbox_cfg: dict[str, Any], general: dict[str, Any]
) -> tuple[list[str], SessionRecord, FakeSandboxDriver]:
    from api.turns import callbacks

    driver = FakeSandboxDriver()
    await driver.start_sandbox("s1")
    await driver.write_file(
        "s1", "/workspace/app/.devcontainer/devcontainer.json", '{"image": "devbox:go"}'
    )
    await driver.write_file("s1", "/workspace/app/.rocket/skills/lint/SKILL.md", "Lint everything.")
    driver.script_command(
        "find",
        ExecResult(
            exit_code=0,
            stdout="/workspace/app/.rocket/skills/lint/SKILL.md\n",
            stderr="",
            duration_ms=1,
        ),
    )
    offered = AsyncMock(return_value=True)
    monkeypatch.setattr(callbacks.SandboxCatalogue, "image_is_offered", offered)
    store = _Store(_record())
    notes = await callbacks.repository_extras(
        None, store, driver, "s1", "org", "/workspace/app", sandbox_cfg, general
    )
    return notes, store.record, driver


@pytest.mark.setting("sandboxes.use_devcontainer_image")
async def test_an_offered_dev_container_image_is_used_only_when_allowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    notes, record, _ = await _clone_extras(
        monkeypatch, {"use_devcontainer_image": True}, {"repository_skills": False}
    )
    assert record.container_image == "devbox:go" and "devbox:go" in notes[0]
    notes, record, _ = await _clone_extras(
        monkeypatch, {"use_devcontainer_image": False}, {"repository_skills": False}
    )
    assert record.container_image != "devbox:go" and notes == []


@pytest.mark.setting("general.repository_skills")
async def test_repository_skills_are_offered_only_when_allowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    notes, record, _ = await _clone_extras(
        monkeypatch, {"use_devcontainer_image": False}, {"repository_skills": True}
    )
    assert "lint" in (record.metadata or {}).get("repository_skills", {}) and any(
        "lint" in n for n in notes
    )
    notes, record, _ = await _clone_extras(
        monkeypatch, {"use_devcontainer_image": False}, {"repository_skills": False}
    )
    assert "repository_skills" not in (record.metadata or {}) and notes == []

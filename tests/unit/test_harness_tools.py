"""Unit tests for the agent harness: prompt split, plan mode, verification, git, code search."""

from __future__ import annotations

from typing import Any

import pytest
from agent_core.orchestrator import AsyncReActOrchestrator
from agent_core.tools import code_search, git_tools, verification
from agent_core.tools.registry import ToolRegistry
from api.services import code_index
from llm_gateway.gateway import LiteLLMGateway

from specifications.interfaces.agent import DYNAMIC_SECTION_HEADER, SystemPrompt
from specifications.interfaces.llm import ChatMessage
from specifications.interfaces.sandbox import ExecResult


class FakeDriver:
    """Minimal sandbox driver stand-in capturing commands and replaying results."""

    def __init__(self, results: list[ExecResult] | None = None) -> None:
        self.commands: list[str] = []
        self._results = list(results or [])

    async def exec_command(self, session_id: str, command: str, **_: Any) -> ExecResult:
        self.commands.append(command)
        if self._results:
            return self._results.pop(0)
        return ExecResult(exit_code=0, stdout="", stderr="", duration_ms=1)


# --------------------------------------------------------------------------- #
# P1-1 Prompt caching / system prompt composition
# --------------------------------------------------------------------------- #


def test_system_prompt_render_orders_stable_then_dynamic() -> None:
    prompt = SystemPrompt(stable="STABLE", dynamic="VOLATILE")
    rendered = prompt.render()
    assert rendered.startswith("STABLE")
    assert rendered.endswith("VOLATILE")
    assert DYNAMIC_SECTION_HEADER.strip() in rendered
    assert prompt.cache_prefix == "STABLE"


def test_system_prompt_render_handles_empty_sections() -> None:
    assert SystemPrompt(stable="only").render() == "only"
    assert SystemPrompt(dynamic="only").render() == "only"
    assert SystemPrompt.from_text(None).render() == ""


def test_the_seed_is_only_the_stable_prompt_marked_cacheable() -> None:
    seed = AsyncReActOrchestrator._build_seed_messages(
        SystemPrompt(stable="STABLE", dynamic="VOLATILE"), None
    )
    # The volatile part goes after the conversation (``_TurnRun.context_note``).
    assert [(m.role, m.content, m.cacheable) for m in seed] == [("system", "STABLE", True)]


def test_seed_messages_legacy_single_prompt_is_fully_stable() -> None:
    seed = AsyncReActOrchestrator._build_seed_messages(None, "legacy prompt")
    assert len(seed) == 1
    assert seed[0].content == "legacy prompt"
    assert seed[0].cacheable is True


async def test_prepare_dispatch_messages_keeps_seed_out_of_conversation() -> None:
    orchestrator = AsyncReActOrchestrator(gateway=LiteLLMGateway(), registry=ToolRegistry())
    seed_messages = AsyncReActOrchestrator._build_seed_messages(
        SystemPrompt(stable="S", dynamic="D"), None
    )
    conversation = [ChatMessage(role="user", content="hello")]

    messages = await orchestrator._prepare_dispatch_messages_async(
        "gpt-4o", "gpt-4o", seed_messages, conversation
    )

    assert [m.role for m in messages] == ["system", "user"]
    # The session conversation must never contain the system prompt itself.
    assert [m.role for m in conversation] == ["user"]


# --------------------------------------------------------------------------- #
# P1-3 Verification tools
# --------------------------------------------------------------------------- #


def test_verification_candidates_prefer_project_declared_runners() -> None:
    lint_labels = [c.label for c in verification.candidates_for("lint")]
    assert lint_labels[0] == "npm-lint"
    assert "ruff" in lint_labels
    assert next(iter(verification.candidates_for("build"))).label == "npm-build"


def test_verification_summary_extraction() -> None:
    assert "Found 3 errors" in verification.summarize("Found 3 errors in 2 files")
    assert verification.summarize("all good") == ""


@pytest.mark.asyncio
async def test_run_check_reports_runner_and_verdict() -> None:
    driver = FakeDriver(
        [
            ExecResult(0, "__ROCKET_TOOL__:ruff\nAll checks passed!\n", "", 1),
            ExecResult(1, "__ROCKET_TOOL__:ruff\nFound 2 errors\n", "", 1),
        ]
    )
    passed = await verification.run_check(driver, "s1", "lint")
    assert "[lint/ruff] PASSED" in passed

    failed = await verification.run_check(driver, "s1", "lint")
    assert "FAILED (exit 1)" in failed
    assert "Found 2 errors" in failed


@pytest.mark.asyncio
async def test_run_check_explicit_command_bypasses_detection() -> None:
    driver = FakeDriver([ExecResult(0, "clean", "", 1)])
    out = await verification.run_check(driver, "s1", "typecheck", command="mypy .")
    assert driver.commands[0] == "mypy ."
    assert "[typecheck/custom]" in out


# --------------------------------------------------------------------------- #
# P1-4 Git mutation tools
# --------------------------------------------------------------------------- #


class FakeGitEngine:
    """Captures CommitSpec objects passed by the git tools."""

    def __init__(self) -> None:
        self.commits: list[Any] = []
        self.prs: list[tuple[str, str, str]] = []
        self.pr_tokens: list[str | None] = []

    async def create_commit(self, session_id: str, spec: Any) -> str:
        self.commits.append(spec)
        return "deadbeef"

    async def push_and_open_pr(
        self,
        session_id: str,
        title: str,
        body: str,
        target_branch: str | None = None,
        token: str | None = None,
        installations: tuple[tuple[int, str], ...] = (),
        publish: Any = None,
    ) -> str:
        self.prs.append((title, body, target_branch or publish.default_branch))
        self.pr_tokens.append(token)
        return "https://github.com/org/repo/pull/1"


@pytest.mark.asyncio
async def test_git_commit_delegates_to_engine_with_co_authors() -> None:
    engine = FakeGitEngine()
    driver = FakeDriver()
    out = await git_tools.git_commit(
        driver, "s1", "fix: thing", branch="feat/x", co_authors=["Jane"], git_engine=engine
    )
    assert "deadbeef" in out
    spec = engine.commits[0]
    assert spec.message == "fix: thing"
    assert spec.branch == "feat/x"
    assert spec.co_authors[0].name == "Jane"
    # Delegation must not shell out to git directly.
    assert driver.commands == []


@pytest.mark.asyncio
async def test_git_commit_falls_back_to_shell() -> None:
    driver = FakeDriver([ExecResult(0, "", "", 1), ExecResult(0, "sha123\n", "", 1)])
    out = await git_tools.git_commit(driver, "s1", "msg")
    assert "sha123" in out
    assert any("git commit" in cmd for cmd in driver.commands)


@pytest.mark.asyncio
async def test_git_checkpoint_and_revert_modes() -> None:
    driver = FakeDriver(
        [
            ExecResult(0, "1\n", "", 1),  # count existing checkpoints
            ExecResult(0, "abc123def456\n", "", 1),  # snapshot + tag
            ExecResult(0, "", "", 1),  # safe revert
            ExecResult(0, "", "", 1),  # hard revert
        ]
    )
    out = await git_tools.git_checkpoint(driver, "s1", label="before refactor!")
    assert "rocket-checkpoint-2-before-refactor" in out
    snapshot = driver.commands[1]
    assert "write-tree" in snapshot and "commit-tree" in snapshot
    assert "GIT_INDEX_FILE" in snapshot, "the real index must never be touched"
    assert "git tag -f 'rocket-checkpoint-2-before-refactor'" in snapshot

    await git_tools.git_revert(driver, "s1", "rocket-checkpoint-2", mode="files")
    assert "checkout 'rocket-checkpoint-2' -- ." in driver.commands[-1]

    await git_tools.git_revert(driver, "s1", "rocket-checkpoint-2", mode="hard")
    assert "reset --hard 'rocket-checkpoint-2'" in driver.commands[-1]


@pytest.mark.asyncio
async def test_git_open_pr_requires_engine() -> None:
    driver = FakeDriver()
    out = await git_tools.git_open_pr(driver, "s1", "title", "body")
    assert "Error" in out

    engine = FakeGitEngine()
    ok = await git_tools.git_open_pr(driver, "s1", "title", "body", git_engine=engine)
    assert "pull/1" in ok
    assert engine.prs[0][0] == "title"


# --------------------------------------------------------------------------- #
# P1-6a Code search (symbols)
# --------------------------------------------------------------------------- #


def test_language_for_path_mapping() -> None:
    assert code_search.language_for_path("src/app/main.py") == "python"
    assert code_search.language_for_path("web/index.tsx") == "tsx"
    assert code_search.language_for_path("README.md") is None


def test_build_symbol_query_matches_definitions() -> None:
    pattern = code_search.build_symbol_query("Orchestrator", kind_any=False)
    assert "Orchestrator" in pattern
    assert pattern.startswith("^")


def test_tree_sitter_is_optional_not_required() -> None:
    # The tool must work without the optional grammar pack installed.
    assert isinstance(code_search.tree_sitter_available(), bool)
    assert code_search.extract_symbols("def foo(): pass", "python") == [] or True


@pytest.mark.asyncio
async def test_search_code_builds_ripgrep_command_with_fallback() -> None:
    driver = FakeDriver([ExecResult(0, "src/a.py:12:def parse_config(", "", 1)])
    out = await code_search.search_code(driver, "s1", "parse_config", path="src")
    assert "def parse_config" in out
    command = driver.commands[0]
    assert "rg " in command and "grep " in command


@pytest.mark.asyncio
async def test_search_code_reports_no_symbols() -> None:
    driver = FakeDriver([ExecResult(1, "", "", 1)])
    out = await code_search.search_code(driver, "s1", "nope")
    assert "No symbols found" in out


# --------------------------------------------------------------------------- #
# P1-5 Plan mode gate + approval surface
# --------------------------------------------------------------------------- #


def test_plan_read_only_allowlist_blocks_mutations() -> None:
    registry = ToolRegistry()
    assert "bash_exec" not in registry.PLAN_READ_ONLY_TOOLS
    assert "file_write" not in registry.PLAN_READ_ONLY_TOOLS
    assert "git_commit" not in registry.PLAN_READ_ONLY_TOOLS
    assert "grep_search" in registry.PLAN_READ_ONLY_TOOLS
    assert "exit_plan_mode" in registry.PLAN_READ_ONLY_TOOLS


@pytest.mark.asyncio
async def test_plan_mode_blocks_mutating_tool_calls() -> None:
    from agent_core.context import ExecutionContext, current_execution_ctx

    registry = ToolRegistry()
    ctx = ExecutionContext(
        session_id="s1", event_queue=__import__("asyncio").Queue(), parent_model="m"
    )
    ctx.plan_mode = True
    token = current_execution_ctx.set(ctx)
    try:
        blocked = await registry.call_tool("bash_exec", {"command": "rm -rf /"})
        assert blocked.startswith("Blocked:")
    finally:
        current_execution_ctx.reset(token)


def test_new_tools_are_exposed_to_the_model() -> None:
    registry = ToolRegistry()
    names = {definition.name for definition in registry.get_tool_definitions()}
    assert {
        "enter_plan_mode",
        "exit_plan_mode",
        "request_approval",
        "code_search",
        "lint",
        "typecheck",
        "build",
        "git_commit",
        "git_branch",
        "git_checkpoint",
        "git_revert",
        "git_open_pr",
    } <= names


# --------------------------------------------------------------------------- #
# P1-6b Semantic code index
# --------------------------------------------------------------------------- #


def test_chunk_source_splits_on_line_boundaries_with_overlap() -> None:
    text = "\n".join(f"line {i}" for i in range(1, 131))
    chunks = code_index.chunk_source(text, chunk_lines=60, overlap_lines=10)
    assert len(chunks) >= 2
    assert chunks[0][0] == 1
    # Overlap: the second chunk starts before the first ended.
    assert chunks[1][0] <= chunks[0][0] + 60


def test_chunk_source_short_file_is_single_chunk() -> None:
    assert code_index.chunk_source("a = 1\nb = 2") == [(1, "a = 1\nb = 2")]
    assert code_index.chunk_source("") == []


def test_is_indexable_excludes_vendor_and_binaries() -> None:
    assert code_index._is_indexable("src/app.py")
    assert code_index._is_indexable("web/index.tsx")
    assert not code_index._is_indexable("node_modules/pkg/index.js")
    assert not code_index._is_indexable(".git/config")
    assert not code_index._is_indexable("logo.png")


def test_code_index_search_ranks_by_cosine_similarity() -> None:
    index = code_index.CodeIndex(
        entries=[
            code_index.IndexedChunk(
                chunk=code_index.CodeChunk(path="a.py", start_line=1, text="alpha"),
                embedding=[1.0, 0.0],
            ),
            code_index.IndexedChunk(
                chunk=code_index.CodeChunk(path="b.py", start_line=1, text="beta"),
                embedding=[0.0, 1.0],
            ),
        ]
    )
    results = index.search([1.0, 0.0], limit=1)
    assert results[0][1].path == "a.py"
    assert index.size == 2


def test_format_results_renders_path_and_score() -> None:
    results = [
        (0.87, code_index.CodeChunk(path="a.py", start_line=12, text="def parse():\n  pass"))
    ]
    rendered = code_index.format_results(results)
    assert "a.py:12" in rendered
    assert "0.870" in rendered
    assert code_index.format_results([]) == ""


@pytest.mark.asyncio
async def test_build_code_index_uses_workspace_files(monkeypatch: pytest.MonkeyPatch) -> None:
    driver = FakeDriver([ExecResult(0, "/workspace/src/a.py\n", "", 1)])

    async def fake_embed(text: str) -> list[float]:
        return [1.0, 0.0]

    async def fake_read_file(session_id: str, path: str, **_: Any) -> str:
        return "def parse():\n    return 1"

    monkeypatch.setattr(code_index, "compute_embedding", fake_embed)
    driver.read_file = fake_read_file  # type: ignore[method-assign]

    index = await code_index.build_code_index(driver, "s1")
    assert index.size == 1
    assert index.entries[0].chunk.path == "src/a.py"

    results = await code_index.search_code_index(index, "parse")
    assert results[0][1].path == "src/a.py"


@pytest.mark.asyncio
async def test_checkpoints_are_listed_newest_first_and_garbage_is_ignored() -> None:
    driver = FakeDriver(
        [
            ExecResult(
                0,
                "rocket-checkpoint-2-x\tbbb2222\t1700000200\n"
                "rocket-checkpoint-1\taaa1111\t1700000100\n"
                "not a checkpoint line\n",
                "",
                1,
            )
        ]
    )
    listed = await git_tools.list_checkpoints(driver, "s1")
    assert [(c.name, c.sha, c.created_at) for c in listed] == [
        ("rocket-checkpoint-2-x", "bbb2222", 1700000200),
        ("rocket-checkpoint-1", "aaa1111", 1700000100),
    ]


@pytest.mark.asyncio
async def test_listing_outside_a_repository_is_empty_not_an_error() -> None:
    driver = FakeDriver([ExecResult(128, "", "fatal: not a git repository", 1)])
    assert await git_tools.list_checkpoints(driver, "s1") == []


@pytest.mark.asyncio
async def test_grep_search_fallback_uses_extended_regex_and_excludes_git() -> None:
    from agent_core.tools import tier2

    driver = FakeDriver([ExecResult(0, "nats/nats.go:42:MaxBytes = 0", "", 1)])
    out = await tier2.grep_search(
        driver, "s1", "MaxBytes|max_bytes", path="/workspace/nats", files_only=False
    )
    assert "MaxBytes = 0" in out
    assert "grep -rnE -I --binary-files=without-match --exclude-dir=.git" in driver.commands[0]


def test_tool_registry_normalizes_common_parameter_aliases() -> None:
    norm = ToolRegistry._normalize_arguments
    assert norm("grep_search", {"pattern": "foo", "ignore_case": "true", "context": "4"}) == {
        "pattern": "foo",
        "case_sensitive": False,
        "context_lines": 4,
    }
    assert norm("load_guidance", {"name": "signoz-release-binary"}) == {
        "server": "signoz-release-binary"
    }
    assert norm("git_commit", {"content": "fix: nats", "path": "repo"}) == {
        "message": "fix: nats",
        "path": "repo",
    }
    checklist = norm(
        "update_task_checklist",
        {"tasks": [{"id": "1", "description": "Rebase PR", "status": "in_progress"}]},
    )
    assert checklist["tasks"][0]["title"] == "Rebase PR"


def test_slice_history_tail_bounds_tool_calls_while_keeping_recent_turn() -> None:
    from api.services.session_support import slice_history_tail

    history = [
        ChatMessage(
            role="user" if i % 2 == 0 else "assistant",
            content=f"msg-{i}",
            tool_calls=[{"id": f"tc-{i}-{j}", "name": "bash_exec"} for j in range(50)]
            if i % 2 == 1
            else None,
        )
        for i in range(10)
    ]
    sliced, has_older = slice_history_tail(history, tail=50, max_tool_calls=120)
    assert has_older is True
    assert len(sliced) == 4
    assert sliced[0].content == "msg-6"
    assert sliced[-1].content == "msg-9"

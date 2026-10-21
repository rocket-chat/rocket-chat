"""What the agent is told about its environment, and that it stays cacheable.

``tests/golden/system_prompt_git.json`` holds the reviewed git guidance for every credential
and signing mode. Regenerate with ``UPDATE_PROMPT_SNAPSHOTS=1 uv run pytest
tests/unit/test_system_prompt.py``.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from api.services.git_credentials import GitCredentialBundle
from api.store import InMemorySessionStore
from api.turns import prompt
from sandbox_driver.fake_driver import FakeSandboxDriver

from specifications.interfaces.agent import GitPolicy, ToolLoading
from specifications.interfaces.sandbox import SandboxStatus
from specifications.interfaces.session import SessionRecord
from tests.turn_harness import run_turn

SNAPSHOT = Path(__file__).parents[1] / "golden" / "system_prompt_git.json"
DEFAULT_PROMPT_SNAPSHOT = Path(__file__).parents[1] / "golden" / "system_prompt_default.txt"
CREDENTIAL_MODES = ("app", "member", "public")
SIGNING_MODES = ("github_app", "gpg_key", "none")


def _bundle(mode: str) -> GitCredentialBundle:
    return GitCredentialBundle(
        token=None, mode=mode, author_name="Ada", author_email="ada@example.com"
    )


def _guidance() -> dict[str, str]:
    return {
        f"{mode}/{signing}": "\n".join(
            prompt.git_guidance(_bundle(mode), GitPolicy(signing_mode=signing))
        )
        for mode in CREDENTIAL_MODES
        for signing in SIGNING_MODES
    }


def test_the_git_guidance_matches_the_reviewed_snapshot() -> None:
    current = _guidance()
    if os.getenv("UPDATE_PROMPT_SNAPSHOTS"):
        SNAPSHOT.write_text(json.dumps(current, indent=2, sort_keys=True) + "\n")
    assert current == json.loads(SNAPSHOT.read_text())


async def test_the_default_agents_whole_prompt_matches_the_reviewed_snapshot() -> None:
    """Every change to what the agent is told shows up in review as a change to this file."""
    current = await _stable("org_prompt_snapshot")
    if os.getenv("UPDATE_PROMPT_SNAPSHOTS"):
        DEFAULT_PROMPT_SNAPSHOT.write_text(current + "\n")
    assert current + "\n" == DEFAULT_PROMPT_SNAPSHOT.read_text()


def test_the_git_guidance_is_true_in_every_mode() -> None:
    for key, text in _guidance().items():
        assert "`git push` and the `gh` CLI do not work here" in text, key
        assert "Never commit to `main`" in text and "github_pull_request" in text, key
    assert "Verified" in _guidance()["app/github_app"]
    assert "The member's key signs" in _guidance()["member/gpg_key"]
    assert "unsigned" in _guidance()["public/none"]
    assert "Private repositories need" in _guidance()["public/none"]


async def _stable(org: str, metadata: dict[str, Any] | None = None) -> str:
    outcome = await run_turn(org, metadata=metadata, driver=FakeSandboxDriver())
    first = outcome.model.requests[0].messages[0]
    assert first.role == "system"
    return str(first.content)


async def test_the_stable_prompt_carries_no_per_turn_content() -> None:
    first = await _stable("org_prompt_a")
    second = await _stable("org_prompt_b", {"selected_model": "openai/gpt-4o"})

    assert first == second, "nothing turn- or model-specific in the cached prefix"
    for volatile in ("Active model", "routing mode", "Background processes"):
        assert volatile not in first
    assert prompt.DEFAULT_AGENT_PROMPT in first

    assert "[Workspace]" in first and "[Editing and verifying]" in first
    assert "[Long-running tasks and git hygiene]" in first
    assert "Never leave a rebase" in first
    assert "`.rocket/PLAN.md`" in first and "never re-send an" in first
    # Size budget assertion on stable platform prompt to prevent unbounded prompt inflation
    assert 2_000 < len(first) < 10_000


def _ctx(store: InMemorySessionStore, record: SessionRecord, driver: Any) -> Any:
    return SimpleNamespace(
        req=SimpleNamespace(driver=driver, store=store), session=record, session_id="s1"
    )


async def test_the_repositorys_instructions_survive_a_sleeping_sandbox() -> None:
    store, driver = InMemorySessionStore(), FakeSandboxDriver()
    record = SessionRecord(
        session_id="s1", tenant_org_id="o", tenant_user_id="u", title="t",
        metadata={"active_repository": "/workspace/api"}, sandbox_status=SandboxStatus.RUNNING,
    )  # fmt: skip
    await store.create_session(record)
    await driver.write_file("s1", "/workspace/api/AGENTS.md", "Run make check.")

    awake = await prompt._project_instructions(_ctx(store, record, driver))
    record.sandbox_status = SandboxStatus.HIBERNATED
    asleep = await prompt._project_instructions(_ctx(store, record, driver))

    assert awake and "Run make check." in awake
    assert asleep == awake
    record.metadata = {**record.metadata, "active_repository": "/workspace/web"}
    assert await prompt._project_instructions(_ctx(store, record, driver)) is None


async def test_repository_instructions_ride_with_the_turn_not_the_cached_prefix() -> None:
    """Switching the active repository must not change the cached prefix of the next turn."""
    section = "[Project Instructions - from `/workspace/api/AGENTS.md`]\nRun make check."
    metadata = {
        "active_repository": "/workspace/api",
        prompt.PROJECT_INSTRUCTIONS_KEY: {"repository": "/workspace/api", "text": section},
    }

    outcome = await run_turn("org_prompt_instr", metadata=metadata, driver=FakeSandboxDriver())

    messages = outcome.model.requests[0].messages
    assert "Run make check." not in str(messages[0].content)
    assert any("Run make check." in (m.content or "") for m in messages[1:] if m.role == "user")
    assert str(messages[0].content) == await _stable("org_prompt_instr_none")


READ_ONLY = prompt.PromptTools(allowed=frozenset({"file_read", "grep_search", "web_search"}))


def _guidance_text(tools: prompt.PromptTools) -> str:
    return "\n".join(
        [
            *prompt.sandbox_guidance(tools),
            *prompt.workspace_guidance(tools),
            *prompt.editing_guidance(tools),
            prompt.working_style_guidance(tools),
            prompt.long_task_guidance(tools),
            prompt.showing_work_guidance(tools),
        ]
    )


def test_a_read_only_agent_is_not_told_to_edit_run_or_publish() -> None:
    text = _guidance_text(READ_ONLY)

    for tool in ("bash_exec", "file_edit", "run_tests", "process_start", "create_artifact"):
        assert tool not in text, tool
    assert "web search" in text


def test_a_tool_the_turn_denies_is_not_recommended() -> None:
    everything = prompt.PromptTools(active=None)
    no_artifacts = prompt.PromptTools(active=None, denied=frozenset({"create_artifact", "env_set"}))

    assert "create_artifact" in _guidance_text(everything)
    assert "create_artifact" not in _guidance_text(no_artifacts)
    assert "env_set" not in _guidance_text(no_artifacts)


def test_tool_patterns_in_an_agents_list_allow_matching_tools() -> None:
    tools = prompt.PromptTools(allowed=frozenset({"file_*"}))

    assert tools.can("file_read") and not tools.can("bash_exec")
    assert tools.named("file_read", "bash_exec") == "`file_read`"


def test_verification_checkpoint_and_delegation_are_each_said_once() -> None:
    text = _guidance_text(prompt.PromptTools(active=None))

    assert text.count("Verify what you changed") == 1
    assert text.count("git_checkpoint") == 1
    assert text.count("delegate_subagent") == 0, "delegation has its own section"
    assert "Creator, noted" not in text


async def test_a_denied_publishing_tool_is_explained_not_recommended() -> None:
    ctx = SimpleNamespace(
        req=SimpleNamespace(
            overlay=SimpleNamespace(
                tool_scope=None,
                denied_tools=("git_push", "git_open_pr"),
                stable_suffix="",
                dynamic="",
            )
        ),
        binding=SimpleNamespace(
            whitelisted_tools=["*"], policy=SimpleNamespace(tool_loading=ToolLoading())
        ),
    )

    tools = prompt.prompt_tools(ctx)  # type: ignore[arg-type]

    assert not tools.has(*prompt.PUBLISH_TOOLS)
    assert tools.has("bash_exec")


def test_guidance_about_a_deferred_tool_arrives_with_the_tool_not_the_prompt() -> None:
    core_only = _guidance_text(prompt.PromptTools())
    kept = _guidance_text(prompt.PromptTools(active=("process_*",)))

    for deferred in ("env_set", "create_artifact", "git_checkpoint", "process_start"):
        assert deferred not in core_only, deferred
    assert "process_start" in kept, "a tool the agent keeps active keeps its guidance"
    assert "steer the current task" in core_only
    assert "run them as written" in core_only


def test_the_model_notice_is_plain_and_names_no_internals() -> None:
    import inspect

    source = inspect.getsource(prompt.build_system_prompt)
    assert "volatile; not cacheable" not in source
    assert "routing mode" not in source

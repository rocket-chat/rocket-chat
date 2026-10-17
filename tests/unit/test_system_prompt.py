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

from specifications.interfaces.agent import GitPolicy
from specifications.interfaces.sandbox import SandboxStatus
from specifications.interfaces.session import SessionRecord
from tests.turn_harness import run_turn

SNAPSHOT = Path(__file__).parents[1] / "golden" / "system_prompt_git.json"
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
    assert "never leave a rebase" in first
    assert "maintain `.rocket/PLAN.md`" in first
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

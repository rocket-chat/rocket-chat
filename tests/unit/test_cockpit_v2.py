"""Cockpit v2 on the server: plan decisions, remembered approvals, answer feedback, the Git tab,
per-hunk undo, public links that show the work redacted, and Setup health."""

from __future__ import annotations

import time
from types import SimpleNamespace
from typing import Any

import pytest
from api.services import plan_approval
from api.services.answer_feedback import (
    FeedbackError,
    FeedbackStore,
    reset_memory_feedback,
    turn_excerpts,
)
from api.services.approval_rules import allowed_scopes
from api.services.session_git import branch_state
from api.services.settings import save_org_config
from api.services.setup_health import setup_health
from api.services.sharing import public_artifacts, public_transcript
from api.services.workspace_changes import file_hunks, revert_hunk
from api.store import InMemorySessionStore

from specifications.interfaces.llm import ChatMessage
from specifications.interfaces.sandbox import ExecResult
from specifications.interfaces.session import SessionRecord


def _record(history: list[ChatMessage], org: str = "cockpit_org") -> SessionRecord:
    return SessionRecord(
        session_id=f"s_{org}",
        tenant_org_id=org,
        tenant_user_id="owner",
        title="t",
        conversation_history=history,
    )


# --- plans -------------------------------------------------------------------------------


class _Gates:
    def __init__(self, waiting: bool = True) -> None:
        self.waiting = waiting
        self.released: list[tuple[str, bool]] = []

    async def submit_human_approval(
        self, *, session_id: str, action_id: str, approved: bool
    ) -> None:
        if not self.waiting:
            raise KeyError(action_id)
        self.released.append((action_id, approved))


class _Bus:
    def __init__(self) -> None:
        self.events: list[Any] = []

    async def publish(self, event: Any) -> None:
        self.events.append(event)


async def _decide(
    choice: plan_approval.PlanDecisionInput, *, owner: bool = True, waiting: bool = True
) -> tuple[Any, Any, Any]:
    store, bus, gates = InMemorySessionStore(), _Bus(), _Gates(waiting)
    record = _record([])
    await store.create_session(record)
    decider = plan_approval.PlanDecider(store, gates, bus, None, "owner", may_change_mode=owner)
    decision = await plan_approval.decide_plan(decider, record, choice)
    return decision, await store.get_session(record.session_id), (bus, gates)


async def test_an_approved_plan_continues_in_the_chosen_mode_with_the_edited_checklist() -> None:
    choice = plan_approval.PlanDecisionInput(
        action_id="ap_1",
        approved=True,
        permission_mode="auto_edit",
        checklist=(plan_approval.PlanTaskEdit(id="1", title="Only this"),),
        feedback="Keep it small",
    )
    decision, stored, (bus, gates) = await _decide(choice)

    assert gates.released == [("ap_1", True)]
    assert decision.permission_mode == "auto_edit"
    assert stored is not None and stored.metadata.get("permission_mode") == "auto_edit"
    read = plan_approval.plan_decision_of(stored.metadata, since=time.time() - 60)
    assert read is not None and [t.title for t in read.checklist] == ["Only this"]
    kinds = [e.event_type for e in bus.events]
    assert "plan_updated" in kinds and "plan_decided" in kinds


async def test_only_the_owner_or_an_administrator_approves_but_anyone_may_reject() -> None:
    approve = plan_approval.PlanDecisionInput(
        action_id="ap_2", approved=True, permission_mode="auto"
    )
    with pytest.raises(plan_approval.PlanDecisionError) as refused:
        await _decide(approve, owner=False)
    assert refused.value.status == 403

    decision, _, _ = await _decide(
        plan_approval.PlanDecisionInput(action_id="ap_2", approved=False), owner=False
    )
    assert decision.approved is False and decision.permission_mode == ""


async def test_a_decision_for_a_plan_no_longer_waiting_is_refused_and_forgotten() -> None:
    choice = plan_approval.PlanDecisionInput(action_id="gone", approved=True, permission_mode="ask")
    store, bus, gates = InMemorySessionStore(), _Bus(), _Gates(waiting=False)
    record = _record([])
    await store.create_session(record)
    decider = plan_approval.PlanDecider(store, gates, bus, None, "owner", may_change_mode=True)
    with pytest.raises(plan_approval.PlanDecisionError) as err:
        await plan_approval.decide_plan(decider, record, choice)
    assert err.value.status == 404
    stored = await store.get_session(record.session_id)
    assert stored is not None and plan_approval.METADATA_KEY not in stored.metadata


# --- remembered approvals ----------------------------------------------------------------


@pytest.mark.setting("governance.remembered_approvals")
async def test_remembered_approvals_follow_the_organization_and_its_lock() -> None:
    org = "cockpit_remember"
    assert await allowed_scopes(None, org) == ("session", "repository")
    await save_org_config(None, org, "governance", {"remembered_approvals": "session"}, [])
    assert await allowed_scopes(None, org) == ("session",)
    await save_org_config(None, org, "governance", {"remembered_approvals": "off"}, [])
    assert await allowed_scopes(None, org) == ()
    await save_org_config(
        None, org, "governance", {"remembered_approvals": "session_and_repository"}, []
    )
    await save_org_config(
        None, org, "user_preferences", {"permission_mode": "ask"}, ["permission_mode"]
    )
    assert await allowed_scopes(None, org) == (), "a locked mode is not loosened by a member"


# --- answer feedback ---------------------------------------------------------------------


def test_the_rated_turn_is_the_nth_message_the_person_sent_and_its_answer() -> None:
    history = [
        ChatMessage(role="user", content="first"),
        ChatMessage(role="assistant", content="one"),
        ChatMessage(role="user", content="second"),
        ChatMessage(
            role="assistant",
            content="",
            tool_calls=[{"id": "c", "function": {"name": "x", "arguments": "{}"}}],
        ),
        ChatMessage(role="tool", content="r", tool_call_id="c"),
        ChatMessage(role="assistant", content="two"),
    ]
    assert turn_excerpts(history, 2) == ("second", "two")
    with pytest.raises(FeedbackError):
        turn_excerpts(history, 3)


async def test_a_rating_is_kept_per_person_and_turn_and_exported() -> None:
    reset_memory_feedback()
    store = FeedbackStore(None)
    record = _record(
        [ChatMessage(role="user", content="q"), ChatMessage(role="assistant", content="a")]
    )
    await store.rate(record, "u1", 1, "up", "")
    await store.rate(record, "u1", 1, "down", "wrong file")
    await store.rate(record, "u2", 1, "up", "")

    mine = await store.mine(record.tenant_org_id, record.session_id, "u1")
    assert [(f.rating, f.comment) for f in mine] == [("down", "wrong file")]
    exported = await store.export(record.tenant_org_id)
    assert {f.user_id for f in exported} == {"u1", "u2"}
    assert await store.export("another_org") == []
    reset_memory_feedback()


# --- the Git tab and per-hunk undo -------------------------------------------------------


class _ScriptedDriver:
    """Answers the git commands the Git tab and the hunk undo run."""

    def __init__(self, answers: dict[str, str]) -> None:
        self.answers = answers
        self.commands: list[str] = []
        self.files: dict[str, str] = {}

    async def exec_command(self, session_id: str, command: str, **_: Any) -> ExecResult:
        self.commands.append(command)
        for needle, out in self.answers.items():
            if needle in command:
                return ExecResult(0, out, "", 1)
        return ExecResult(0, "", "", 1)

    async def write_file(self, session_id: str, path: str, content: str, **_: Any) -> None:
        self.files[path] = content


async def test_the_git_tab_reads_the_branch_against_its_base() -> None:
    driver = _ScriptedDriver(
        {
            "rev-parse --abbrev-ref HEAD": "rocket/fix-greet\n",
            "symbolic-ref --short refs/remotes/origin/HEAD": "origin/main\n",
            "rev-list --left-right --count": "1\t2\n",
            "log --format": "abc\x1fAda\x1f1700000000\x1fFix greet\n",
            "status --porcelain": " M src/app.py\n?? new.txt\n",
        }
    )
    state = await branch_state(driver, "s", "/workspace/api", "acme/api")  # type: ignore[arg-type]
    assert (state.branch, state.base, state.ahead, state.behind, state.uncommitted) == (
        "rocket/fix-greet", "main", 2, 1, 2,
    )  # fmt: skip
    assert state.commits[0].subject == "Fix greet"


async def test_one_hunk_is_undone_with_the_files_header() -> None:
    diff = (
        "diff --git a/src/app.py b/src/app.py\n--- a/src/app.py\n+++ b/src/app.py\n"
        "@@ -1,2 +1,2 @@\n-a\n+b\n@@ -10,2 +10,3 @@\n c\n+d\n"
    )
    driver = _ScriptedDriver(
        {
            "git rev-parse -q --verify": "",
            "git diff --no-color": diff,
            "rev-parse --show-toplevel": "/workspace/api",
        }
    )
    hunks = await file_hunks(driver, "s", "/workspace/api", "turn", "src/app.py")  # type: ignore[arg-type]
    assert [(h.added, h.removed) for h in hunks] == [(1, 1), (1, 0)]

    await revert_hunk(driver, "s", "/workspace/api", "turn", "src/app.py", 1)  # type: ignore[arg-type]
    patch = next(iter(driver.files.values()))
    assert (
        patch.startswith("diff --git") and "@@ -10,2 +10,3 @@" in patch and "@@ -1,2" not in patch
    )
    assert any("git apply -R" in c for c in driver.commands)


# --- public links ------------------------------------------------------------------------


def test_a_public_link_shows_the_steps_redacted_and_bounded() -> None:
    history = [
        ChatMessage(role="user", content="deploy with key sk-secret"),
        ChatMessage(
            role="assistant",
            content="",
            tool_calls=[
                {
                    "id": "c1",
                    "function": {"name": "bash_exec", "arguments": '{"command": "echo sk-secret"}'},
                }
            ],
        ),
        ChatMessage(role="tool", content="x" * 10_000, tool_call_id="c1"),
        ChatMessage(role="assistant", content="Deployed.", reasoning="private thoughts"),
    ]
    redact = lambda text: text.replace("sk-secret", "[REDACTED]")  # noqa: E731
    shown = public_transcript(_record(history), redact)

    assert [m["role"] for m in shown] == ["user", "assistant"]
    assert "[REDACTED]" in shown[0]["content"]
    step = shown[1]["steps"][0]
    assert step["name"] == "bash_exec" and "sk-secret" not in step["arguments"]
    assert "result" not in step and "xxxx" not in str(shown), "a step's output is never public"
    assert "private thoughts" not in str(shown)

    artifact = SimpleNamespace(
        title="Report", filename="r.md", artifact_type="markdown", content="sk-secret"
    )
    assert public_artifacts([artifact], redact)[0]["content"] == "[REDACTED]"


# --- setup health ------------------------------------------------------------------------


async def test_setup_health_says_what_is_missing_and_where_to_fix_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in (
        "ANTHROPIC_API_KEY",
        "OPENROUTER_API_KEY",
        "OPENAI_API_KEY",
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
        "DEEPSEEK_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("ROCKET_SEARCH_PROVIDER", raising=False)
    checks = {
        c.id: c
        for c in await setup_health(None, "cockpit_health", None, "anthropic/claude-sonnet-5-5")
    }

    assert (
        checks["models"].status == "error" and checks["models"].fix_href == "/settings/org/models"
    )
    assert checks["embeddings"].status == "warning"
    assert checks["search"].status == "warning"
    assert checks["sandbox_images"].status == "unknown"

    monkeypatch.setenv("ANTHROPIC_API_KEY", "a")
    monkeypatch.setenv("ROCKET_SEARCH_PROVIDER", "tavily")
    checks = {
        c.id: c
        for c in await setup_health(None, "cockpit_health", None, "anthropic/claude-sonnet-5-5")
    }
    assert checks["models"].status == "ok" and checks["search"].status == "ok"

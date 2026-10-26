"""Unit tests for the plan -> execute -> verify gate (Item E1)."""

from __future__ import annotations

from agent_core.checklist import merge_verified_answers, verification_gate_notice

from specifications.interfaces.llm import ChatMessage


def test_verification_gate_triggers_when_files_edited_without_verification() -> None:
    # Scenario: Agent wrote a file, updated checklist marking a task completed, but didn't verify.
    conversation = [
        ChatMessage(role="user", content="Fix the auth vulnerability"),
        ChatMessage(
            role="assistant",
            content="",
            tool_calls=[
                {
                    "id": "c1",
                    "type": "function",
                    "function": {
                        "name": "file_write",
                        "arguments": '{"path": "src/auth.py", "content": "fixed"}',
                    },
                }
            ],
        ),
        ChatMessage(role="tool", content="ok", tool_call_id="c1"),
        ChatMessage(
            role="assistant",
            content="",
            tool_calls=[
                {
                    "id": "c2",
                    "type": "function",
                    "function": {
                        "name": "update_task_checklist",
                        "arguments": '{"tasks": [{"id": "t1", "title": "Patch auth", "status": "completed"}]}',
                    },
                }
            ],
        ),
        ChatMessage(role="tool", content="updated", tool_call_id="c2"),
        ChatMessage(role="assistant", content="All done!"),
    ]

    notice = verification_gate_notice(conversation)
    assert notice is not None
    assert notice.startswith("Verification gate: you changed files in this turn")


def test_verification_gate_passes_when_verification_tool_ran_after_edit() -> None:
    # Scenario: Agent wrote a file, ran tests, and updated checklist.
    conversation = [
        ChatMessage(role="user", content="Fix the auth vulnerability"),
        ChatMessage(
            role="assistant",
            content="",
            tool_calls=[
                {
                    "id": "c1",
                    "type": "function",
                    "function": {
                        "name": "file_write",
                        "arguments": '{"path": "src/auth.py", "content": "fixed"}',
                    },
                }
            ],
        ),
        ChatMessage(role="tool", content="ok", tool_call_id="c1"),
        ChatMessage(
            role="assistant",
            content="",
            tool_calls=[
                {
                    "id": "c2",
                    "type": "function",
                    "function": {
                        "name": "run_tests",
                        "arguments": '{"command": "pytest"}',
                    },
                }
            ],
        ),
        ChatMessage(role="tool", content="1 passed", tool_call_id="c2"),
        ChatMessage(
            role="assistant",
            content="",
            tool_calls=[
                {
                    "id": "c3",
                    "type": "function",
                    "function": {
                        "name": "update_task_checklist",
                        "arguments": '{"tasks": [{"id": "t1", "title": "Patch auth", "status": "completed"}]}',
                    },
                }
            ],
        ),
        ChatMessage(role="tool", content="updated", tool_call_id="c3"),
        ChatMessage(role="assistant", content="All verified and done!"),
    ]

    notice = verification_gate_notice(conversation)
    assert notice is None


def test_verification_gate_does_not_trigger_for_read_only_turns() -> None:
    # Scenario: Turn only ran grep_search and read_file.
    conversation = [
        ChatMessage(role="user", content="Where is login defined?"),
        ChatMessage(
            role="assistant",
            content="",
            tool_calls=[
                {
                    "id": "c1",
                    "type": "function",
                    "function": {
                        "name": "grep_search",
                        "arguments": '{"query": "def login"}',
                    },
                }
            ],
        ),
        ChatMessage(role="tool", content="src/login.py:10", tool_call_id="c1"),
        ChatMessage(role="assistant", content="It is defined at src/login.py:10."),
    ]

    notice = verification_gate_notice(conversation)
    assert notice is None


def _call(call_id: str, name: str, arguments: str) -> ChatMessage:
    return ChatMessage(
        role="assistant",
        content="",
        tool_calls=[
            {"id": call_id, "type": "function", "function": {"name": name, "arguments": arguments}}
        ],
    )


_DONE = '{"tasks": [{"id": "t1", "title": "Fix CI", "status": "completed"}]}'


def test_an_edit_from_an_earlier_turn_does_not_reopen_a_turn_that_only_read_ci() -> None:
    # session_116c1b0bb3: woken by a CI update after the edits; it answered and was sent back
    # to rebuild the project for 23 steps.
    conversation = [
        ChatMessage(role="user", content="Fix the CI errors"),
        _call("c1", "file_edit", '{"path": "ticket/go.mod"}'),
        ChatMessage(role="tool", content="ok", tool_call_id="c1"),
        _call("c2", "update_task_checklist", _DONE),
        ChatMessage(role="tool", content="updated", tool_call_id="c2"),
        ChatMessage(role="assistant", content="Pushed; waiting for CI."),
        ChatMessage(role="user", content="[Automatic turn] All 6 CI checks passed."),
        _call("c3", "github_pull_request", '{"action": "view"}'),
        ChatMessage(role="tool", content="#3816 open", tool_call_id="c3"),
        ChatMessage(role="assistant", content="CI is green. Summary: ..."),
    ]
    assert verification_gate_notice(conversation) is None


def test_a_test_command_run_in_the_shell_counts_as_verification() -> None:
    conversation = [
        ChatMessage(role="user", content="Fix it"),
        _call("c1", "file_edit", '{"path": "a.go"}'),
        ChatMessage(role="tool", content="ok", tool_call_id="c1"),
        _call("c2", "bash_exec", '{"command": "go test ./...", "cwd": "/workspace/svc"}'),
        ChatMessage(role="tool", content="ok", tool_call_id="c2"),
        _call("c3", "update_task_checklist", _DONE),
        ChatMessage(role="tool", content="updated", tool_call_id="c3"),
        ChatMessage(role="assistant", content="Done."),
    ]
    assert verification_gate_notice(conversation) is None


def test_the_reminder_lets_the_agent_finish_when_it_cannot_verify() -> None:
    conversation = [
        ChatMessage(role="user", content="Fix it"),
        _call("c1", "file_edit", '{"path": "a.go"}'),
        ChatMessage(role="tool", content="ok", tool_call_id="c1"),
        _call("c2", "bash_exec", '{"command": "git push", "cwd": "/workspace/svc"}'),
        ChatMessage(role="tool", content="ok", tool_call_id="c2"),
        _call("c3", "update_task_checklist", _DONE),
        ChatMessage(role="tool", content="updated", tool_call_id="c3"),
        ChatMessage(role="assistant", content="Done."),
    ]
    notice = verification_gate_notice(conversation)
    assert notice is not None and "Do not install toolchains" in notice


def test_published_work_and_a_checks_read_count_as_verification() -> None:
    # slack_2ff369f1_…: edits, git_push, git_open_pr, github_pull_request(actions=["checks"]),
    # "Done", then sent back to install Go and buf to check the change locally.
    for name, arguments in (
        ("git_push", '{"path": "/workspace/proto"}'),
        ("git_open_pr", '{"path": "/workspace/proto", "target_branch": "beta"}'),
        ("github_pull_request", '{"actions": ["checks"], "number": 3820}'),
    ):
        conversation = [
            ChatMessage(role="user", content="Implement it and open a PR"),
            _call("c1", "file_edit", '{"path": "pb/cross-models.proto"}'),
            ChatMessage(role="tool", content="ok", tool_call_id="c1"),
            _call("c2", name, arguments),
            ChatMessage(role="tool", content="ok", tool_call_id="c2"),
            _call("c3", "update_task_checklist", _DONE),
            ChatMessage(role="tool", content="updated", tool_call_id="c3"),
            ChatMessage(role="assistant", content="Done — 2 PRs opened."),
        ]
        assert verification_gate_notice(conversation) is None, name


def test_merge_verified_answers() -> None:
    draft = "I modified auth.py to fix the race condition."
    verif = "Ran pytest: 5 passed."
    merged = merge_verified_answers(draft, verif)
    assert "I modified auth.py" in merged
    assert "**Verification:**" in merged
    assert "Ran pytest: 5 passed." in merged

    full = f"{draft}\nMore info."
    assert merge_verified_answers(draft, full) == full
    assert merge_verified_answers(draft, "") == draft
    assert merge_verified_answers("", verif) == verif

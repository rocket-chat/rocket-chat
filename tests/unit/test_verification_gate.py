"""Unit tests for the plan -> execute -> verify gate (Item E1)."""

from __future__ import annotations

from agent_core.checklist import verification_gate_notice

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
    assert "Verification gate: you modified files but have not run project verification" in notice


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

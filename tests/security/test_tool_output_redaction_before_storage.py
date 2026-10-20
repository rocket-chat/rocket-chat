"""Security tests ensuring tool output is redacted before it enters session_messages, events, or prompts."""

import json
from typing import Any

import pytest
from sandbox_driver.fake_driver import FakeSandboxDriver

from specifications.interfaces.sandbox import ExecResult
from tests.turn_harness import run_turn

SECRET_TOKEN = "ghp_supersecretgithubtoken1234567890abc"


class _SecretLeakingDriver(FakeSandboxDriver):
    async def exec_command(self, session_id: str, command: str, *a: Any, **k: Any) -> ExecResult:
        if "cat .env" in command or "print_secret" in command:
            return ExecResult(0, f"GITHUB_TOKEN={SECRET_TOKEN}\n", "", 1)
        return await super().exec_command(session_id, command, *a, **k)


@pytest.mark.asyncio
async def test_secrets_in_tool_output_never_enter_storage_events_or_prompts() -> None:
    driver = _SecretLeakingDriver()
    script = [
        ("bash_exec", {"command": "cat .env", "cwd": "/workspace"}),
        ("file_list", {"directory": "/workspace"}),
    ]

    outcome = await run_turn(
        "org_sec_test",
        script=script,
        prompt="Please check .env and list files.",
        driver=driver,
    )

    # 1. Assert SECRET_TOKEN is never present in the stored conversation history (session_messages)
    for msg in outcome.session.conversation_history:
        assert msg.content is None or SECRET_TOKEN not in msg.content
        if msg.role == "tool":
            assert "[REDACTED:github_token]" in (msg.content or "")

    # 2. Assert SECRET_TOKEN is never present in any published event payload
    for event in outcome.events:
        payload_str = json.dumps(event.payload)
        assert SECRET_TOKEN not in payload_str

    # 3. Assert SECRET_TOKEN is never present in subsequent model requests
    assert len(outcome.model.requests) >= 2
    second_request = outcome.model.requests[1]
    messages_str = json.dumps(
        [
            m.model_dump() if hasattr(m, "model_dump") else str(m)
            for m in getattr(second_request, "messages", [])
        ]
    )
    assert SECRET_TOKEN not in messages_str
    assert "[REDACTED:github_token]" in messages_str

    # 4. Check tool_completed event marked redaction
    tool_events = outcome.events_of("tool_completed")
    bash_event = next(e for e in tool_events if e.payload.get("name") == "bash_exec")
    assert bash_event.payload.get("redacted") == 1
    assert "[REDACTED:github_token]" in bash_event.payload.get("result", "")

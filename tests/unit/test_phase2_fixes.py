"""Unit tests verifying Phase 2 fixes (M1 through M18)."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from agent_core.checklist import latest_tasks
from agent_core.context import ExecutionContext
from git_engine.config import GitEngineSettings
from git_engine.engine import GitEngine
from git_engine.publish import BranchPublisher, _FileChange
from llm_gateway.gateway import LiteLLMGateway

from specifications.interfaces.events import EventType


def test_m7_dead_events_removed() -> None:
    """Verify TASK_STARTED and TASK_COMPLETED are no longer in EventType enum."""
    assert not hasattr(EventType, "TASK_STARTED")
    assert not hasattr(EventType, "TASK_COMPLETED")


def test_m8_agents_hold_no_sandbox_resources() -> None:
    """Agents pick a sandbox type; the type holds the image, CPU and memory (decision D7)."""
    from api.db.models import AgentPersonaEntityModel

    columns = AgentPersonaEntityModel.__table__.c
    assert "cpu_limit" not in columns and "memory_limit" not in columns
    assert "sandbox_image" not in columns and "sandbox_id" in columns


def test_m9_parse_repo_pr() -> None:
    """Verify _parse_repo_pr parses full URLs, shorthand, and handles normalization."""
    from api.turns.callbacks import _parse_repo_pr

    # Full GitHub PR URL
    assert _parse_repo_pr("https://github.com/org/repo/pull/42", "default/repo") == ("org/repo", 42)
    # Shorthand owner/repo#pr
    assert _parse_repo_pr("Org/Repo#99", "default/repo") == ("org/repo", 99)
    # Raw number with default repo
    assert _parse_repo_pr("#123", "org/my-repo") == ("org/my-repo", 123)
    assert _parse_repo_pr("123", "org/my-repo") == ("org/my-repo", 123)
    # Unmatched string without slash returns (clean.lower(), None)
    assert _parse_repo_pr("invalid-text", "org/my-repo") == ("invalid-text", None)


@pytest.mark.asyncio
async def test_m10_clamped_timeouts() -> None:
    """Verify timeouts are clamped by wait kind in _build_wait_for."""
    from api.events import AsyncIOEventBus
    from api.turns.callbacks import _build_wait_for

    bus = AsyncIOEventBus()
    events = []
    bus.publish = AsyncMock(side_effect=lambda e: events.append(e))  # type: ignore[method-assign]

    wait_for = _build_wait_for("s1", bus, AsyncMock())

    # Time wait max 86400 (1 day)
    await wait_for("time", "100000", None)
    assert events[-1].payload["timeout_seconds"] == 86400

    # Process exit max 3600 (1 hour)
    await wait_for("process_exit", "proc_1", 5000)
    assert events[-1].payload["timeout_seconds"] == 3600

    # CI status max 7200 (2 hours)
    await wait_for("ci_status", "org/repo#1", 10000)
    assert events[-1].payload["timeout_seconds"] == 7200


def test_m11_execution_context_turn_index() -> None:
    """Verify ExecutionContext has turn_index field initialized."""
    ctx = ExecutionContext(
        session_id="s1",
        event_queue=asyncio.Queue(),
        parent_model="m",
        turn_index=3,
    )
    assert ctx.turn_index == 3


def test_m13_checklist_normalizes_calls() -> None:
    """Verify checklist handles both call['function'] and direct call shapes."""
    from specifications.interfaces.llm import ChatMessage

    # Shape 1: OpenAI style { "function": { "name": ..., "arguments": ... } }
    msg_openai = ChatMessage(
        role="assistant",
        content="",
        tool_calls=[
            {
                "function": {
                    "name": "update_task_checklist",
                    "arguments": '{"tasks": [{"id": "1", "title": "step 1", "status": "completed"}]}',
                }
            }
        ],
    )
    tasks = latest_tasks([msg_openai])
    assert len(tasks) == 1
    assert tasks[0]["title"] == "step 1"

    # Shape 2: Direct { "name": ..., "arguments": { ... } }
    msg_direct = ChatMessage(
        role="assistant",
        content="",
        tool_calls=[
            {
                "name": "update_task_checklist",
                "arguments": {"tasks": [{"id": "2", "title": "step 2", "status": "in_progress"}]},
            }
        ],
    )
    tasks_direct = latest_tasks([msg_direct])
    assert len(tasks_direct) == 1
    assert tasks_direct[0]["title"] == "step 2"


def test_m15_interaction_atomic_pop() -> None:
    """Verify submit_question_answer and submit_human_approval pop atomically."""
    from agent_core.interaction import DecisionGateManager

    from specifications.interfaces.agent import ApprovalRequest, InteractiveQuestion

    gates = DecisionGateManager()
    evt_q = asyncio.Event()
    q = InteractiveQuestion(
        question_id="q1",
        question_text="What is your name?",
        options=["Alice", "Bob"],
        is_multi_select=False,
    )
    gates.register_question("q1", "session-1", evt_q, q)
    assert "q1" in gates._pending_questions
    gates.submit_question_answer("session-1", "q1", ["Alice"])
    assert "q1" not in gates._pending_questions
    assert evt_q.is_set()
    with pytest.raises(KeyError):
        gates.submit_question_answer("session-1", "q1", ["Alice"])

    evt_a = asyncio.Event()
    appr = ApprovalRequest(
        action_id="a1",
        action_type="bash_exec",
        command_or_path="deploy",
        risk_level="medium",
    )
    gates.register_approval("a1", "session-1", evt_a, appr)
    assert "a1" in gates._pending_approvals
    gates.submit_human_approval("session-1", "a1", True)
    assert "a1" not in gates._pending_approvals
    assert evt_a.is_set()
    with pytest.raises(KeyError):
        gates.submit_human_approval("session-1", "a1", True)


def test_m16_rotating_log_wrapper() -> None:
    """Verify background processes wrapper caps log with python rotator and drops ulimit -f."""
    from agent_core.tools.processes import _START, _WRAPPER

    assert "ulimit -f" not in _START
    assert "truncate" in _WRAPPER
    assert "max_bytes" in _WRAPPER


@pytest.mark.asyncio
async def test_m17_stream_idle_timeout() -> None:
    """Verify gateway yields error chunk on stream idle timeout exceeding threshold."""
    from specifications.interfaces.llm import ChatMessage, ModelRequest

    gateway = LiteLLMGateway()

    class MockDelta:
        content = "first"
        tool_calls = None
        reasoning_content = None
        thinking = None
        thinking_blocks = None

    class MockChoice:
        delta = MockDelta()
        finish_reason = None

    class MockChunk:
        def __init__(self) -> None:
            self.choices = [MockChoice()]
            self.usage = None

    async def slow_stream():
        yield MockChunk()
        await asyncio.sleep(2.0)
        yield MockChunk()

    req = ModelRequest(
        model="gpt-4o",
        messages=[ChatMessage(role="user", content="hello")],
        timeout=0.1,
    )

    with patch("llm_gateway.gateway.litellm.acompletion", return_value=slow_stream()):
        chunks = []
        async for chunk in gateway.chat_stream(req):
            chunks.append(chunk)

        error_chunks = [c for c in chunks if c.finish_reason == "error"]
        assert len(error_chunks) == 1
        assert "idle timeout" in (error_chunks[0].error or "").lower()


def test_m18_executable_mode_check() -> None:
    """Verify _check_representable catches executable bit on modified files."""
    notices: list[str] = []
    # Added executable file
    change_a = _FileChange(status="A", path="script.sh", mode="100755", blob="abc")
    BranchPublisher._check_representable(change_a, notices)
    assert len(notices) == 1
    assert "executable bit" in notices[0]

    # Modified file with executable bit
    notices.clear()
    change_m = _FileChange(status="M", path="script.sh", mode="100755", blob="abc")
    BranchPublisher._check_representable(change_m, notices)
    assert len(notices) == 1
    assert "executable bit" in notices[0]

    # Normal non-executable file
    notices.clear()
    change_normal = _FileChange(status="M", path="doc.txt", mode="100644", blob="abc")
    BranchPublisher._check_representable(change_normal, notices)
    assert len(notices) == 0


@pytest.mark.asyncio
async def test_m18_ambiguous_installations() -> None:
    """Verify engine refuses installation fallback if multiple installations exist."""
    settings = GitEngineSettings(
        github_app_id="12345",
        github_app_private_key="test-key",
        allow_unmapped_installations=True,
    )
    engine = GitEngine(settings=settings)

    with (
        patch.object(engine, "generate_app_jwt", return_value="jwt"),
        patch("httpx.AsyncClient.get") as mock_get,
    ):
        # Simulate /app/installations returning 2 installations
        mock_res = MagicMock()
        mock_res.status_code = 200
        mock_res.json.return_value = [{"id": 101}, {"id": 102}]
        mock_get.return_value = mock_res

        token = await engine.get_github_access_token()
        assert token is None

"""Two tenants, two simultaneous turns: nothing may cross sessions.

Tool collaborators (questions, approvals, rename, knowledge search, sandbox provisioning)
used to be installed on the shared ``ToolRegistry`` by whichever turn started last, so one
tenant's approval prompt could be answered through another tenant's turn.
"""

from __future__ import annotations

import asyncio
import json
import random
from collections.abc import AsyncIterator
from typing import Any

import pytest
from agent_core.orchestrator import AsyncReActOrchestrator
from agent_core.tools.registry import ToolRegistry

from specifications.interfaces.agent import AgentEvent, TurnCallbacks
from specifications.interfaces.llm import LLMGatewayProtocol, StreamChunk


class TagScriptedGateway(LLMGatewayProtocol):
    """Each conversation runs: ask_question -> session_rename -> kb_search -> summary.

    The tag (``A``/``B``) is read from the first user message, so both sessions share one
    gateway exactly like production, and jitter makes their steps interleave.
    """

    async def chat_stream(self, request: Any) -> AsyncIterator[StreamChunk]:
        await asyncio.sleep(random.random() * 0.01)
        user_text = next(m.content for m in request.messages if m.role == "user")
        tag = "A" if "tag=A" in user_text else "B"
        tool_results = [m.content for m in request.messages if m.role == "tool"]
        step = len(tool_results)
        calls = [
            ("ask_question", {"question": f"Proceed {tag}?", "options": [f"{tag}-yes", "no"]}),
            ("session_rename", {"title": f"title-{tag}"}),
            ("kb_search", {"query": f"query-{tag}"}),
        ]
        if step < len(calls):
            name, arguments = calls[step]
            yield StreamChunk(
                tool_call_delta={
                    "index": 0,
                    "id": f"call_{tag}_{step}",
                    "function": {"name": name, "arguments": json.dumps(arguments)},
                }
            )
            return
        yield StreamChunk(text_delta="RESULTS " + " || ".join(tool_results))

    async def check_budget(self, tenant_org_id: str, tenant_user_id: str) -> bool:
        return True

    async def record_usage(self, *args: Any, **kwargs: Any) -> None:
        return None


async def _drive_turn(
    orchestrator: AsyncReActOrchestrator,
    tag: str,
    seen_questions: dict[str, str],
    renames: list[tuple[str, str]],
    searches: list[tuple[str, str]],
) -> str:
    session_id = f"session_{tag}"

    async def rename(target: str, title: str) -> None:
        renames.append((f"cb-{tag}:{target}", title))

    async def kb_search(query: str, _kb: str | None, _limit: int) -> str:
        searches.append((f"cb-{tag}", query))
        return f"kb-result-for-{tag}"

    callbacks = TurnCallbacks(rename=rename, kb_search=kb_search)
    answer = ""
    async for event in orchestrator.process_user_turn(
        session_id,
        f"tag={tag} please go",
        tenant_org_id=f"org_{tag}",
        tenant_user_id=f"user_{tag}",
        callbacks=callbacks,
    ):
        assert event.session_id == session_id, "event leaked across sessions"
        if event.event_type == "interactive_question":
            seen_questions[tag] = event.payload["question_text"]
            await orchestrator.submit_question_answer(
                session_id, event.payload["question_id"], [f"{tag}-yes"]
            )
        elif event.event_type == "thought" and not event.payload.get("is_reasoning"):
            answer += event.payload.get("delta", "")
    return answer


@pytest.mark.asyncio
@pytest.mark.parametrize("round_", range(20))
async def test_concurrent_turns_never_share_collaborators(round_: int) -> None:
    registry = ToolRegistry()
    orchestrator = AsyncReActOrchestrator(gateway=TagScriptedGateway(), registry=registry)
    questions: dict[str, str] = {}
    renames: list[tuple[str, str]] = []
    searches: list[tuple[str, str]] = []

    answer_a, answer_b = await asyncio.wait_for(
        asyncio.gather(
            _drive_turn(orchestrator, "A", questions, renames, searches),
            _drive_turn(orchestrator, "B", questions, renames, searches),
        ),
        timeout=20,
    )

    assert questions == {"A": "Proceed A?", "B": "Proceed B?"}
    assert "A-yes" in answer_a and "B-yes" not in answer_a
    assert "B-yes" in answer_b and "A-yes" not in answer_b
    assert "kb-result-for-A" in answer_a and "kb-result-for-B" not in answer_a
    assert "kb-result-for-B" in answer_b and "kb-result-for-A" not in answer_b
    assert sorted(renames) == [("cb-A:session_A", "title-A"), ("cb-B:session_B", "title-B")]
    assert sorted(searches) == [("cb-A", "query-A"), ("cb-B", "query-B")]


@pytest.mark.asyncio
async def test_registry_holds_no_per_turn_state_after_a_turn() -> None:
    registry = ToolRegistry()
    orchestrator = AsyncReActOrchestrator(gateway=TagScriptedGateway(), registry=registry)
    await _drive_turn(orchestrator, "A", {}, [], [])
    for attribute in (
        "_on_question_callback",
        "_on_approval_callback",
        "_on_rename_callback",
        "_on_kb_search_callback",
        "_on_diff_callback",
        "_on_plan_callback",
        "_on_subagent_callback",
        "_on_ensure_sandbox_callback",
        "_on_execute_skill_callback",
    ):
        assert getattr(registry, attribute) is None, attribute


@pytest.mark.asyncio
async def test_file_locks_are_scoped_per_session_and_released() -> None:
    registry = ToolRegistry()
    orchestrator = AsyncReActOrchestrator(gateway=TagScriptedGateway(), registry=registry)
    dispatcher = orchestrator.dispatcher
    order: list[str] = []

    async def hold(session: str, label: str, delay: float) -> None:
        async with dispatcher.file_lock(session, "src/app.py"):
            order.append(f"{label}-in")
            await asyncio.sleep(delay)
            order.append(f"{label}-out")

    await asyncio.gather(hold("s1", "a", 0.05), hold("s2", "b", 0.01))
    # Different sessions do not serialize each other...
    assert order.index("b-out") < order.index("a-out")

    order.clear()
    await asyncio.gather(hold("s1", "a", 0.03), hold("s1", "b", 0.0))
    # ...while the same session and path are mutually exclusive.
    assert order in (["a-in", "a-out", "b-in", "b-out"], ["b-in", "b-out", "a-in", "a-out"])
    assert dispatcher._file_locks == {}


def test_event_type_import_is_stable() -> None:
    assert AgentEvent.__name__ == "AgentEvent"

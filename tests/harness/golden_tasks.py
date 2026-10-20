"""Deterministic golden-task evaluation harness.

The product targets "task completion (golden set) ≥ 80 % pass"; this module is the
harness that checks it: a
bounded set of golden tasks that drive the **real** ``AsyncReActOrchestrator`` against
the deterministic stub gateway and the fake sandbox, then score pass/fail from
observable state (session history, tool results, workspace snapshot) — never wall-clock.

Why a stub subclass instead of the shipped stub alone: ``DeterministicStubGateway``
only scripts ``list_dir`` / a clarifying question / a plain answer, so the sandbox
*exec* and *file-write* paths would never be exercised. :class:`GoldenStubGateway`
adds three pure-function markers on top of it (``[golden:shell]``, ``[golden:write]``,
``[golden:read]``) and reuses the inherited usage ledger, so the suite stays a pure
function of the request and remains cost/token-accounted with no provider call.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from agent_core.orchestrator import AsyncReActOrchestrator
from agent_core.tools.registry import ToolRegistry
from llm_gateway.pricing import estimate_cost_usd
from llm_gateway.stub import QUESTION_MARKER, TOOL_MARKER, DeterministicStubGateway
from sandbox_driver.fake_driver import FakeSandboxDriver

from specifications.interfaces.agent import AgentEvent, SystemPrompt
from specifications.interfaces.llm import ChatMessage, ModelRequest, StreamChunk

DEFAULT_MODEL = "openrouter/deepseek/deepseek-v4.1-flash"
STABLE_PREFIX = "You are a Senior Autonomous Software Engineer."

SHELL_MARKER = "[golden:shell]"
WRITE_MARKER = "[golden:write]"
READ_MARKER = "[golden:read]"
MEMORY_BLOCK = (
    "# --- Recalled Long-Term Memory (volatile; not cacheable) ---\n"
    "- [fact/org] Production deploys run at 09:00 UTC."
)

STRUCTURED_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"title": {"type": "string"}, "count": {"type": "integer"}},
    "required": ["title", "count"],
}


class GoldenStubGateway(DeterministicStubGateway):
    """The deterministic stub plus shell/file-write/file-read scripted markers.

    Every override is a pure function of the prompt (like the shipped stub), and
    ``requests`` retains each dispatched :class:`ModelRequest` so a golden task can
    assert on the dynamic prompt suffix (recall injection) without a provider.
    """

    def __init__(self, *, chunk_delay_seconds: float = 0.0) -> None:
        super().__init__(chunk_delay_seconds=chunk_delay_seconds)
        self.requests: list[ModelRequest] = []

    def _script(self, prompt: str) -> tuple[str, str, dict[str, object] | None]:
        if SHELL_MARKER in prompt:
            return (
                "Golden reasoning: running the test suite in the sandbox.",
                "",
                _tool_call(
                    "call_golden_shell", "bash_exec", {"command": "pytest", "cwd": "/workspace"}
                ),
            )
        if WRITE_MARKER in prompt:
            return (
                "Golden reasoning: persisting the notes file.",
                "",
                _tool_call(
                    "call_golden_write",
                    "file_write",
                    {"path": "notes.txt", "content": "golden", "overwrite": True},
                ),
            )
        if READ_MARKER in prompt:
            return (
                "Golden reasoning: reading the seeded source file.",
                "",
                _tool_call("call_golden_read", "file_read", {"path": "src/app.py"}),
            )
        return super()._script(prompt)

    async def chat_stream(self, request: ModelRequest) -> AsyncIterator[StreamChunk]:
        """Record the request, then delegate to the inherited deterministic stream."""
        self.requests.append(request)
        async for chunk in super().chat_stream(request):
            yield chunk


def _tool_call(call_id: str, name: str, arguments: dict[str, Any]) -> dict[str, object]:
    return {
        "index": 0,
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(arguments)},
    }


@dataclass
class Outcome:
    """Everything a scorer may inspect, captured from one golden-task execution."""

    task_id: str
    events: list[AgentEvent] = field(default_factory=list)
    conversation: list[ChatMessage] = field(default_factory=list)
    driver: FakeSandboxDriver | None = None
    gateway: GoldenStubGateway | None = None
    registry: ToolRegistry | None = None
    session_id: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def tool_results(self) -> list[str]:
        return [
            str(e.payload.get("result", ""))
            for e in self.events
            if e.event_type == "tool_completed"
        ]

    def final_answer(self) -> str:
        assistants = [m.content or "" for m in self.conversation if m.role == "assistant"]
        return assistants[-1] if assistants else ""

    def tool_messages(self) -> list[str]:
        return [m.content or "" for m in self.conversation if m.role == "tool"]


@dataclass(frozen=True)
class GoldenResult:
    """A scored golden task: the pass/fail plus its deterministic token/cost columns."""

    task_id: str
    layer: str
    kind: str
    passed: bool
    detail: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    cost_usd: float


@dataclass(frozen=True)
class GoldenTask:
    task_id: str
    description: str
    layer: str
    kind: str
    execute: Callable[[], Awaitable[Outcome]]
    check: Callable[[Outcome], tuple[bool, str]]


def _components(
    memory: bool = False,
) -> tuple[str, FakeSandboxDriver, GoldenStubGateway, ToolRegistry]:
    session_id = f"golden_{uuid.uuid4().hex[:8]}"
    driver = FakeSandboxDriver()
    gateway = GoldenStubGateway()
    registry = ToolRegistry(driver=driver, session_id=session_id)
    if memory:
        registry.set_memory_callbacks(remember=_remember, recall=_recall)
    return session_id, driver, gateway, registry


async def _remember(content: str, kind: str, scope: str) -> str:
    return "stored"


async def _recall(query: str, scope: str | None, limit: int) -> str:
    return MEMORY_BLOCK


async def _drain(
    orchestrator: AsyncReActOrchestrator, session_id: str, prompt: str
) -> list[AgentEvent]:
    events: list[AgentEvent] = []
    parts = SystemPrompt(stable=STABLE_PREFIX, dynamic="")
    async for event in orchestrator.process_user_turn(
        session_id, prompt, system_prompt_parts=parts
    ):
        events.append(event)
    return events


def _token_cost(gateway: GoldenStubGateway) -> tuple[int, int, float]:
    """Sum prompt/completion tokens and estimated USD over the gateway's streamed calls.

    ``chat_stream`` appends ``{model, prompt_tokens, completion_tokens, total_tokens}``;
    ``record_usage`` appends a *tenant-scoped* entry. Only the former is counted so a
    single model step is never double-billed.
    """
    prompt_tokens = completion_tokens = 0
    cost = 0.0
    for entry in gateway.usage_ledger:
        if "tenant_org_id" in entry:
            continue
        prompt = _as_int(entry.get("prompt_tokens"))
        completion = _as_int(entry.get("completion_tokens"))
        prompt_tokens += prompt
        completion_tokens += completion
        cost += estimate_cost_usd(str(entry.get("model", "")), prompt, completion)
    return prompt_tokens, completion_tokens, round(cost, 6)


def _as_int(value: object) -> int:
    return int(value) if isinstance(value, (int, float)) else 0


async def _outcome(task_id: str, *, memory: bool = False, prompt: str) -> Outcome:
    session_id, driver, gateway, registry = _components(memory=memory)
    orchestrator = AsyncReActOrchestrator(
        gateway=gateway, registry=registry, default_model=DEFAULT_MODEL
    )
    events = await _drain(orchestrator, session_id, prompt)
    return Outcome(
        task_id=task_id,
        events=events,
        conversation=list(orchestrator._session_histories.get(session_id, [])),
        driver=driver,
        gateway=gateway,
        registry=registry,
        session_id=session_id,
    )


# -- golden tasks -----------------------------------------------------------------


async def _answer() -> Outcome:
    return await _outcome("answer_conversational", prompt="Explain the retry policy.")


def _check_answer(outcome: Outcome) -> tuple[bool, str]:
    answer = outcome.final_answer()
    completed = any(
        e.event_type == "status" and e.payload.get("state") == "completed" for e in outcome.events
    )
    passed = completed and answer.startswith("Stub answer")
    return passed, f"completed={completed} answer={answer[:60]!r}"


async def _answer_twice() -> Outcome:
    first = await _outcome("answer_conversational", prompt="Summarise the diff.")
    second = await _outcome("answer_conversational", prompt="Summarise the diff.")
    outcome = Outcome(task_id="answer_deterministic_repeat")
    outcome.extra["first"] = first.final_answer()
    outcome.extra["second"] = second.final_answer()
    if first.gateway is not None and second.gateway is not None:
        # Both calls are part of this task, so bill their ledgers together.
        first.gateway.usage_ledger.extend(second.gateway.usage_ledger)
    outcome.gateway = first.gateway
    return outcome


def _check_answer_twice(outcome: Outcome) -> tuple[bool, str]:
    first = str(outcome.extra["first"])
    second = str(outcome.extra["second"])
    return first == second and bool(first), f"identical={first == second}"


async def _tool_use() -> Outcome:
    return await _outcome("tool_use_list_dir", prompt=f"{TOOL_MARKER} list the workspace.")


def _check_tool_use(outcome: Outcome) -> tuple[bool, str]:
    results = outcome.tool_results()
    listed = any("README.md" in result for result in results)
    final = outcome.final_answer()
    return listed and bool(final), f"tool_results={len(results)} listed_seed={listed}"


async def _shell() -> Outcome:
    return await _outcome("shell_command", prompt=f"{SHELL_MARKER} run the test suite.")


def _check_shell(outcome: Outcome) -> tuple[bool, str]:
    calls = outcome.driver.exec_calls if outcome.driver else []
    ran_pytest = any("pytest" in command for _, command in calls)
    observed = any("passed" in message for message in outcome.tool_messages())
    return ran_pytest and observed, f"ran_pytest={ran_pytest} observed={observed}"


async def _write() -> Outcome:
    return await _outcome("file_write", prompt=f"{WRITE_MARKER} save the notes.")


def _check_write(outcome: Outcome) -> tuple[bool, str]:
    snapshot = outcome.driver.file_snapshot(outcome.session_id) if outcome.driver else {}
    stored = snapshot.get("notes.txt", "")
    return stored.strip() == "golden", f"notes.txt={stored.strip()!r}"


async def _read() -> Outcome:
    return await _outcome("file_read_seeded", prompt=f"{READ_MARKER} read the app module.")


def _check_read(outcome: Outcome) -> tuple[bool, str]:
    read = any("def greet" in message for message in outcome.tool_messages())
    return read, f"seeded_content_read={read}"


async def _question() -> Outcome:
    return await _outcome("clarifying_question", prompt=QUESTION_MARKER)


def _check_question(outcome: Outcome) -> tuple[bool, str]:
    answer = outcome.final_answer()
    return "clarifying question" in answer, f"answer={answer[:60]!r}"


async def _memory() -> Outcome:
    outcome = await _outcome(
        "memory_recall_injected", memory=True, prompt="When do production deploys run?"
    )
    requests = outcome.gateway.requests if outcome.gateway else []
    outcome.extra["injected"] = any(
        MEMORY_BLOCK in (message.content or "")
        for request in requests
        for message in request.messages
    )
    return outcome


def _check_memory(outcome: Outcome) -> tuple[bool, str]:
    injected = bool(outcome.extra.get("injected"))
    return injected and bool(outcome.final_answer()), f"recall_injected={injected}"


async def _multi_turn() -> Outcome:
    session_id, driver, gateway, registry = _components()
    orchestrator = AsyncReActOrchestrator(
        gateway=gateway, registry=registry, default_model=DEFAULT_MODEL
    )
    first = await _drain(orchestrator, session_id, "First question.")
    second = await _drain(orchestrator, session_id, "Second question.")
    conversation = list(orchestrator._session_histories.get(session_id, []))
    return Outcome(
        task_id="multi_turn_continuity",
        events=[*first, *second],
        conversation=conversation,
        driver=driver,
        gateway=gateway,
        registry=registry,
        session_id=session_id,
        extra={
            "user_turns": sum(1 for m in conversation if m.role == "user"),
            "assistant_turns": sum(1 for m in conversation if m.role == "assistant"),
        },
    )


def _check_multi_turn(outcome: Outcome) -> tuple[bool, str]:
    users = int(outcome.extra.get("user_turns", 0))
    assistants = int(outcome.extra.get("assistant_turns", 0))
    first_content = outcome.conversation[0].content if outcome.conversation else ""
    distinct = "First question." in (first_content or "")
    passed = users == 2 and assistants == 2 and distinct
    return passed, f"user_turns={users} assistant_turns={assistants}"


async def _structured() -> Outcome:
    gateway = GoldenStubGateway()
    request = ModelRequest(
        model=DEFAULT_MODEL,
        messages=[ChatMessage(role="user", content="Return a title and a count.")],
        response_schema=STRUCTURED_SCHEMA,
    )
    text = "".join([chunk.text_delta or "" async for chunk in gateway.chat_stream(request)])
    outcome = Outcome(task_id="structured_output", gateway=gateway)
    outcome.extra["text"] = text
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as err:
        outcome.extra["error"] = str(err)
        return outcome
    outcome.extra["parsed"] = parsed
    return outcome


def _check_structured(outcome: Outcome) -> tuple[bool, str]:
    parsed = outcome.extra.get("parsed")
    if not isinstance(parsed, dict):
        return False, f"invalid_json={outcome.extra.get('error')}"
    valid = (
        isinstance(parsed.get("title"), str)
        and isinstance(parsed.get("count"), int)
        and set(STRUCTURED_SCHEMA["required"]) <= set(parsed)
    )
    return valid, f"parsed_keys={sorted(parsed)}"


GOLDEN_TASKS: tuple[GoldenTask, ...] = (
    GoldenTask(
        "answer_conversational",
        "A plain prompt completes with the stub's deterministic answer in session history.",
        "orchestrator",
        "conversational",
        _answer,
        _check_answer,
    ),
    GoldenTask(
        "answer_deterministic_repeat",
        "The same prompt twice yields byte-identical answers (stub purity).",
        "orchestrator",
        "conversational",
        _answer_twice,
        _check_answer_twice,
    ),
    GoldenTask(
        "tool_use_list_dir",
        "A tool-marker prompt runs list_dir on the fake sandbox and answers from the result.",
        "orchestrator",
        "sandbox",
        _tool_use,
        _check_tool_use,
    ),
    GoldenTask(
        "shell_command",
        "A shell-marker prompt runs pytest through the fake sandbox and observes its output.",
        "orchestrator",
        "sandbox",
        _shell,
        _check_shell,
    ),
    GoldenTask(
        "file_write",
        "A write-marker prompt persists a file the workspace snapshot reflects.",
        "orchestrator",
        "sandbox",
        _write,
        _check_write,
    ),
    GoldenTask(
        "file_read_seeded",
        "A read-marker prompt reads a seeded workspace file into the tool transcript.",
        "orchestrator",
        "sandbox",
        _read,
        _check_read,
    ),
    GoldenTask(
        "clarifying_question",
        "A question-marker prompt returns the deterministic clarifying question.",
        "orchestrator",
        "conversational",
        _question,
        _check_question,
    ),
    GoldenTask(
        "memory_recall_injected",
        "A bound recall injects the memory into the volatile context at the end of the request.",
        "orchestrator",
        "conversational",
        _memory,
        _check_memory,
    ),
    GoldenTask(
        "multi_turn_continuity",
        "Two turns on one session accumulate two user and two assistant messages.",
        "orchestrator",
        "conversational",
        _multi_turn,
        _check_multi_turn,
    ),
    GoldenTask(
        "structured_output",
        "A schema-constrained dispatch returns JSON that validates against its schema.",
        "gateway",
        "conversational",
        _structured,
        _check_structured,
    ),
)


async def run_golden_suite() -> list[GoldenResult]:
    """Execute every golden task and return its scored, cost-annotated result."""
    results: list[GoldenResult] = []
    for task in GOLDEN_TASKS:
        outcome = await task.execute()
        passed, detail = task.check(outcome)
        prompt_tokens, completion_tokens, cost = (
            _token_cost(outcome.gateway) if outcome.gateway else (0, 0, 0.0)
        )
        results.append(
            GoldenResult(
                task_id=task.task_id,
                layer=task.layer,
                kind=task.kind,
                passed=passed,
                detail=detail,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=prompt_tokens + completion_tokens,
                cost_usd=cost,
            )
        )
    return results


def completion_rate(results: list[GoldenResult]) -> float:
    """Fraction of golden tasks that passed (the 'task completion' metric)."""
    if not results:
        return 0.0
    return sum(1 for result in results if result.passed) / len(results)


def render_report(results: list[GoldenResult]) -> str:
    """Render the deterministic, byte-stable golden-task report."""
    header = (
        f"{'task_id':<28}{'layer':<13}{'kind':<15}{'result':<7}"
        f"{'prompt_tok':>11}{'completion_tok':>15}{'total_tok':>10}{'cost_usd':>11}"
    )
    lines = [
        "GOLDEN TASK REPORT (deterministic stub gateway + fake sandbox)",
        "=" * len(header),
        header,
    ]
    for result in results:
        lines.append(
            f"{result.task_id:<28}{result.layer:<13}{result.kind:<15}"
            f"{'PASS' if result.passed else 'FAIL':<7}"
            f"{result.prompt_tokens:>11}{result.completion_tokens:>15}"
            f"{result.total_tokens:>10}{result.cost_usd:>11.6f}"
        )
    total_cost = round(sum(result.cost_usd for result in results), 6)
    lines.append("-" * len(header))
    lines.append(
        f"{'TOTAL':<28}{'':<13}{'':<15}{'':<7}"
        f"{sum(r.prompt_tokens for r in results):>11}"
        f"{sum(r.completion_tokens for r in results):>15}"
        f"{sum(r.total_tokens for r in results):>10}{total_cost:>11.6f}"
    )
    lines.append(
        f"TASK COMPLETION: {sum(1 for r in results if r.passed)}/{len(results)} = "
        f"{completion_rate(results) * 100:.1f}%"
    )
    return "\n".join(lines)

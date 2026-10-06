# Architecture Plan: Context Hygiene, Resilient Orchestration & Frontier Capabilities

## 1. Executive Summary & Objective

This document establishes the architecture and engineering plan for scaling **Rocket Chat's** agent orchestrator for heavy coding workloads with frontier models (Claude 3.7 Sonnet, Gemini 2.5 Pro, GPT-4.5/o1/o3, DeepSeek R1/V3) while **strictly minimizing custom code** by leveraging existing standard libraries, LiteLLM, and standard Python primitives.

### Core Problems Being Solved
1. **Context Window Exhaustion without Amnesia**: Raw tool outputs (build logs, test suites, large file inspections) consume 80k-200k tokens in long-running coding sessions. Standard message trimming drops early implementation plans and architectural decisions.
2. **Session Concurrency Race Conditions**: The shared `ToolRegistry` currently mutates closure callbacks (`set_diff_callback`, `set_subagent_callback`) on every turn, causing concurrent sessions to overwrite each other's event dispatchers.
3. **Monolithic Orchestrator Coupling**: [`packages/agent-core/src/agent_core/orchestrator.py`](file:///Users/nperriolat/Backend/rocket-chat/packages/agent-core/src/agent_core/orchestrator.py) (~500 lines) mixes state machines, decision gating, file locks, tool execution, and LLM streaming, violating the Single Responsibility Principle outlined in [`AGENTS.md`](file:///Users/nperriolat/Backend/rocket-chat/AGENTS.md).
4. **Frontier Model Reasoning Pass-Through**: LiteLLM supports extended thinking budgets (`thinking_budget_tokens`) and reasoning effort (`reasoning_effort`), but these parameters are not exposed across our `ModelRequest` interface.

---

## 2. Minimal-Custom-Code Architecture: 3-Tier Context Hygiene

Rather than building an over-engineered semantic memory engine or vector database, we implement the **industry-standard 3-tier hybrid compaction** using lightweight, deterministic algorithms.

```
┌────────────────────────────────────────────────────────────────────────┐
│ Tier 1: Zero-Loss Tool Output Clamping (Solves ~80% of bloat)          │
│ Immediate head/tail slice on stdout/stderr and file reads (~25 LOC).  │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │ If context > 70% threshold
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│ Tier 2: Tool-Payload Pruning (Preserves Reasoning & Plans)              │
│ Evacuates bulky results of old tool calls; preserves assistant        │
│ thoughts, reasoning, and plans intact (~30 LOC).                      │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │ If context > 85% emergency ceiling
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│ Tier 3: Invariant Anchor Compaction (LiteLLM Safety Net)               │
│ Anchors System Prompt + Initial Task + Active Checklist + Last N Turns│
└────────────────────────────────────────────────────────────────────────┘
```

### Tier 1: Deterministic Output Clamping (`agent_core/tools/clamp.py` ~25 lines)
* Applied directly in `bash_exec` and `file_read`.
* Configurable thresholds: `max_lines=400`, `max_bytes=25_000`.
* If exceeded:
  * Keeps the first 80 lines (command invoke, environment, build start).
  * Keeps the last 200 lines (stack traces, test assertion failures, exit code).
  * Injects a clean marker:
    `\n\n... [TRUNCATED {N} lines ({bytes}KB) of repetitive output. Inspect targeted lines or tail as needed] ...\n\n`

### Tier 2: Tool-Payload Pruning (`agent_core/context.py` ~35 lines)
* When total conversation tokens reach 70% of the model's context window (calculated using `litellm.token_counter` or character ratios):
  * Iterate over historical messages *older than the last 3 turns*.
  * For messages with `role == "tool"`, replace their verbose body with a compact receipt:
    `"[Tool {tool_name} returned {line_count} lines (content pruned to preserve active reasoning context)]"`
  * **Critical guarantee**: Assistant reasoning blocks, implementation plans, user instructions, and message sequence parity (`tool_call_id`) remain 100% intact.

### Tier 3: Anchor Compaction (~20 lines)
* If tokens still exceed 85% of context window:
  * Anchor **Invariant 1**: System prompt.
  * Anchor **Invariant 2**: Original user prompt.
  * Anchor **Invariant 3**: The structured checklist (`tasks` from `update_task_checklist`).
  * Anchor **Invariant 4**: The most recent 3 turns.
  * Middle conversational filler is replaced with a single summary message.

---

## 3. Safe Multi-Session Concurrency via Standard Python `contextvars`

### Current Vulnerability
In `orchestrator.py`:
```python
# Race condition: Shared registry instance has its callback mutated per turn
self._registry.set_subagent_callback(_on_subagent)
```
If User A and User B trigger turns simultaneously, User B's callback overwrites User A's `event_queue`.

### Minimal-Code Solution: Zero Third-Party Dependencies
Use Python's built-in `contextvars`:
```python
# In agent_core/context.py
import contextvars
from dataclasses import dataclass

@dataclass
class ExecutionContext:
    session_id: str
    event_queue: asyncio.Queue
    parent_model: str

current_execution_ctx: contextvars.ContextVar[ExecutionContext | None] = contextvars.ContextVar("current_ctx", default=None)
```
* Set `current_execution_ctx.set(...)` in `process_user_turn`.
* Tools and subagent runners read `current_execution_ctx.get()`.
* Completely thread-safe, coroutine-safe, and removes callback registration code entirely.

---

## 4. Single Responsibility Refactoring of `AsyncReActOrchestrator`

Following [`AGENTS.md`](file:///Users/nperriolat/Backend/rocket-chat/AGENTS.md), decompose [`orchestrator.py`](file:///Users/nperriolat/Backend/rocket-chat/packages/agent-core/src/agent_core/orchestrator.py) into modular components:

1. **`agent_core.dispatcher.ToolDispatcher`**:
   - Manages per-file `asyncio.Lock()` per path (`file_write`, `file_edit`, `apply_patch`).
   - Executes parallel tool calls via `asyncio.gather(*tasks)`.
   - Emits tool start/completion events.
2. **`agent_core.interaction.DecisionGateManager`**:
   - Manages human-in-the-loop interactive questions (`_pending_questions`, `_question_answers`).
   - Manages approval requests (`_pending_approvals`, `_approval_decisions`).
3. **`agent_core.context.ContextManager`**:
   - Manages Tier 1 clamping, Tier 2 payload pruning, and Tier 3 anchor compaction.
4. **`agent_core.orchestrator.AsyncReActOrchestrator`**:
   - Slender state machine (~120 lines) coordinating the turn loop, model streaming, and error propagation.

---

## 5. Frontier Reasoning & Thinking Controls

In [`specifications/interfaces/llm.py`](file:///Users/nperriolat/Backend/rocket-chat/specifications/interfaces/llm.py):
```python
@dataclass
class ModelRequest:
    model: str
    messages: list[ChatMessage]
    tools: list[ToolDefinition] | None = None
    temperature: float = 0.2
    max_tokens: int | None = None
    thinking_budget_tokens: int | None = None  # e.g., 4096 for Claude 3.7
    reasoning_effort: str | None = None        # "low" | "medium" | "high" for OpenAI o1/o3
    credentials: BYOKCredentials | None = None
    stream: bool = True
```

In [`packages/llm-gateway/src/llm_gateway/gateway.py`](file:///Users/nperriolat/Backend/rocket-chat/packages/llm-gateway/src/llm_gateway/gateway.py):
Pass directly to LiteLLM (LiteLLM handles provider translation automatically):
```python
if request.thinking_budget_tokens is not None:
    kwargs["thinking"] = {"type": "enabled", "budget_tokens": request.thinking_budget_tokens}
if request.reasoning_effort is not None:
    kwargs["reasoning_effort"] = request.reasoning_effort
```

---

## 6. Implementation Stages & Work Breakdown

| Stage | Files Impacted | Deliverables | Verification |
| :--- | :--- | :--- | :--- |
| **Stage 1** | `specifications/interfaces/llm.py`<br />`packages/llm-gateway/src/llm_gateway/gateway.py` | Add `thinking_budget_tokens` and `reasoning_effort` pass-through to LiteLLM. | `pytest tests/unit/test_llm_gateway.py` |
| **Stage 2** | `packages/agent-core/src/agent_core/tools/clamp.py`<br />`packages/agent-core/src/agent_core/tools/tier2.py` | Deterministic head/tail output clamping on `bash_exec` and `file_read`. | Unit test with 10k-line stdout. |
| **Stage 3** | `packages/agent-core/src/agent_core/context.py` | Tool-payload pruning & invariant anchor compaction. Safe `contextvars` execution context. | Unit test verifying plans are preserved after compaction. |
| **Stage 4** | `packages/agent-core/src/agent_core/dispatcher.py`<br />`packages/agent-core/src/agent_core/interaction.py`<br />`packages/agent-core/src/agent_core/orchestrator.py` | Extract `ToolDispatcher` and `DecisionGateManager`. Decouple `AsyncReActOrchestrator`. | Parallel tests & concurrency tests. |
| **Stage 5** | `tests/unit/`, `tests/integration/`, `apps/web/e2e/` | Add unit tests for compaction, concurrency safety, and Playwright E2E for long sessions. | Full test suite passes. |
| **Stage 6** | `AGENTS.md`<br />`doc/architecture/orchestrator.md` | Update engineering guidelines and architecture docs. | `pnpm run docs:check` |

---

## 7. Testing & Verification Plan

### Unit Tests (`tests/unit/`)
1. **`test_context_hygiene.py`**:
   - Verify `clamp_output` preserves the first 80 lines and last 200 lines while trimming intermediate bloat.
   - Verify Tier 2 payload pruning replaces old tool results with lightweight receipts while keeping message counts and `tool_call_id` parity intact.
   - Verify the initial user goal and task checklist are never dropped or corrupted.
2. **`test_concurrency_safety.py`**:
   - Spawn two concurrent sessions on the same `AsyncReActOrchestrator` instance.
   - Verify that subagents and tool events from Session 1 never bleed into Session 2's event queue.
3. **`test_frontier_thinking_parameters.py`**:
   - Verify `thinking_budget_tokens` and `reasoning_effort` map cleanly into LiteLLM calls.

### Integration & E2E Tests
1. **`tests/integration/test_heavy_coding_orchestration.py`**:
   - Run a simulated 12-turn ReAct coding session generating 200KB of compilation and test output.
   - Assert the session completes successfully and the final token payload stays under bounds.
2. **`apps/web/e2e/mcp_and_subagents.spec.ts`**:
   - Ensure the UI correctly displays truncated outputs with clean visual affordances and continues streaming subagent cards.

---

## 8. Documentation Updates (`AGENTS.md` & `doc/`)

### Rule Additions in `AGENTS.md`:
* **Section 1.D: Context Hygiene Standard**:
  - Tools must never emit unbounded raw stdout/stderr directly into session memory.
  - Historical tool payloads must be pruned at the 70% threshold; implementation plans and assistant reasoning must remain anchored.
* **Section 1.E: Zero Concurrency Collisions**:
  - Prohibit mutable closures on shared singletons; use Python `contextvars` for per-turn execution scope.

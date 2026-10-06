# `agent-core`

Autonomous ReAct Agent Engine, Code Editor, and Two-Tier Tool Server for the Rocket Chat platform.

## Overview

This package implements the core agent execution layer as defined in `specifications/03_AGENT_ENGINE_AND_TOOLS.md` and `specifications/interfaces/agent.py`.

### Components

1. **Async ReAct Orchestrator (`agent_core.orchestrator`):**
   - Pure Python `asyncio` execution loop.
   - Pydantic v2 event streaming (`AgentEvent`, `ReasoningDelta`, `ToolExecution`, `FileDiff`, `InteractiveQuestion`).
   - Interactive Decision Gating: Suspends execution in `PAUSED_FOR_INPUT` when an `InteractiveQuestion` is raised and resumes seamlessly upon receiving `submit_question_answer`.
   - Dynamic prompt injection, context compaction, and loop termination detection.

2. **Resilient AST/Block Code Editor (`agent_core.editor`):**
   - Whitespace/indentation-tolerant multi-line block replacement (`replace_block`).
   - Ambiguity prevention ensuring edits only match unique target blocks.
   - Unified diff generation producing structured `FileDiff` telemetry events.

3. **Two-Tier FastMCP Tool Architecture (`agent_core.tools`):**
   - **Tier 1 (In-Process Tools):**
     - `web_fetch`: Async HTTP request with HTML-to-Markdown conversion and noise removal.
     - `web_search`: Async web search with structured snippet retrieval.
   - **Tier 2 (Sandbox Tools):**
     - `bash_exec`: Dispatches commands to `SandboxDriverProtocol` with stdout/stderr capture.
     - `file_read`: Reads file contents with line range slicing support.
     - `file_write`: Atomically creates or overwrites files in the sandbox workspace.
     - `file_edit`: Performs targeted block replacements and emits file diffs.
     - `apply_patch`: Applies unified diff patches in-place.
     - `ask_question`: Emits interactive decision gates pausing the orchestrator.

## Installation

```bash
uv sync
```

## Running Tests

```bash
# Unit tests
uv run pytest tests/unit/test_agent_tools.py tests/unit/test_editor.py -v

# Integration tests
uv run pytest tests/integration/test_react_orchestrator.py -v
```

## Environment Variables

- `ROCKET_SERPAPI_API_KEY`: (Optional) Custom API key for `web_search` tool execution.

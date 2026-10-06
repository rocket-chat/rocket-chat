# Engineering & Agent Collaboration Guidelines (`AGENTS.md`)

This document establishes the strict engineering, architectural, and operational rules for **AI agents** and **human contributors** developing the **Rocket Chat** platform. 

Every contribution must maintain high software craftsmanship, long-term maintainability, and architectural fidelity.

---

## 1. Core Engineering Principles

### A. Readability & Maintainability Above Cleverness
* **Code is written for humans first.** Write clear, idiomatic, self-documenting code.
* Prefer explicit variable names and descriptive function names over abbreviated jargon.
* Functions must follow the **Single Responsibility Principle** (SRP). If a function exceeds 40 lines or does more than one distinct task, decompose it into private helper functions.
* Keep dependency graphs clean: `apps/` may import from `packages/`, but `packages/` must **never** import from `apps/` or create circular dependencies.

### B. The Anti-Slop Commenting Standard
AI-generated code frequently suffers from "AI slop comments"—meaningless, redundant annotations that state the obvious and clutter review diffs.

#### ❌ FORBIDDEN (AI Slop):
```python
# Bad: Obvious narrative comments that restate the code
# Increment count by 1
count += 1

# Function to get user by id
def get_user(user_id: str):
    # Fetch user from db
    user = db.query(user_id)
    # Return user
    return user
```

#### ✅ REQUIRED (High-Value Technical Documentation):
* **Self-Documenting Code:** Code that is clean enough that comments are rarely needed.
* **Comment the "WHY", Not the "WHAT":** Reserve comments exclusively for:
  1. Non-obvious architectural decisions or trade-offs.
  2. Race condition mitigations or concurrency locks.
  3. Workarounds for third-party library quirks, cloud CSI storage delays, or upstream API bugs.
  4. Mathematical or cryptographic formulas.
```python
# Good: Explains a critical non-obvious infrastructure trade-off
# Pin pod to the previous node to bypass cloud EBS detachment latency (~90s).
# If the target node is unschedulable, Kubernetes will fall back to dynamic rescheduling.
node_selector = {"kubernetes.io/hostname": previous_node_name}
```

### C. The "No Quick Wins" Mandate
* **Zero Fragile Hacks:** Never implement a quick one-line patch or shortcut that creates tech debt.
* **Generic & Sustainable Architecture:** Every feature must be built according to the contracts defined in `specifications/interfaces/`. If an abstraction is missing, extend the interface cleanly.
* **Strict Type Safety:**
  - Python: Enforce strict `mypy` / `pyright`. Do not use `Any` as an escape hatch. Use generic type variables (`TypeVar`), Pydantic models, or `TypedDict`.
  - TypeScript: Strict mode enabled (`noImplicitAny: true`). Prohibit `@ts-ignore` or `any` unless wrapping an untyped external library (and document why).

### D. Context Hygiene Standard
* **Deterministic Clamping:** Tools must never emit unbounded raw stdout/stderr directly into session memory. Apply head/tail output clamping (`clamp_output`) on file reads and bash execution to preserve critical diagnostic outputs (startup and errors) while dropping intermediate bloat.
* **3-Tier Compaction:** Historical tool payloads must be pruned at the 70% context limit into lightweight receipts. Implementation plans, structured task checklists, system prompts, and assistant reasoning blocks must remain anchored and never dropped.

### E. Zero Concurrency Collisions
* **Immutable Closure Scope:** Prohibit mutable callbacks on shared singleton registries across user turns. Use standard Python `contextvars` (`current_execution_ctx`) to scope session IDs, active event queues, and parent model configurations to the executing task.

---

## 2. Technology Stack & Language Standards

### Backend (Python 3.12+)
* **Tooling:** All dependencies and environments are managed with **`uv`**. Never run raw `pip install`.
* **Formatting & Linting:** Strict adherence to **`ruff`** (`ruff check --fix` and `ruff format`).
* **Async Concurrency:**
  - Standardize on `asyncio`.
  - Never call blocking synchronous I/O functions (`time.sleep()`, synchronous `requests`, raw file read/write) inside async paths. Use `asyncio.sleep()`, `httpx.AsyncClient()`, or `anyio.to_thread.run_sync()`.
  - Always enforce explicit timeouts on external network calls (Docker daemon, K8s API, LiteLLM).

### Frontend (Next.js 15, TypeScript, Tailwind)
* **Design Guidelines:** The user interface follows a modern, polished dark/light pair-programming cockpit theme (Rocket Chat). Note: "Rocket" is purely a visual theme and brand identity, NOT the domain of the AI. The default autonomous agent is a versatile, senior **General Software Engineer** equipped with full access to terminal execution, surgical block editing, unified patch application, web lookups, and task checklist management.
* **Component Architecture:** Clean, highly accessible layout with real-time streaming, collapsed-by-default tool reasoning blocks, predictive `[Tab ⇥]` autocompletion for probable next prompts, and native system-matching dark/light modes.
* **Component Library:** Use modern Tailwind CSS and **lucide-react**. Keep components atomic, composable, and accessible.

---

## 3. Living Documentation Standard

Documentation must evolve **continuously alongside code development**, not as an afterthought.

1. **Every package and application must have a `README.md`** detailing:
   - What the component does.
   - How to install its dependencies (`uv sync` or `pnpm install`).
   - How to run its unit and integration test suite.
   - Required environment variables.
2. **Architecture & User Documentation (`doc/`):**
   - The central documentation website is maintained in Markdown under `doc/` using **Mintlify** (`doc/mint.json`).
   - If an implementation detail requires altering a protocol or data flow, update the corresponding documentation file in `doc/` before or in the same commit as the code change.
   - Machine-readable context (`/llms.txt` and `/llms-full.txt`) is indexed directly in `doc/ai/llms-index.md` and accessible for LLMs.
3. **API & Contract Documentation:**
   - FastAPI routes must include clean docstrings and Pydantic response models to ensure the generated OpenAPI (`/docs`) is comprehensive and accurate.
   - Core interfaces and data models must be maintained in `specifications/interfaces/`.

---

## 4. Delivery & CI/CD Workflow

All code must be deliverable and packaged for real-world deployment:

* **Local Packaging (`deploy/docker/`):**
  - Maintain clean, minimal multi-stage `Dockerfile.backend` and `Dockerfile.frontend`.
  - Maintain `deploy/docker-compose.yml` (production images) and `deploy/docker-compose.dev.yml` (local development mode).
* **Cloud Packaging (`deploy/helm/platform`):**
  - Maintain the official Helm chart.
  - Ensure cloud storage classes (AWS `gp3`, Azure `managed-csi`, GCP `pd-balanced`) remain up to date.
* **Automated CI Checks:**
  - Every pull request must pass:
    ```bash
    # Backend
    uv run ruff check .
    uv run ruff format --check .
    uv run mypy packages apps/api
    uv run pytest tests/
    
    # Frontend
    pnpm --filter web lint
    pnpm --filter web type-check
    pnpm --filter web build
    
    # Helm & Docs
    helm lint deploy/helm/platform
    pnpm run docs:check
    ```

---

## 5. Agent Operating Protocol & User Alignment

When an AI agent (like Antigravity) works on this codebase, it must adhere to the following protocol:

1. **Documentation and Interfaces are the Single Source of Truth:**
   - Always refer to `doc/*.md` and `specifications/interfaces/*.py` before designing or writing any implementation.
2. **Proactive Clarification (Do Not Guess):**
   - If a requirement is ambiguous, if a library introduces unexpected breaking changes, or if an architectural trade-off must be made: **PAUSE AND ASK THE USER**. Never make silent, uncoordinated architectural changes.
3. **Test-Driven Verification:**
   - Never declare a feature or phase complete without providing and executing automated tests (`pytest` for backend, unit/type checks for frontend).
   - Verify changes against both the **Docker driver** (local) and **K8s driver** (cluster) mental models.
4. **Clean Git Hygiene:**
   - Atomic, well-scoped commits with conventional commit messages (`feat:`, `fix:`, `refactor:`, `test:`, `docs:`).
   - Always append proper `Co-authored-by:` trailers as outlined in `doc/architecture/git-engine.md`.

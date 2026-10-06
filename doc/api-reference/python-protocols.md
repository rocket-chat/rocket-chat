# Core Python Protocol Interfaces

All internal packages and plugins in Rocket Chat communicate through strict `typing.Protocol` interfaces defined in `specifications/interfaces/`.

---

## 1. Sandbox Protocol (`specifications.interfaces.sandbox`)

Defines execution environments (Docker containers, Kubernetes Pods):

```python
class SandboxDriverProtocol(Protocol):
    async def create_sandbox(self, spec: WorkspaceSpec) -> SandboxStatus:
        """Provision container/pod and persistent storage."""
        ...

    async def start_sandbox(self, session_id: str) -> None:
        """Resume execution from hibernation."""
        ...

    async def stop_sandbox(self, session_id: str) -> None:
        """Hibernate execution runtime while preserving storage."""
        ...

    async def delete_sandbox(self, session_id: str) -> None:
        """Tear down sandbox and destroy persistent storage."""
        ...

    async def execute_command(self, session_id: str, cmd: str, timeout: int = 60) -> ExecResult:
        """Execute non-interactive shell command."""
        ...

    async def stream_command(self, session_id: str, cmd: str) -> AsyncIterator[str]:
        """Stream stdout and stderr chunks in real time."""
        ...

    async def read_file(self, session_id: str, path: str) -> str:
        """Read file contents from the sandbox workspace."""
        ...

    async def write_file(self, session_id: str, path: str, content: str) -> None:
        """Write file contents to the sandbox workspace."""
        ...
```

---

## 2. LLM Gateway Protocol (`specifications.interfaces.llm`)

Defines universal model provider routing and streaming:

```python
class LLMGatewayProtocol(Protocol):
    async def complete(self, request: ModelRequest) -> ModelResponse:
        """Execute non-streaming completion."""
        ...

    async def stream(self, request: ModelRequest) -> AsyncIterator[StreamChunk]:
        """Stream completion tokens, tool calls, and thoughts."""
        ...
```

---

## 3. Agent Orchestrator Protocol (`specifications.interfaces.agent`)

Defines the ReAct state machine:

```python
class AgentOrchestratorProtocol(Protocol):
    async def run_turn(
        self,
        session_id: str,
        user_prompt: str,
        session_history: list[ChatMessage],
        org_id: str | None = None,
    ) -> AsyncIterator[AgentEvent]:
        """Execute an autonomous ReAct loop yielding telemetry events."""
        ...

    async def submit_decision(
        self,
        session_id: str,
        question_id: str,
        selected_options: list[str],
        write_in_response: str | None = None,
    ) -> None:
        """Submit human-in-the-loop decision to resume paused execution."""
        ...
```

---

## 4. Git Engine Protocol (`specifications.interfaces.git`)

Defines version control, commit signing, and co-authorship:

```python
class GitEngineProtocol(Protocol):
    async def commit_changes(self, session_id: str, spec: CommitSpec) -> str:
        """Stage, format co-authors, cryptographically sign, and commit."""
        ...

    async def get_diff(self, session_id: str, staged_only: bool = False) -> str:
        """Return unified diff of workspace changes."""
        ...
```

---

## 5. Config Engine Protocol (`specifications.interfaces.config`)

Defines 4-tier policy evaluation and constraint checking:

```python
class ConfigEngineProtocol(Protocol):
    def resolve_config(
        self,
        org_policy: OrgPolicy | None = None,
        team_policy: TeamPolicy | None = None,
        user_override: UserOverride | None = None,
    ) -> ResolvedConfig:
        """Cascade resolution and validate constraints, raising PolicyViolationError on violations."""
        ...
```

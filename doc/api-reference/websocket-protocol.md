# WebSocket Streaming Protocol

For real-time telemetry, low-latency log streaming, and immediate decision gate interaction, Rocket Chat provides a bidirectional WebSocket protocol.

---

## 1. Connection Handshake

Connect to the session's WebSocket channel:

```text
ws://<host>:8000/ws/sessions/{session_id}?token=<jwt-auth-token>
```

Upon connection, the server sends a confirmation handshake:
```json
{
  "type": "connection_ack",
  "session_id": "sess-9a8b7c6d",
  "status": "connected"
}
```

---

## 2. Server-to-Client Telemetry Events

All events emitted by the backend follow the `AgentEvent` schema:

### A. Internal Thinking Stream (`thought`)
Streams the model's Chain-of-Thought reasoning tokens in real time:
```json
{
  "type": "thought",
  "delta": "Checking file tree to locate test configuration..."
}
```

### B. Tool Invocation (`tool_call`)
Notifies the client that an autonomous tool is about to be executed:
```json
{
  "type": "tool_call",
  "tool_name": "replace_file_content",
  "parameters": {
    "TargetFile": "/workspace/src/auth.py",
    "StartLine": 45,
    "EndLine": 52,
    "TargetContent": "...",
    "ReplacementContent": "..."
  }
}
```

### C. Live Terminal Logs (`tool_output`)
Streams ANSI-colored stdout and stderr chunks for the `@xterm/xterm` log viewer:
```json
{
  "type": "tool_output",
  "stream": "stdout",
  "chunk": "\u001b[32mPASSED\u001b[0m tests/unit/test_auth.py::test_token\n"
}
```

### D. File Diffs (`diff`)
Emits side-by-side git diff payloads for Monaco DiffEditor:
```json
{
  "type": "diff",
  "file_path": "src/auth.py",
  "original": "def verify():\n    return False\n",
  "modified": "def verify():\n    return True\n"
}
```

### E. In-Stream Decision Gates (`decision_gate`)
Prompts the human engineer for clarification:
```json
{
  "type": "decision_gate",
  "gate_type": "InteractiveQuestion",
  "question_id": "q-101",
  "question": "Which signing mechanism should be used for this commit?",
  "options": [
    "(Recommended) GitHub App Verified Badge",
    "GPG Key Injection"
  ],
  "is_multi_select": false
}
```

---

## 3. Client-to-Server Messages

### A. Submitting a Turn
```json
{
  "type": "turn_input",
  "prompt": "Run the integration tests and fix any failing assertions."
}
```

### B. Answering a Decision Gate
```json
{
  "type": "decision_response",
  "question_id": "q-101",
  "selected_options": ["(Recommended) GitHub App Verified Badge"]
}
```

## `plan_updated`

Emitted when the agent updates its task checklist. Payload: `{"session_id": "...", "tasks": [{"id": "1", "title": "...", "status": "in_progress"}]}`. The UI renders it as a collapsible "Tasks" panel pinned at the top of the conversation.

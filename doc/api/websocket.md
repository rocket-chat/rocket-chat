# WebSocket Streaming Protocol

For real-time telemetry, low-latency log streaming, and immediate decision gate interaction, Rocket Chat provides a bidirectional WebSocket protocol.

---

## 1. Connection Handshake

Connect to the session's WebSocket channel:

```text
ws://<host>:8000/v1/sessions/{session_id}/ws?session_id={session_id}[&token=<jwt-auth-token>]
```

Upon connection the server rehydrates the session and emits a state sync frame:

```json
{
  "type": "sync",
  "session_id": "sess-9a8b7c6d",
  "session": {
    "session_id": "sess-9a8b7c6d",
    "title": "Mission Alpha",
    "status": "idle"
  },
  "running_turn": null,
  "last_seq": 411,
  "catch_up": null
}
```

`catch_up` is `null` for an idle session. When a turn is still running (or ended while the
session record was being read) it is `{"from_seq": 398, "events": [...]}`: the events the
record cannot hold yet, to apply on top of `session`. `running_turn` provides the in-flight turn metadata and its completed steps (`steps` list with `id`, `name`, `arguments`, `result`, `status`, `duration_ms`, `redacted`, `seq`, and `reasoning`: what the model thought before the call, on the first call of each step) so a client connecting or refreshing mid-turn renders the full flight log without waiting for the turn to complete. Events are idempotent by `seq`, and
tokens of an answer that is already stored are ignored, so overlap with the record is safe. A client that applies a `sync` with a `catch_up` must rewind its resume cursor to `from_seq` first: the snapshot replaced the transcript, so anything it had applied beyond that point has to be applied again.

A turn is over when a `status_changed` event with `"final": true` arrives (completed, or error). The orchestrator also reports `completed` earlier, before the answer is stored, which is why that one does not carry the flag.

The cockpit consumes this frame to repopulate its store after a reconnect, and stores
`last_seq` so it can ask for exactly the events it missed (see §3 F). The server closes
with code `1008` when the session does not exist or the caller fails the session ACL.

---

## 2. Server-to-Client Telemetry Events

Every telemetry event is pushed with a single envelope:

```json
{
  "event_type": "reasoning_token",
  "session_id": "sess-9a8b7c6d",
  "payload": { "...": "event-specific fields" },
  "timestamp_ms": 1735689600000,
  "seq": 412
}
```

`event_type` mirrors the `EventType` enum in `specifications/interfaces/events.py`
and is the discriminant the cockpit switches on. `seq` is a **monotonic per-session
sequence number** assigned by the event bus; clients store the last one they applied and
send it back on reconnect (see §3 F) so nothing published while they were away is lost.
Only control frames (`sync` in §1, `replay` and the `pong` reply) carry a `type` field.

### A. Internal Thinking Stream (`reasoning_token`)
Streams the model's Chain-of-Thought reasoning tokens in real time:
```json
{
  "event_type": "reasoning_token",
  "payload": { "delta": "Checking file tree to locate test configuration..." }
}
```

### B. Tool Invocation (`tool_started` / `tool_completed`)
Announces a tool about to run, then reports its result:
```json
{
  "event_type": "tool_started",
  "payload": {
    "id": "call_7d1a",
    "name": "file_edit",
    "arguments": { "target_file": "/workspace/src/auth.py", "start_line": 45 }
  }
}
```
```json
{
  "event_type": "tool_completed",
  "payload": {
    "id": "call_7d1a",
    "name": "file_edit",
    "status": "success",
    "result": "Updated /workspace/src/auth.py",
    "duration_ms": 412
  }
}
```

### C. Live Terminal Logs (`log_chunk`)
Streams ANSI-colored stdout and stderr chunks for the `@xterm/xterm` log viewer:
```json
{
  "event_type": "log_chunk",
  "payload": {
    "text": "\u001b[32mPASSED\u001b[0m tests/unit/test_auth.py::test_token\n"
  }
}
```

### D. File Diffs (`file_diff`)
Emits side-by-side git diff payloads for the Monaco DiffEditor:
```json
{
  "event_type": "file_diff",
  "payload": {
    "path": "src/auth.py",
    "diff_content": "@@ -1,2 +1,2 @@",
    "additions": 1,
    "deletions": 1,
    "is_new_file": false
  }
}
```

### E. Status, Plans, Subagents, Synthesis, and Errors
The remaining catalogue uses the same envelope: `token`, `status_changed`,
`plan_updated`, `post_failure_synthesis`, `subagent_started`,
`subagent_progress`, `subagent_completed`, `followup_suggestion`,
`session_updated`, `sandbox_state` (payload: `state`, `message`; `state: "failed"` means the sandbox could not start and `message` says why, for example an image that cannot be pulled), `decision_submitted`, `user_message`, `process_started`,
`process_exited` (payload: `process_id`, `name`, `command`, `state`, `port`, `exit_code`), `github_event` (activity on a pull request the session follows; payload: `kind`, `repo`, `pr_number`, `summary`, `outcome`, `url`, `actor`, `at`, `woke`), `context_compacted` (payload: `tokens_before`, `tokens_after`, `compacted_messages_count`, `summary`), `goal_updated` (payload: `goal_id`, `description`, `status`, `consumed_tokens`, `consumed_cost_usd`, `continuation_count`, `max_continuations`, `stop_condition`), `continuation_scheduled` (payload: `continuation_index`, `max_continuations`, `reason`, `goal_id`), `wait_started` (payload: `wait_id`, `wait_type`, `target`, `timeout_seconds`), `wait_completed` (payload: `wait_id`, `wait_type`, `target`, `status`, `payload`), `artifact_created` (payload: `artifact_id`, `title`, `kind`, `path`), `artifact_updated` (payload: `artifact_id`, `title`, `kind`, `path`), `task_started` (payload: `task_id`, `command`, `state`), `task_completed` (payload: `task_id`, `exit_code`, `status`), and `error`.

### E2. Replay After a Reconnect (`replay`)
Sent in answer to a client `resume` frame (§3 F). It carries every retained event
published after the client's cursor, so a socket that dropped mid-turn loses nothing:
```json
{
  "type": "replay",
  "session_id": "sess-9a8b7c6d",
  "continuous": true,
  "last_seq": 417,
  "events": [
    {
      "event_type": "token",
      "payload": { "delta": "The retry policy is..." },
      "seq": 412
    }
  ]
}
```
`continuous: false` means the client's cursor predates the retained replay window (or is
ahead of the server, e.g. after a restart), so the client must refetch
`GET /v1/sessions/{id}` instead of trusting an incomplete transcript. The replay window is
bounded by count per session; it is a reconnect buffer, not an audit log.

### F. In-Stream Decision Gates (`decision_required`)
Prompts the human engineer for clarification. Emitted by the orchestrator's
`interactive_question` event:
```json
{
  "event_type": "decision_required",
  "payload": {
    "question_id": "q-101",
    "question_text": "Which signing mechanism should be used for this commit?",
    "options": [
      "(Recommended) GitHub App Verified Badge",
      "GPG Key Injection"
    ],
    "is_multi_select": false,
    "default_recommended_option": "(Recommended) GitHub App Verified Badge"
  }
}
```
The cockpit renders `DecisionCard`; Slack renders Block Kit option buttons.

### G. Human Approval Gates (`approval_required`)
Emitted when the agent pauses for explicit human approval — entering/exiting plan
mode, or any sensitive action routed through the `request_approval` tool:
```json
{
  "event_type": "approval_required",
  "payload": {
    "action_id": "ap_3f9c21ab",
    "action_type": "git_revert_hard",
    "command_or_path": "Discard uncommitted work",
    "risk_level": "critical"
  }
}
```
The session enters `paused_for_input` and the turn stays suspended until a
decision arrives. The cockpit renders `ApprovalCard` (Approve / Reject); Slack
renders `approve_action` / `reject_action` Block Kit buttons. Approving resumes
the turn; rejecting returns an explicit denial to the model so it can choose a
safe alternative.

### H. Graceful Drain (`draining`)
When the pod is draining for a rollout, a newly accepted socket is told immediately,
then closed with code `1001` so the client reconnects to a healthy pod instead of hanging on a
connection that is about to die:
```json
{
  "type": "draining"
}
```

---

## 3. Client-to-Server Messages

### A. Submitting a Turn
```json
{
  "type": "turn",
  "content": "Run the integration tests and fix any failing assertions.",
  "model": "openrouter/deepseek/deepseek-v4.1-flash",
  "attachment_ids": ["att_1a2b3c4d5e6f7788"]
}
```
`attachment_ids` is optional and names files already uploaded through
`POST /v1/sessions/{session_id}/attachments`. They are attached to the **current turn
only**: the model receives image blocks or inlined text on this dispatch,
and the bytes are never re-sent on later turns, which keeps the provider's cached prefix
stable.

**While a turn is running**, a `turn` frame without attachments does not wait for it to end:
the running turn reads the message at its next step, as part of the conversation, and the
server broadcasts it as `user_message` (with the frame's `message_id`) at that moment. The
session history then reads in the order the agent saw it: the work before the message, the
message, the work after it. A message that arrives as the turn finishes, one with attachments,
or one sent while the turn runs on another replica, starts the next turn instead (after the
running one).

### B. Answering a Decision Gate
Handled by the `answer_question` frame; the payload fields mirror the pending
`decision_required` event:
```json
{
  "type": "answer_question",
  "question_id": "q-101",
  "selected_options": ["(Recommended) GitHub App Verified Badge"],
  "custom_text": null
}
```

### C. Interrupting / Canceling an Active Turn
Halts model reasoning and background tool execution immediately:
```json
{
  "type": "cancel"
}
```
The server aborts active tool tasks, emits `status_changed` with status `"cancelled"`, and stands by for the next message (a message sent during the turn that it had not read yet starts the next turn).

### D. Submitting an Approval Decision
Resolves a pending `approval_required` gate:
```json
{
  "type": "approval",
  "action_id": "ap_3f9c21ab",
  "approved": true
}
```
The equivalent REST endpoint is `POST /v1/sessions/{session_id}/approvals/{action_id}`.

### E. Keepalive
The cockpit sends a ping and expects the `{"type": "pong"}` control frame in
reply, which it uses to detect half-open connections:
```json
{
  "type": "ping"
}
```
`turn`/`action`, `cancel`/`interrupt`/`abort`, and the `share` and `terminal_input`
steering frames are the other accepted client messages.

### F. Resuming After a Reconnect
A client that reconnects sends the last `seq` it applied to receive exactly the events it
missed, instead of losing everything published in the gap:
```json
{
  "type": "resume",
  "last_seq": 411
}
```
The server answers with a control frame containing the missed suffix (see `replay` in §2),
after which live delivery continues on the same socket.

## `plan_updated`

Emitted when the agent updates its task checklist. Payload: `{"session_id": "...", "tasks": [{"id": "1", "title": "...", "status": "in_progress"}]}`. The UI renders it as a collapsible "Tasks" panel pinned at the top of the conversation.

## `post_failure_synthesis`

Emitted when autonomous execution reaches its maximum turn budget or encounters an error after gathering findings. Payload: `{"reason": "...", "max_turns": 30, "turns_taken": 30}`. The UI renders an in-stream amber pill notification in the flight stream while the agent synthesizes its concluding answer with tools disabled.

## `context_compacted`

Emitted when context window compaction runs to prune verbose tool results or summarize intermediate turns. Payload: `{"tokens_before": 145000, "tokens_after": 52000, "compacted_messages_count": 28, "summary": "..."}`. The UI renders a compaction milestone badge in the flight log.

## `wait_started`

Emitted when a turn suspends execution to wait for an asynchronous condition (e.g. timer, process exit, CI run, or PR review). Payload: `{"wait_id": "wait-...", "wait_type": "time", "target": "60", "deadline": 1735689900, "status": "waiting"}`. The UI renders an active wait indicator and tracks completion.

## `wait_completed`

Emitted when an asynchronous wait resolves, expires, or is cancelled. Payload: `{"wait_id": "wait-...", "wait_type": "time", "target": "60", "status": "completed", "summary": "..."}`. Triggers resumption of autonomous execution.

## `artifact_created` / `artifact_updated`

Emitted when an agent creates or updates a workspace deliverable artifact. Payload: `{"artifact_id": "...", "filename": "...", "title": "...", "artifact_type": "...", "version": 1}`. Pushed to the Cockpit Artifacts drawer and connected Slack channels.

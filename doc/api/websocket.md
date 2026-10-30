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
record cannot hold yet, to apply on top of `session`. `running_turn` provides the in-flight turn metadata, `current_reasoning` and `current_text` (what it has streamed since its last step: the thought in progress, and a preface or the answer so far), `seq` (the sequence those two cover through: every `token`/`reasoning_token` up to it is in them, none after it), and its completed steps (`steps` list with `id`, `name`, `arguments`, `result`, `status`, `duration_ms`, `redacted`, `seq`, and `reasoning`: what the model thought before the call, on the first call of each step) so a client connecting or refreshing mid-turn renders the full flight log without waiting for the turn to complete. Events are idempotent by `seq`, and
tokens of an answer that is already stored are ignored, so overlap with the record is safe. `catch_up` starts where the running turn began, so it also holds the events of the steps `running_turn` already lists: a client showing them skips the catch-up's `tool_started` for a listed call, and every `token`/`reasoning_token` up to `running_turn.seq` (or, without it, up to the highest step `seq`, the sequence just after that step's `tool_completed`), in the catch-up and among the live events that follow alike. On a replica that is not running the turn, `running_turn` holds the stored steps and the live part reported by the replica that is, and there may be no `catch_up`. A client that applies a `sync` with a `catch_up` must rewind its resume cursor to `from_seq` first: the snapshot replaced the transcript, so anything it had applied beyond that point has to be applied again.

A turn is over when a `status_changed` event with `"final": true` arrives (completed, or error). The orchestrator also reports `completed` earlier, before the answer is stored, which is why that one does not carry the flag. A failed turn's closing status, and its `error` event, carry `error_kind`: `provider` (keys, quota or an outage at the provider), `budget` (a spend limit), `model` (the model is unknown or not allowed), `sandbox` (the workspace), `access` (the sender may not use the session's agent) or `internal`, so a client can offer the right way out without reading the message.

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
A **subagent's own step** carries `subagent_id` and `parent_tool_call_id` (the parent's `delegate_subagent` call) on both events: clients nest it under that subagent's card and never count it as one of the turn's steps.

### C. Live Terminal Logs (`log_chunk`)
Streams ANSI-colored stdout and stderr chunks for the `@xterm/xterm` log viewer. A command's output arrives **while it runs**, a few times a second and whole lines at a time, already redacted; `tool_call_id` names the tool call it belongs to (the same id as in `tool_started` and `tool_completed`), so a client can show it under that step. It is absent for text that belongs to no call. A call that streamed its output does not repeat it in a final chunk; its `tool_completed` carries the complete (clamped) result. Streaming for one call stops after about 200,000 characters.
```json
{
  "event_type": "log_chunk",
  "payload": {
    "text": "\u001b[32mPASSED\u001b[0m tests/unit/test_auth.py::test_token\n",
    "tool_call_id": "call_3"
  }
}
```

### D. File Diffs (`file_diff`)
Reports one edit the agent made to a file, with its unified diff. It is also the cue to read the session's changes again (`GET /v1/sessions/{session_id}/changes`), which compares each file with where the work started and stays correct after a reload:
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
The remaining catalogue uses the same envelope: `token`, `status_changed` (payload: `status`, `message`, `context_tokens`, `context_limit`, `context_model_limit`, `context_target_tokens`, `context_source`, `context_tier2_tokens`, `context_tier3_tokens`, `step` (the model call a running turn is on, from 1), `error_kind` (on a failed turn's closing status), `turn_start` and `message_id` (on the first `running` status of a turn: where it begins, and the id of the message that started it when the sender gave one); while a step runs tools, `status: "running"` carries a short action such as `Reading files...` or `Running a command...`, never the model's own text, which arrives as `token` events; `context_tokens` and `context_limit` provide live context window gauge metrics with pruning and compaction thresholds),
`plan_updated`, `post_failure_synthesis`, `subagent_started` (payload: `subagent_id`, `role`, `name`, `task`, `parent_tool_call_id`, `model`, `isolation`, `background`),
`subagent_progress` (payload: `subagent_id`, `delta`, `is_reasoning`; merged, at most one event per 250 ms or 200 characters), `subagent_completed` (payload: `subagent_id`, `role`, `task`, `summary`, `status`: `completed`, `partial`, `blocked`, `failed` or `cancelled`, `duration_ms`, `turns_taken`, `tool_calls_count`, `tokens`, `cost`, `changed_files`, `artefacts`, `parent_tool_call_id`, `model`, `branch` holding its changes, and `report`: the whole structured report with `status`, `summary`, `findings` (`file`, `line`, `severity`, `evidence`), `changes` (`branch`, `diff_stat`, `files`), `open_questions`, `next_steps`, `data`), `followup_suggestion`,
`session_updated`, `sandbox_state` (payload: `state`, `message`; `state: "failed"` means the sandbox could not start and `message` says why, for example an image that cannot be pulled), `decision_submitted`, `user_message`, `message_pending` (payload: `message_id`, `delivery`, `content`, `author_user_id`), `message_cancelled` (payload: `message_id`), `process_started`,
`process_exited` (payload: `process_id`, `name`, `command`, `state`, `port`, `exit_code`), `github_event` (activity on a pull request the session follows; payload: `kind`, `repo`, `pr_number`, `summary`, `outcome`, `url`, `actor`, `at`, `woke`), `context_compacted` (payload: `tokens_before`, `tokens_after`, `compacted_messages_count`, `summary`, `summary_status`), `goal_updated` (payload: `goal_id`, `description`, `status`, `consumed_tokens`, `consumed_cost_usd`, `continuation_count`, `max_continuations`, `stop_condition`), `continuation_scheduled` (payload: `continuation_index`, `max_continuations`, `reason`, `goal_id`), `wait_started` (payload: `wait_id`, `wait_type`, `target`, `timeout_seconds`, `sha` for a CI wait on a named commit), `wait_completed` (payload: `wait_id`, `wait_type`, `target`, `status`, `payload`), `artifact_created` (payload: `artifact_id`, `title`, `kind`, `path`), `artifact_updated` (payload: `artifact_id`, `title`, `kind`, `path`), `task_started` (payload: `task_id`, `command`, `state`), `task_completed` (payload: `task_id`, `exit_code`, `status`), `budget_warning` (a spend limit that applies to the turn is nearly used; payload: `limit_id`, `subject`, `subject_id`, `period`, `mode`, `label`, `spent`, `amount`, `resets_at`), `budget_blocked` (a block limit refused the turn's next model call before anything was bought; payload: `limit_id`, `subject`, `period`, `spent`, `amount`, `resets_at`, `message`), `agent_access_denied` (the person who sent the message may not use the session's agent, never granted or revoked; payload: `agent_id`, `agent_name`, `message`), and `error` (payload: `error`, `error_kind`: `provider`, `budget`, `model`, `sandbox`, `access` or `internal`). `budget_blocked` and `agent_access_denied` come just before the turn's `error`.

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
The cockpit renders `DecisionCard` (radios, or checkboxes when `is_multi_select`, and an "Other" answer); Slack renders Block Kit option buttons. A question a subagent asks (only a role allowed to ask) adds `subagent_id` and `subagent_role`.

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
The payload also carries the structured call when there is one: `tool`, `arguments` (each value
cut at 2,000 characters), `diff` for an edit (cut at 20,000), `repository` (`owner/name`),
`remember_options` (`session`, `repository`: how a "yes" may be remembered, empty when none
applies), and `subagent_id` / `subagent_role` when a subagent needs the approval.
The session enters `paused_for_input` and the turn stays suspended until a
decision arrives. The cockpit renders `ApprovalCard` (Approve / Reject); Slack
renders `approve_action` / `reject_action` Block Kit buttons. Approving resumes
the turn; rejecting returns an explicit denial to the model so it can choose a
safe alternative.

A gate whose `action_type` is `execute_plan` comes from `exit_plan_mode`: the cockpit shows it
as a plan card (the proposed checklist, Approve / Edit / Reject and the permission mode the work
continues in) and answers it with `POST /v1/sessions/{id}/plan/decision`, not the `approval`
frame. When the organization turned on `general.plan_critic`, `command_or_path` ends with the
`reviewer` subagent's notes on the plan. Every participant then receives `plan_decided`:
```json
{
  "event_type": "plan_decided",
  "payload": {
    "action_id": "ap_3f9c21ab",
    "approved": true,
    "permission_mode": "auto_edit",
    "edited": false,
    "decided_by": "user_42"
  }
}
```
`permission_mode` is empty for a rejected plan; otherwise it is the session's mode from then on
(the running turn switches to it too).

### G3. Remembered Approvals (`approval_rules_changed`)
A person saved or revoked a remembered approval for the session (payload: `change`: `added` or
`revoked`, `rule_id`, `scope`: `session` or `repository`). Clients read the session's rules again
(`GET /v1/sessions/{id}/approval-rules`); the rules themselves are never sent on the socket.

### G2. Publishing From the Cockpit (`git_published`)
A person committed, pushed or opened a pull request from the Git tab (`POST
/v1/sessions/{id}/git/commit`, `/git/push`, `/git/pull-request`). Clients refresh their Git view
and show the line in the conversation; `summary` is what the publishing path answered (bounded),
`url` is set for a pull request:
```json
{
  "event_type": "git_published",
  "payload": {
    "action": "pull_request",
    "repository": "/workspace/api",
    "summary": "Pull request opened: https://github.com/acme/api/pull/42",
    "url": "https://github.com/acme/api/pull/42",
    "decided_by": "user_42"
  }
}
```

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
  "approved": true,
  "remember": "session"
}
```
`remember` (optional, with `approved: true`) is one of the gate's `remember_options`: the same
call is then answered for the rest of the session (`session`) or, for the same command in the
same repository, in every session of yours (`repository`).
The equivalent REST endpoint is `POST /v1/sessions/{session_id}/approvals/{action_id}`.

### E. Keepalive
The cockpit sends a ping and expects the `{"type": "pong"}` control frame in
reply, which it uses to detect half-open connections:
```json
{
  "type": "ping"
}
```
`turn`/`action`, `cancel`/`interrupt`/`abort`, `cancel_message`, `share` and `terminal_input`
are the other accepted client messages.

A `turn` frame sent while a turn of the session runs (on any replica) is kept until a turn takes
it, and announced with a `message_pending` event (`message_id`, `delivery`, `content`,
`author_user_id`). Its `delivery` says what happens to it:

* `"steer"` (the default): the running turn takes it at its next step as a new user message (a
  `user_message` event is broadcast), so the agent can change course without the turn being
  cancelled;
* `"queue"`: it runs after the turn, as a turn of its own.

A message the turn did not take, and every queued one, runs as the next turns, one each, in the
order they were sent. They are stored, so a restart or another replica loses none. A
`cancel_message` frame (`{"type": "cancel_message", "message_id": "..."}`, or
`DELETE /v1/sessions/{id}/queued-messages/{message_id}`) withdraws one no turn has taken yet; a
`message_cancelled` event (`message_id`) says so. A frame sent when no turn runs starts a turn.

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

## 4. The Session List Socket (`/v1/sidebar/ws`)

`ws://<host>/v1/sidebar/ws?token=<token>` follows where every session the caller may see stands,
for a list of conversations. It authenticates like the session socket and closes with `1008`
without credentials. Once it is listening it sends `{"type": "ready"}`; read the list
(`GET /v1/sessions`) then, since nothing from before is replayed. It then sends each
`session_updated` event of the caller's organization whose session the caller may open (the
same rule as the session socket, checked against the session as stored when the event is
relayed, so a session unshared since the socket opened is no longer reported):

```json
{
  "event_type": "session_updated",
  "session_id": "session_8f2a",
  "payload": { "session_id": "session_8f2a", "is_processing": false, "last_turn_status": "waiting" },
  "timestamp_ms": 1793191830000
}
```

The payload is the one documented in §2 E. It accepts `{"type": "ping"}` (answered with
`{"type": "pong"}`) and no other message.

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

Emitted when an agent creates or updates a deliverable artifact. Payload: `{"id": "art_...", "filename": "...", "title": "...", "artifact_type": "markdown", "version": 1, "content": "<first 500 characters>"}`; read the whole artifact, or an earlier version, with `GET /v1/sessions/{session_id}/artifacts/{id}` (`?version=N`; `GET …/versions` lists them). Pushed to the Cockpit Artifacts drawer and connected Slack channels.

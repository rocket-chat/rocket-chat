export type EventType =
  | "token"
  | "reasoning_token"
  | "status_changed"
  | "tool_started"
  | "tool_completed"
  | "subagent_started"
  | "subagent_progress"
  | "subagent_completed"
  | "file_diff"
  | "decision_required"
  | "decision_submitted"
  | "approval_required"
  | "log_chunk"
  | "sandbox_state"
  | "post_failure_synthesis"
  | "github_event"
  | "context_compacted"
  | "continuation_scheduled"
  | "goal_updated"
  | "wait_started"
  | "wait_completed"
  | "artifact_created"
  | "artifact_updated"
  | "error"
  | "user_message";

export interface ContextCompactedPayload {
  tokens_before: number;
  tokens_after: number;
  compacted_messages_count: number;
  summary?: string | null;
  /** "model", "model_retry", or "digest" when the summary was rebuilt from the tool history. */
  summary_status?: string;
}

export interface ContinuationScheduledPayload {
  continuation_index: number;
  max_continuations: number;
  reason: string;
  goal_id: string;
}

export interface GoalUpdatedPayload {
  goal_id: string;
  status: string;
  consumed_tokens: number;
  consumed_cost_usd: number;
  max_cost_usd?: number;
  max_total_tokens?: number;
  description?: string;
}

export interface WaitStartedPayload {
  wait_id: string;
  wait_type: string;
  target: string;
  timeout_seconds?: number | null;
  reason?: string | null;
}

export interface WaitCompletedPayload {
  wait_id: string;
  wait_type: string;
  target: string;
  status: string;
  summary?: string | null;
  payload?: Record<string, unknown>;
}

export interface ArtifactItem {
  id: string;
  session_id: string;
  title: string;
  filename: string;
  artifact_type: "markdown" | "diff" | "diagram" | "html" | "json" | string;
  version: number;
  content?: string;
  metadata?: Record<string, unknown>;
  created_at: number;
  updated_at: number;
}

/** A telemetry envelope as published on the session socket (see the WebSocket reference). */
export interface TelemetryFrame {
  event_type: string;
  session_id?: string;
  payload?: Record<string, unknown>;
  timestamp_ms?: number;
  seq?: number;
}

export interface RunningTurnData {
  turn_index: number;
  started_at: number;
  model?: string;
  status?: string;
  steps: Array<{
    id?: string;
    name: string;
    arguments?: Record<string, unknown>;
    result?: unknown;
    status: string;
    duration_ms?: number;
    redacted?: number;
    seq?: number;
    created_at?: number;
    /** The reasoning that led to this call (on the first call of its step). */
    reasoning?: string;
    /** Step intent text emitted immediately before this tool call. */
    preface?: string;
  }>;
  /** In-flight reasoning of the active step, recovered on refresh. */
  current_reasoning?: string;
  /** Text streamed since the last step (a preface or the answer so far), recovered on refresh. */
  current_text?: string;
  /** The sequence `current_reasoning` and `current_text` cover through. */
  seq?: number;
}

export interface SyncFrame {
  type: "sync";
  session_id?: string;
  session?: SessionData;
  last_seq?: number;
  /**
   * Events the session record may not reflect yet (a turn still running, or one that ended
   * while the record was being read), to be applied on top of `session`.
   */
  catch_up?: { from_seq: number; events: TelemetryFrame[] } | null;
  /** A human gate the turn is paused on, recovered for a tab that connected late or reloaded. */
  pending_approval?: Record<string, unknown> | null;
  pending_decision?: Record<string, unknown> | null;
  /** Running turn state and completed steps when a turn is actively in flight. */
  running_turn?: RunningTurnData | null;
}

export interface ReplayFrame {
  type: "replay";
  session_id?: string;
  continuous?: boolean;
  last_seq?: number;
  events?: TelemetryFrame[];
}

export interface FileDiffItem {
  path: string;
  diff_content: string;
  additions: number;
  deletions: number;
  is_new_file: boolean;
}

export interface DecisionQuestion {
  question_id: string;
  question_text: string;
  options: string[];
  is_multi_select: boolean;
  default_recommended_option?: string | null;
  /** Set when a subagent asks: its role. */
  subagent_role?: string;
}

/** How a "yes" may be remembered: for the session, or for the command in the repository. */
export type ApprovalRememberScope = "session" | "repository";

export interface ApprovalRequestInfo {
  action_id: string;
  action_type: string;
  description: string;
  risk_level: "low" | "medium" | "critical" | string;
  /** The structured call behind the gate (bounded by the server). */
  tool?: string;
  arguments?: Record<string, unknown>;
  diff?: string;
  repository?: string;
  remember_options?: ApprovalRememberScope[];
  /** Set when a subagent needs the approval: its role. */
  subagent_role?: string;
}

export interface SessionData {
  session_id: string;
  title: string;
  tenant_org_id: string;
  tenant_user_id: string;
  sandbox_status: string;
  is_processing?: boolean;
  source?: "web" | "slack" | string;
  agent_id?: string;
  inference_mode?: "agent" | "direct";
  selected_model?: string;
  container_image?: string;
  git_repo?: string;
  git_branch?: string;
  metadata?: Record<string, unknown>;
  created_at?: string;
  updated_at?: string;
  is_shared?: boolean;
  conversation_history?: Array<{
    role: string;
    content: string;
    reasoning?: string;
    tool_calls?: ToolCallData[];
    /** Who wrote a user message (shared sessions). */
    author_user_id?: string | null;
    /** On a user message: the agent (empty for a raw model) and model its turn ran with. */
    agent_id?: string | null;
    agent_name?: string | null;
    model?: string | null;
    created_at?: number | string | null;
  }>;
  collaborators?: string[];
  checklist?: ChecklistTask[];
  suggested_followup?: string | null;
  has_older_history?: boolean;
  total_messages?: number;
  /** Messages the person sent before the first one of ``conversation_history``. */
  user_turns_before?: number;
  is_draft?: boolean;
  /** Sidebar ergonomics: pinned sessions float to the top, archived hide. */
  is_pinned?: boolean;
  is_archived?: boolean;
  /** Provenance of a forked session: the parent it branched from. */
  forked_from?: { session_id: string; turn?: number | null } | null;
}

export type PermissionMode = "plan" | "ask" | "auto_edit" | "auto" | "bypass";

export type ChecklistTaskStatus = "pending" | "in_progress" | "completed" | "failed";

export interface ChecklistTask {
  id: string;
  title: string;
  status: ChecklistTaskStatus;
  description?: string;
}

export interface ToolCallData {
  id?: string;
  name: string;
  arguments?: Record<string, unknown>;
  result?: string;
  status?: "pending" | "success" | "error";
  duration_ms?: number;
  stdout?: string;
  stderr?: string;
  exit_code?: number;
  created_at?: number;
  /** The reasoning that led to this call (on the first call of its step). */
  reasoning?: string;
  /** Step intent text emitted immediately before this tool call. */
  preface?: string;
}

/**
 * One attachment uploaded through the composer. `preview` is a data URL
 * for image thumbnails; `status` makes an in-flight or failed upload visible instead of
 * showing a chip that silently never sent.
 */
export interface AttachmentInfo {
  attachmentId: string;
  name: string;
  mime: string;
  size: number;
  sha256: string;
  workspacePath: string;
  preview?: string | null;
  /** `staged`: chosen in a new chat, uploaded once its first message has created the session. */
  status: "uploading" | "staged" | "ready" | "error";
  error?: string;
  file?: File;
}

export interface ModelOption {
  id: string;
  name: string;
  context_window: number;
  provider: string;
  description?: string;
}

/** One thing a subagent found (its report's `findings`). */
export interface SubagentFinding {
  file: string;
  line: number | null;
  severity: "critical" | "high" | "medium" | "low" | "info" | string;
  evidence: string;
}

/** The structured result a subagent ends with (`SubagentReport` on the server). */
export interface SubagentReport {
  status: string;
  summary: string;
  findings: SubagentFinding[];
  changes: { branch: string | null; diff_stat: string; files: string[] } | null;
  open_questions: string[];
  next_steps: string[];
  data: Record<string, unknown> | null;
}

/** One of a subagent's own tool calls, nested under its card. */
export interface SubagentStep {
  id: string;
  name: string;
  arguments?: Record<string, unknown>;
  status: "pending" | "success" | "error";
  result?: string;
  duration_ms?: number;
}

export interface SubagentExecutionData {
  subagentId: string;
  role: string;
  task: string;
  /** running, completed, partial, blocked, error (failed) or cancelled. */
  status: "running" | "completed" | "partial" | "blocked" | "error" | "cancelled" | string;
  thought?: string;
  summary?: string;
  duration_ms?: number;
  tokens?: number;
  cost?: number;
  changed_files?: string[];
  artefacts?: string[];
  model?: string;
  isolation?: string;
  background?: boolean;
  /** The parent's `delegate_subagent` call the child runs under. */
  parentToolCallId?: string | null;
  branch?: string | null;
  report?: SubagentReport | null;
  steps?: SubagentStep[];
}

/** Who answered a turn: the agent's name (empty for a raw model) and the model. */
export interface TurnBinding {
  agentName: string;
  model: string;
}

export interface TurnErrorInfo {
  message: string;
  /** provider | budget | model | sandbox | access | internal (the server's `error_kind`). */
  kind: string;
  /** The instruction of the failed turn, to send again; null when it is not known. */
  prompt: string | null;
  /** The spend limit that refused the turn (`budget_blocked`). */
  limit?: BudgetBlockedInfo;
  /** The agent the person may not use (`agent_access_denied`). */
  agent?: { id: string; name: string };
}

export interface BudgetBlockedInfo {
  limit_id: string;
  subject: string;
  period: string;
  spent: number;
  amount: number;
  resets_at: number | null;
}

export interface FlightLogEntry {
  id: string;
  role: "USER" | "AGENT" | "PLANNER" | "CODER" | "TESTER" | "SYSTEM" | "SUBAGENT";
  content: string;
  timestamp: string;
  /**
   * Server-side identity of a user message. The originating tab stores the id it
   * optimistically inserted here so the echoed `user_message` event can be
   * recognised instead of rendered a second time.
   */
  messageId?: string;
  authorUserId?: string | null;
  /** On an answer restored from history: the agent and model of its turn. */
  answeredBy?: TurnBinding | null;
  attachments?: AttachmentInfo[];
  reasoning?: string;
  isReasoningActive?: boolean;
  toolCall?: ToolCallData;
  subagent?: SubagentExecutionData;
  /**
   * True when this entry was reconstructed from the persisted session history rather
   * than streamed live. Its content is already complete, so a replayed `token` /
   * `reasoning_token` event for the same turn must not be folded into it — that would
   * append the answer to itself after a reconnect whose snapshot already carried it.
   */
  isPersisted?: boolean;
  /** A durable wait the agent registered: one card that settles in place when the wait ends. */
  wait?: WaitEntry;
  /** A GitHub event on a followed pull request, kept apart so a wait card can show the latest. */
  githubEvent?: GithubEventEntry;
  /** A deliverable saved where no call of this conversation shows it (a subagent's, for one). */
  artifact?: ArtifactItem;
  /** The failure that ended a turn, with what is needed to retry it. */
  turnError?: TurnErrorInfo;
  /** A platform notice, kept as the event's fields so its card never parses `content` back. */
  notice?: SystemNotice;
  /** Position of the message in the stored conversation history (entries restored from it). */
  historyIndex?: number;
}

/**
 * What the platform tells the person in the conversation. `content` on the same entry stays a
 * plain-text rendering of it (for search and copy); the card is drawn from these fields.
 */
export type SystemNotice =
  | {
      kind: "compaction";
      compactedMessages: number;
      tokensBefore: number;
      tokensAfter: number;
      summary: string;
      /** The model's summary was unusable and the platform rebuilt it from the tool history. */
      rebuilt: boolean;
    }
  | { kind: "continuation"; index: number; max: number; reason: string }
  | { kind: "step_limit"; steps: number; reason: string }
  | { kind: "alert"; text: string }
  | { kind: "turn_failed"; message: string }
  | { kind: "sandbox_failed"; message: string }
  | { kind: "send_failed"; message: string }
  | { kind: "turn_stopped" }
  | { kind: "spend_warning"; label: string; spent: number; amount: number }
  | { kind: "cost"; costUsd: number; totalTokens: number; turns: number; calls: number }
  | { kind: "info"; text: string };

export interface WaitEntry {
  waitId: string;
  waitType: string;
  /** `owner/repo#42` for a GitHub wait, seconds for a timer, the process for an exit. */
  target: string;
  /** The commit whose checks are awaited. */
  sha?: string;
  timeoutSeconds: number | null;
  /** `waiting` until the server settles it, then the server's own status (completed, timeout…). */
  status: string;
  summary?: string | null;
}

export interface GithubEventEntry {
  /** `owner/repo#42`, or the repository alone for a branch. */
  target: string;
  headline: string;
  outcome: string;
  url: string;
}

/** One persisted turn trace as returned by `GET /v1/sessions/{id}/turns`. */
export interface TurnTrace {
  id: string;
  session_id: string;
  turn_index: number;
  started_at: number;
  ended_at: number | null;
  status: "completed" | "error" | "running" | "interrupted" | string;
  reasoning_summary: string | null;
  tool_calls: Array<Record<string, unknown>>;
  diff_paths: string[];
  approvals: Array<Record<string, unknown>>;
  /** Files the user attached to this turn, by reference (the bytes stay in the attachments store). */
  attachments?: Array<{ id: string | null; name: string; mime: string; size: number }>;
  tokens: Record<string, unknown>;
  error: string | null;
  resume_attempt?: number;
}

export interface TurnTracePage {
  session_id: string;
  total: number;
  limit: number;
  offset: number;
  turns: TurnTrace[];
}

export interface McpToolSchema {
  name: string;
  description: string;
  parameters_summary?: string;
}

export interface McpHeaderConfig {
  name: string;
  value: string;
  is_secret?: boolean;
}

export interface McpAuthConfig {
  auth_type: "none" | "api_key" | "bearer" | "custom_headers" | "oauth2" | "basic";
  api_key?: string;
  header_name?: string;
  header_prefix?: string;
  headers?: McpHeaderConfig[];
  oauth_token_url?: string;
  oauth_client_id?: string;
  oauth_client_secret?: string;
  oauth_scopes?: string[];
  basic_username?: string;
  basic_password?: string;
}

export interface McpServerConfig {
  id: string;
  name: string;
  transport: "stdio" | "http" | "sse" | "streamable_http" | "websocket";
  endpoint_or_command: string;
  scope?: "org" | "user";
  user_id?: string | null;
  built_in?: boolean;
  org_id?: string | null;
  guidance?: string;
  auth_type?: string;
  auth_config?: Record<string, unknown>;
  status: "connected" | "probing" | "error" | "offline" | "needs_sign_in";
  tools_count: number;
  last_probed?: string;
  is_builtin?: boolean;
  description?: string;
  tools?: McpToolSchema[];
  has_api_key?: boolean;
  api_key_fingerprint?: string | null;
  /** 'member': each member connects their own account to this organization server. */
  oauth_mode?: "org" | "member";
  oauth_status?: string | null;
  headers_preview?: Array<{ name: string; value: string; is_secret: boolean; member_override?: boolean }>;
  /** Command servers: a script that installs the program the command runs. */
  install_command?: string | null;
  /** Command servers: variables each member may set for themselves. */
  overridable_env?: string[];
  /** Of those, the ones the signed-in member has set. */
  member_env_set?: string[];
  has_oauth_secret?: boolean;
  /** Non-secret connection settings, so an edit starts from what is stored. */
  header_name?: string | null;
  header_prefix?: string | null;
  oauth_authorization_url?: string | null;
  oauth_token_url?: string | null;
  oauth_client_id?: string | null;
  oauth_scopes?: string[];
}

export type SubagentTier = "fast" | "default" | "frontier";
export type SubagentIsolation = "none" | "worktree" | "sandbox";
export type SubagentInherit = "agents_md" | "memory" | "checklist" | "skills";

export interface SubagentConfig {
  id: string;
  name: string;
  role_title: string;
  description: string;
  system_prompt: string;
  model?: string | null;
  max_turns: number;
  whitelisted_tools: string[];
  disallowed_tools?: string[];
  temperature: number;
  enabled: boolean;
  /** Model tier when `model` is empty. */
  tier?: SubagentTier;
  reasoning_effort?: string | null;
  /** Spend cap per run in USD (0: none). */
  max_cost_usd?: number;
  /** Wall-clock cap per run in seconds (0: none). */
  max_wall_seconds?: number;
  isolation?: SubagentIsolation;
  /** Whether its approvals and questions reach the person (else they are refused). */
  can_ask_user?: boolean;
  inherit?: SubagentInherit[];
  output_schema?: Record<string, unknown> | null;
  source?: "builtin" | "org" | "repository";
  built_in?: boolean;
  customized?: boolean;
}

export interface CustomSkillConfig {
  id: string;
  name?: string;
  title: string;
  description: string;
  body: string;
  scope?: "org" | "user";
  user_id?: string | null;
  org_id?: string | null;
  created_at?: string;
  updated_at?: string;
}

export interface KnowledgeBaseConfig {
  id: string;
  name: string;
  description?: string;
  scope?: "org" | "user";
  user_id?: string | null;
  org_id?: string | null;
  vector_index?: string;
  documents_count?: number;
  embedding_model?: string;
  hybrid_search?: boolean;
}

export interface AgentPersonaConfig {
  id: string;
  name: string;
  role_title: string;
  description?: string;
  system_prompt: string;
  model: string;
  whitelisted_tools?: string[];
  skill_ids?: string[];
  knowledge_base_ids?: string[];
  scope?: "org" | "user";
  user_id?: string | null;
  org_id?: string | null;
  built_in?: boolean;
  /** A built-in agent whose organization edit is in effect. */
  customized?: boolean;
  /** Whether this agent persona is enabled for cockpit and missions. */
  enabled?: boolean;
  icon?: string;
  /** Sandbox type its sessions start in when their creator picks none; the type holds the
   * image, CPU and memory. Empty: the organization's default. */
  sandbox_id?: string;
  temperature?: number;
  reasoning_effort?: "none" | "minimal" | "low" | "medium" | "high" | "xhigh" | "max";
  /** Steps per turn for this agent; empty inherits the organization's limit, its ceiling. */
  max_turns?: number | null;
  /** Which allowed tools every step carries; empty inherits the organization's setting. */
  /** Tools always loaded, by name or shell-style pattern. */
  active_tools?: string[];
  /** Default permission mode for sessions created with this persona. */
  default_permission_mode?: PermissionMode | null;
  /** The agent's own provider credentials (administrators bind organization agents). */
  credential_bindings?: CredentialBinding[];
  /** Refuse calls on any provider the agent has no binding for. */
  strict_binding?: boolean;
}

export interface CredentialBinding {
  provider: string;
  credential_id: string;
}


/** Whether command (stdio) MCP servers can run on this installation (the MCP runner pod). */
export interface McpRunnerStatus {
  configured: boolean;
  available: boolean;
  detail: string;
}

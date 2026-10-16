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
  | "error"
  | "user_message";

/** A telemetry envelope as published on the session socket (see the WebSocket reference). */
export interface TelemetryFrame {
  event_type: string;
  session_id?: string;
  payload?: Record<string, unknown>;
  timestamp_ms?: number;
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
}

export interface ApprovalRequestInfo {
  action_id: string;
  action_type: string;
  description: string;
  risk_level: "low" | "medium" | "critical" | string;
}

export interface SessionData {
  session_id: string;
  title: string;
  tenant_org_id: string;
  tenant_user_id: string;
  sandbox_status: string;
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
  }>;
  collaborators?: string[];
  checklist?: ChecklistTask[];
  suggested_followup?: string | null;
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
  status: "uploading" | "ready" | "error";
  error?: string;
}

export interface ModelOption {
  id: string;
  name: string;
  context_window: number;
  provider: string;
  description?: string;
}

export interface SubagentExecutionData {
  subagentId: string;
  role: string;
  task: string;
  status: "running" | "completed" | "error";
  turn?: number;
  thought?: string;
  summary?: string;
  duration_ms?: number;
}

/** Who answered a turn: the agent's name (empty for a raw model) and the model. */
export interface TurnBinding {
  agentName: string;
  model: string;
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
  guidance?: string;
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
  status: "connected" | "probing" | "error" | "offline";
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

export interface SubagentConfig {
  id: string;
  name: string;
  role_title: string;
  description: string;
  system_prompt: string;
  model?: string | null;
  max_turns: number;
  whitelisted_tools: string[];
  temperature: number;
  enabled: boolean;
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
  icon?: string;
  sandbox_image?: string;
  /** Sandbox type its sessions start in when their creator picks none; empty: the default. */
  sandbox_id?: string;
  cpu_limit?: string;
  memory_limit?: string;
  temperature?: number;
  thinking_budget_tokens?: number;
  reasoning_effort?: "low" | "medium" | "high";
  /** Steps per turn for this agent; empty inherits the organization's limit, its ceiling. */
  max_turns?: number | null;
  /** Which allowed tools every step carries; empty inherits the organization's setting. */
  tool_loading?: "all" | "on_demand" | "auto" | null;
  /** Tools always loaded, by name or shell-style pattern. */
  active_tools?: string[];
}


/** Whether command (stdio) MCP servers can run on this installation (the MCP runner pod). */
export interface McpRunnerStatus {
  configured: boolean;
  available: boolean;
  detail: string;
}

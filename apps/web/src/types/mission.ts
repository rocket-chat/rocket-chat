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
  | "log_chunk"
  | "sandbox_state"
  | "error";

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

export interface SessionData {
  session_id: string;
  title: string;
  tenant_org_id: string;
  tenant_user_id: string;
  sandbox_status: string;
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
  }>;
  collaborators?: string[];
  checklist?: ChecklistTask[];
  suggested_followup?: string | null;
}

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

export interface FlightLogEntry {
  id: string;
  role: "USER" | "AGENT" | "PLANNER" | "CODER" | "TESTER" | "SYSTEM" | "SUBAGENT";
  content: string;
  timestamp: string;
  reasoning?: string;
  isReasoningActive?: boolean;
  toolCall?: ToolCallData;
  subagent?: SubagentExecutionData;
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
  transport: "stdio" | "http" | "sse";
  endpoint_or_command: string;
  guidance?: string;
  auth_type?: string;
  status: "connected" | "probing" | "error" | "offline";
  tools_count: number;
  last_probed?: string;
  is_builtin?: boolean;
  description?: string;
  tools?: McpToolSchema[];
  has_api_key?: boolean;
  api_key_fingerprint?: string | null;
  headers_preview?: Array<{ name: string; value: string; is_secret: boolean }>;
  has_oauth_secret?: boolean;
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
  name: string;
  description: string;
  runtime: "python" | "node";
  is_active: boolean;
  signature: string;
  code: string;
}

export interface KnowledgeBaseConfig {
  id: string;
  name: string;
  vector_index: string;
  documents_count: number;
  embedding_model: string;
  hybrid_search: boolean;
}

export interface AgentPersonaConfig {
  id: string;
  name: string;
  role_title: string;
  description?: string;
  system_prompt: string;
  model: string;
  whitelisted_tools: string[];
  icon?: string;
  sandbox_image?: string;
  cpu_limit?: string;
  memory_limit?: string;
  temperature?: number;
  thinking_budget_tokens?: number;
  reasoning_effort?: "low" | "medium" | "high";
}


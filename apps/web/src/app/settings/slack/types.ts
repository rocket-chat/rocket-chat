export interface SlackRoute {
  name: string;
  description: string;
  agent_id: string;
  examples: string[];
  response_mode: string;
  enabled: boolean;
}

export interface SlackRouterConfig {
  classifier_agent_id: string;
  classifier_model?: string;
  routes: SlackRoute[];
  allow_dismiss: boolean;
  dismiss_description: string;
  min_confidence: number;
  fallback: string;
  sticky: boolean;
  context_messages: number;
}

export interface SlackBot {
  id: string;
  org_id: string;
  name: string;
  enabled: boolean;
  bot_token_fingerprint?: string | null;
  app_token_fingerprint?: string | null;
  team_id?: string | null;
  team_name?: string | null;
  bot_user_id?: string | null;
  app_id?: string | null;
  handle?: string | null;
  agent_id?: string | null;
  response_mode: "thread" | "private";
  respond_in_dms: boolean;
  listen_channels: string[];
  private_reaction: string;
  private_dm_result: boolean;
  router?: SlackRouterConfig | null;
  policy: Record<string, unknown>;
  created_at: number;
  updated_at: number;
}

export interface RouteDecisionItem {
  id: string;
  org_id: string;
  bot_id: string;
  channel_id: string;
  thread_ts: string;
  slack_user_id: string;
  excerpt: string;
  route: string;
  applied: string;
  confidence: number;
  reason: string;
  model: string;
  latency_ms: number;
  created_at: number;
}

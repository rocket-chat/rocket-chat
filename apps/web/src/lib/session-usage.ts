/** What a session cost, as `GET /v1/sessions/{id}/usage` reports it. */
export interface UsageTotals {
  calls: number;
  prompt_tokens: number;
  cache_read_tokens: number;
  cache_creation_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  cost_usd: number;
  own_key_cost_usd: number;
}

/** How a finished turn spent its time and context (`turn_traces.tokens.metrics`). */
export interface TurnMetrics {
  steps: number;
  model_ms: number;
  tool_ms: number;
  tool_calls: number;
  reasoning_chars: number;
  max_step_reasoning_chars: number;
  reasoning_limit_stops: number;
  overhead_tokens: number;
  overhead_system_tokens?: number;
  overhead_tool_tokens?: number;
  overhead_tools_sent?: number;
  context_window?: number;
  context_window_source?: string;
  shell_file_operations?: number;
  tool_loads: number;
  pushes: number;
  compactions: number;
  compactions_without_model_summary: number;
}

export interface TurnUsage extends UsageTotals {
  turn_index: number;
  started_at: number;
  ended_at: number | null;
  status: string;
  metrics?: TurnMetrics | null;
}

export interface ModelUsage extends UsageTotals {
  model: string;
}

export interface ActiveContextUsage {
  tokens: number;
  limit: number;
  model_limit?: number;
  target_tokens?: number;
  source?: string;
  tier2_tokens?: number;
  tier3_tokens?: number;
}

export interface SessionUsage {
  session_id: string;
  totals: UsageTotals;
  turns: TurnUsage[];
  models: ModelUsage[];
  before_first_turn: UsageTotals;
  active_context?: ActiveContextUsage | null;
}

/** 950 → "950", 12,400 → "12.4k", 3,100,000 → "3.1M". */
export function formatCount(value: number): string {
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(value >= 10_000_000 ? 0 : 1)}M`;
  if (value >= 10_000) return `${Math.round(value / 1000)}k`;
  if (value >= 1000) return `${(value / 1000).toFixed(1)}k`;
  return String(value);
}

const PROVIDER_NAMES: Record<string, string> = {
  openrouter: "OpenRouter",
  anthropic: "Anthropic",
  openai: "OpenAI",
  gemini: "Google",
  vertex_ai: "Vertex AI",
  bedrock: "Bedrock",
  azure: "Azure",
  mistral: "Mistral",
  deepseek: "DeepSeek",
  groq: "Groq",
  ollama: "Ollama",
};

/** "OpenRouter" for `openrouter/anthropic/claude-…`: the provider the call went through. */
export function providerName(modelId: string): string {
  const provider = modelId.includes("/") ? modelId.split("/")[0] : "";
  return PROVIDER_NAMES[provider] ?? (provider ? provider[0].toUpperCase() + provider.slice(1) : "");
}

/**
 * A short name for each model in a list: its last two segments, and the provider as well when two
 * models would otherwise read the same (the same Claude through OpenRouter and from Anthropic).
 */
export function modelLabels(modelIds: string[]): Map<string, string> {
  const short = (id: string) => id.split("/").slice(-2).join("/");
  const counts = new Map<string, number>();
  for (const id of modelIds) counts.set(short(id), (counts.get(short(id)) ?? 0) + 1);
  return new Map(
    modelIds.map((id) => {
      const name = short(id);
      const provider = providerName(id);
      return [id, (counts.get(name) ?? 0) > 1 && provider ? `${name} · ${provider}` : name];
    }),
  );
}

/** Dollars with the precision that matters at that size: $0.0042, $0.42, $12.34. */
export function formatUsd(value: number): string {
  if (value <= 0) return "$0";
  if (value >= 100) return `$${Math.round(value)}`;
  if (value >= 1) return `$${value.toFixed(2)}`;
  if (value >= 0.01) return `$${value.toFixed(3)}`;
  return `$${value.toFixed(4)}`;
}

/** The share of the prompt the provider served from its cache, as a whole percent. */
export function cacheHitPercent(totals: Pick<UsageTotals, "prompt_tokens" | "cache_read_tokens">): number {
  return totals.prompt_tokens > 0 ? Math.round((totals.cache_read_tokens / totals.prompt_tokens) * 100) : 0;
}

/** 45 s → "45s", 754 s → "12m", 4,000 s → "1h 7m". */
export function formatDuration(seconds: number): string {
  if (seconds < 60) return `${Math.max(0, Math.round(seconds))}s`;
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes}m`;
  return `${Math.floor(minutes / 60)}h ${minutes % 60}m`;
}

/** "38 steps · model 21m · tools 3m · 1 compaction", for a turn's time cell. */
export function describeTurnMetrics(metrics: TurnMetrics): string {
  const parts = [
    `${metrics.steps} step${metrics.steps === 1 ? "" : "s"}`,
    `model ${formatDuration(metrics.model_ms / 1000)}`,
    `tools ${formatDuration(metrics.tool_ms / 1000)}`,
  ];
  if (metrics.compactions) {
    parts.push(`${metrics.compactions} compaction${metrics.compactions === 1 ? "" : "s"}`);
  }
  if (metrics.compactions_without_model_summary) {
    parts.push(`${metrics.compactions_without_model_summary} rebuilt from tool history`);
  }
  return parts.join(" · ");
}

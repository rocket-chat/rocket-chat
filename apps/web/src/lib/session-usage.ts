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

export interface TurnUsage extends UsageTotals {
  turn_index: number;
  started_at: number;
  ended_at: number | null;
  status: string;
}

export interface ModelUsage extends UsageTotals {
  model: string;
}

export interface SessionUsage {
  session_id: string;
  totals: UsageTotals;
  turns: TurnUsage[];
  models: ModelUsage[];
  before_first_turn: UsageTotals;
}

/** 950 → "950", 12,400 → "12.4k", 3,100,000 → "3.1M". */
export function formatCount(value: number): string {
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(value >= 10_000_000 ? 0 : 1)}M`;
  if (value >= 10_000) return `${Math.round(value / 1000)}k`;
  if (value >= 1000) return `${(value / 1000).toFixed(1)}k`;
  return String(value);
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

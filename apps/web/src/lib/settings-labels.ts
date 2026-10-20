const WORDS: Record<string, string> = {
  usd: "(USD)",
  mcp: "MCP",
  sso: "SSO",
  pii: "PII",
  llm: "LLM",
  url: "URL",
  api: "API",
  ci: "CI",
  pr: "pull request",
  cpu: "CPU",
  id: "ID",
  ids: "IDs",
  chars: "characters",
  max: "Maximum",
};

/** "max_turn_cost_usd" -> "Maximum turn cost (USD)": a sentence, not a variable. */
export function humanize(key: string): string {
  const words = key.split("_").map((word) => WORDS[word] ?? word);
  const text = words.join(" ");
  return text.charAt(0).toUpperCase() + text.slice(1);
}

export function settingLabel(field: { key: string; label?: string }): string {
  return field.label || humanize(field.key);
}

import type {
  AgentPersonaConfig,
  CredentialBinding,
  PermissionMode,
  SubagentConfig,
  SubagentInherit,
  SubagentIsolation,
  SubagentTier,
} from "../../types/mission";

/** What only a subagent role has: its tier, tree, budgets and what it inherits. */
export interface SubagentFields {
  tier: SubagentTier;
  isolation: SubagentIsolation;
  max_cost_usd: number;
  max_wall_seconds: number;
  can_ask_user: boolean;
  inherit: SubagentInherit[];
  disallowed_tools: string[];
  /** The output schema as the person edits it (JSON text; empty for none). */
  output_schema: string;
}

export const DEFAULT_SUBAGENT_FIELDS: SubagentFields = {
  tier: "default",
  isolation: "none",
  max_cost_usd: 3,
  max_wall_seconds: 0,
  can_ask_user: false,
  inherit: ["agents_md", "memory", "checklist"],
  disallowed_tools: [],
  output_schema: "",
};

export type AgentKind = "agent" | "subagent";
export type ReasoningEffort = NonNullable<AgentPersonaConfig["reasoning_effort"]>;

/** One editable shape for an agent and a subagent role, so one editor serves both. */
export interface AgentDraft {
  kind: AgentKind;
  id: string;
  name: string;
  role_title: string;
  description: string;
  system_prompt: string;
  icon: string;
  model: string;
  reasoning_effort: ReasoningEffort | "";
  temperature: number | null;
  whitelisted_tools: string[];
  active_tools: string[];
  skill_ids: string[];
  knowledge_base_ids: string[];
  sandbox_id: string;
  max_turns: number | null;
  default_permission_mode: PermissionMode | "";
  credential_bindings: CredentialBinding[];
  strict_binding: boolean;
  enabled: boolean;
  built_in: boolean;
  customized: boolean;
  scope: "org" | "user";
  user_id: string | null;
  /** Set for a subagent role. */
  subagent?: SubagentFields;
}

export function fromAgent(agent: AgentPersonaConfig): AgentDraft {
  return {
    kind: "agent",
    id: agent.id,
    name: agent.name,
    role_title: agent.role_title,
    description: agent.description ?? "",
    system_prompt: agent.system_prompt,
    icon: agent.icon ?? "bot",
    model: agent.model ?? "",
    reasoning_effort: agent.reasoning_effort ?? "",
    temperature: agent.temperature ?? null,
    whitelisted_tools: agent.whitelisted_tools ?? [],
    active_tools: agent.active_tools ?? [],
    skill_ids: agent.skill_ids ?? [],
    knowledge_base_ids: agent.knowledge_base_ids ?? [],
    sandbox_id: agent.sandbox_id ?? "",
    max_turns: agent.max_turns ?? null,
    default_permission_mode: agent.default_permission_mode ?? "",
    credential_bindings: agent.credential_bindings ?? [],
    strict_binding: agent.strict_binding ?? false,
    enabled: agent.enabled !== false,
    built_in: !!agent.built_in,
    customized: !!agent.customized,
    scope: agent.scope === "user" || agent.user_id ? "user" : "org",
    user_id: agent.user_id ?? null,
  };
}

export function toAgent(draft: AgentDraft): AgentPersonaConfig {
  return {
    id: draft.id,
    name: draft.name,
    role_title: draft.role_title,
    description: draft.description,
    system_prompt: draft.system_prompt,
    icon: draft.icon,
    model: draft.model,
    reasoning_effort: draft.reasoning_effort || undefined,
    temperature: draft.temperature ?? undefined,
    whitelisted_tools: draft.whitelisted_tools,
    active_tools: draft.active_tools,
    skill_ids: draft.skill_ids,
    knowledge_base_ids: draft.knowledge_base_ids,
    sandbox_id: draft.sandbox_id,
    max_turns: draft.max_turns,
    default_permission_mode: draft.default_permission_mode || null,
    credential_bindings: draft.credential_bindings,
    strict_binding: draft.strict_binding,
    enabled: draft.enabled,
    built_in: draft.built_in,
    scope: draft.scope,
    user_id: draft.user_id,
  };
}

export function fromSubagent(role: SubagentConfig): AgentDraft {
  return {
    ...fromAgent({
      id: role.id,
      name: role.name,
      role_title: role.role_title,
      description: role.description,
      system_prompt: role.system_prompt,
      model: role.model ?? "",
      reasoning_effort: (role.reasoning_effort as ReasoningEffort | null | undefined) ?? undefined,
      whitelisted_tools: role.whitelisted_tools,
      temperature: role.temperature,
      max_turns: role.max_turns,
      enabled: role.enabled,
      built_in: role.built_in,
      customized: role.customized,
      scope: "org",
    }),
    kind: "subagent",
    subagent: {
      tier: role.tier ?? DEFAULT_SUBAGENT_FIELDS.tier,
      isolation: role.isolation ?? DEFAULT_SUBAGENT_FIELDS.isolation,
      max_cost_usd: role.max_cost_usd ?? DEFAULT_SUBAGENT_FIELDS.max_cost_usd,
      max_wall_seconds: role.max_wall_seconds ?? 0,
      can_ask_user: role.can_ask_user ?? false,
      inherit: role.inherit ?? DEFAULT_SUBAGENT_FIELDS.inherit,
      disallowed_tools: role.disallowed_tools ?? [],
      output_schema: role.output_schema ? JSON.stringify(role.output_schema, null, 2) : "",
    },
  };
}

/** The output schema the person wrote, or null when it is empty or not a JSON object. */
export function parsedOutputSchema(text: string): Record<string, unknown> | null {
  if (!text.trim()) return null;
  try {
    const parsed: unknown = JSON.parse(text);
    return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? (parsed as Record<string, unknown>) : null;
  } catch {
    return null;
  }
}

export function toSubagent(draft: AgentDraft): SubagentConfig {
  const fields = draft.subagent ?? DEFAULT_SUBAGENT_FIELDS;
  return {
    id: draft.id,
    name: draft.name,
    role_title: draft.role_title,
    description: draft.description,
    system_prompt: draft.system_prompt,
    model: draft.model || null,
    reasoning_effort: draft.reasoning_effort || null,
    max_turns: draft.max_turns ?? 40,
    whitelisted_tools: draft.whitelisted_tools,
    disallowed_tools: fields.disallowed_tools,
    temperature: draft.temperature ?? 0.2,
    enabled: draft.enabled,
    tier: fields.tier,
    isolation: fields.isolation,
    max_cost_usd: fields.max_cost_usd,
    max_wall_seconds: fields.max_wall_seconds,
    can_ask_user: fields.can_ask_user,
    inherit: fields.inherit,
    output_schema: parsedOutputSchema(fields.output_schema),
    built_in: draft.built_in,
    customized: draft.customized,
  };
}

export function blankDraft(kind: AgentKind, scope: "org" | "user"): AgentDraft {
  const id = kind === "subagent" ? `role_${Date.now().toString(36)}` : `persona-${Date.now().toString(36)}`;
  return {
    ...fromAgent({
      id,
      name: kind === "subagent" ? "New role" : scope === "user" ? "My agent" : "New agent",
      role_title: "Specialist",
      description: "",
      system_prompt: "You are a senior software engineer. Read before you change anything and verify your work.",
      model: "",
      whitelisted_tools: kind === "subagent" ? ["file_read", "grep_search", "glob_files"] : ["*"],
      scope,
    }),
    kind,
    ...(kind === "subagent" ? { subagent: { ...DEFAULT_SUBAGENT_FIELDS } } : {}),
  };
}

// --- .rocket/agents/<id>.md (front matter, then the prompt) -------------------------------

const EXPORTED = [
  "name",
  "role_title",
  "description",
  "model",
  "reasoning_effort",
  "max_turns",
  "default_permission_mode",
  "whitelisted_tools",
  "sandbox_id",
] as const satisfies readonly (keyof AgentDraft)[];

type ExportedKey = (typeof EXPORTED)[number];
type ParsedValue = string | number | string[];

const REASONING_EFFORTS: readonly ReasoningEffort[] = ["none", "minimal", "low", "medium", "high", "xhigh", "max"];
const PERMISSION_MODES: readonly PermissionMode[] = ["plan", "ask", "auto_edit", "auto", "bypass"];

function isExportedKey(key: string): key is ExportedKey {
  return (EXPORTED as readonly string[]).includes(key);
}

function asText(value: ParsedValue): string {
  return Array.isArray(value) ? value.join(", ") : String(value);
}

function oneOf<T extends string>(allowed: readonly T[], value: ParsedValue): T | "" {
  const text = asText(value);
  return allowed.find((item) => item === text) ?? "";
}

/** ``draft`` with one front-matter field applied; a value of the wrong shape is coerced or dropped. */
function applyField(draft: AgentDraft, key: ExportedKey, value: ParsedValue): AgentDraft {
  switch (key) {
    case "max_turns":
      return { ...draft, max_turns: typeof value === "number" ? value : null };
    case "whitelisted_tools":
      return { ...draft, whitelisted_tools: Array.isArray(value) ? value : [asText(value)] };
    case "reasoning_effort":
      return { ...draft, reasoning_effort: oneOf(REASONING_EFFORTS, value) };
    case "default_permission_mode":
      return { ...draft, default_permission_mode: oneOf(PERMISSION_MODES, value) };
    case "name":
    case "role_title":
    case "description":
    case "model":
    case "sandbox_id":
      return { ...draft, [key]: asText(value) };
  }
}

function applySubagentField(fields: SubagentFields, key: "tier" | "isolation", value: string): SubagentFields {
  if (key === "tier") return { ...fields, tier: oneOf<SubagentTier>(["fast", "default", "frontier"], value) || fields.tier };
  return { ...fields, isolation: oneOf<SubagentIsolation>(["none", "worktree"], value) || fields.isolation };
}

/** The agent as a `.rocket/agents/<id>.md` file (no credentials, ever). */
export function toMarkdown(draft: AgentDraft): string {
  const lines = ["---", `id: ${draft.id}`, `kind: ${draft.kind}`];
  for (const key of EXPORTED) {
    const value = draft[key];
    if (value === "" || value === null || (Array.isArray(value) && value.length === 0)) continue;
    lines.push(`${key}: ${Array.isArray(value) ? `[${value.join(", ")}]` : JSON.stringify(value)}`);
  }
  if (draft.subagent) {
    // What the repository loader reads of a role (``.rocket/agents``); budgets stay the organization's.
    lines.push(`tier: ${draft.subagent.tier}`);
    if (draft.subagent.isolation !== "sandbox") lines.push(`isolation: ${draft.subagent.isolation}`);
  }
  lines.push("---", "", draft.system_prompt.trim(), "");
  return lines.join("\n");
}

function parseValue(raw: string): ParsedValue {
  const text = raw.trim();
  if (text.startsWith("[") && text.endsWith("]")) {
    return text
      .slice(1, -1)
      .split(",")
      .map((item) => item.trim())
      .filter(Boolean);
  }
  if (/^-?\d+$/.test(text)) return Number(text);
  try {
    const parsed: unknown = JSON.parse(text);
    return typeof parsed === "string" ? parsed : text;
  } catch {
    return text;
  }
}

/** A draft from a `.rocket/agents/*.md` file, over ``base`` (unknown keys are ignored). */
export function fromMarkdown(text: string, base: AgentDraft): AgentDraft {
  const match = /^---\n([\s\S]*?)\n---\n?([\s\S]*)$/.exec(text.replace(/\r\n/g, "\n"));
  if (!match) return { ...base, system_prompt: text.trim() };
  let draft: AgentDraft = { ...base, system_prompt: match[2].trim() };
  for (const line of match[1].split("\n")) {
    const at = line.indexOf(":");
    if (at <= 0) continue;
    const key = line.slice(0, at).trim();
    if (draft.subagent && (key === "tier" || key === "isolation")) {
      draft = { ...draft, subagent: applySubagentField(draft.subagent, key, asText(parseValue(line.slice(at + 1)))) };
      continue;
    }
    if (!isExportedKey(key)) continue;
    draft = applyField(draft, key, parseValue(line.slice(at + 1)));
  }
  return draft;
}

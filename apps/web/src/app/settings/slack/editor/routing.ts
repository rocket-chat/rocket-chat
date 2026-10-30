import type { ChannelProfile, SlackBot, SlackRoute, SlackRouterConfig } from "../types";

export type RowKind = "default" | "channel" | "intent";
export type ResponseMode = "" | "thread" | "private";

/** One line of the routing table: where a message comes from (or what it asks), and who answers. */
export interface RoutingRow {
  key: number;
  kind: RowKind;
  /** The channel id, or the intent's name; empty on the default row. */
  match: string;
  agent_id: string;
  repository: string;
  branch: string;
  response_mode: ResponseMode;
  /** Intent: when the classifier should choose it. */
  description: string;
  /** Channel: house rules, tool scope and image, kept as they were. */
  instructions: string;
  allowed_tools: string[];
  sandbox_image: string;
  /** Intent: kept as they were. */
  examples: string[];
  enabled: boolean;
}

/** How intents are recognised; used only when the table has at least one intent row. */
export interface ClassifierSettings {
  agent_id: string;
  model: string;
  allow_dismiss: boolean;
  dismiss_description: string;
  min_confidence: number;
  fallback: string;
  sticky: boolean;
  context_messages: number;
}

function asMode(value: string | undefined): ResponseMode {
  return value === "thread" || value === "private" ? value : "";
}

export function blankRow(kind: RowKind, key: number, agentId = ""): RoutingRow {
  return {
    key,
    kind,
    match: "",
    agent_id: agentId,
    repository: "",
    branch: "",
    response_mode: "",
    description: "",
    instructions: "",
    allowed_tools: [],
    sandbox_image: "",
    examples: [],
    enabled: true,
  };
}

function channelRow(key: number, channel: string, profile: ChannelProfile): RoutingRow {
  return {
    ...blankRow("channel", key, profile.agent_persona ?? ""),
    match: channel,
    repository: profile.repository ?? "",
    branch: profile.branch ?? "",
    response_mode: asMode(profile.response_mode),
    instructions: profile.instructions ?? "",
    allowed_tools: profile.allowed_tools ?? [],
    sandbox_image: profile.sandbox_image ?? "",
  };
}

function intentRow(key: number, route: SlackRoute): RoutingRow {
  return {
    ...blankRow("intent", key, route.agent_id),
    match: route.name,
    repository: route.repository ?? "",
    branch: route.branch ?? "",
    response_mode: asMode(route.response_mode),
    description: route.description,
    examples: route.examples ?? [],
    enabled: route.enabled,
  };
}

/** The table of a stored bot: its default agent first, then its channels, then its intents. */
export function rowsFromBot(bot: SlackBot | null, fallbackAgent: string): RoutingRow[] {
  const profiles = (bot?.policy?.channel_profiles ?? {}) as Record<string, ChannelProfile>;
  const channels = Object.entries(profiles).map(([channel, profile], i) => channelRow(i + 1, channel, profile));
  const intents = (bot?.router?.routes ?? []).map((route, i) => intentRow(channels.length + i + 1, route));
  return [blankRow("default", 0, bot?.agent_id || fallbackAgent), ...channels, ...intents];
}

export function classifierFromBot(bot: SlackBot | null): ClassifierSettings {
  const router = bot?.router;
  return {
    agent_id: router?.classifier_agent_id ?? "",
    model: router?.classifier_model ?? "",
    allow_dismiss: router?.allow_dismiss ?? true,
    dismiss_description: router?.dismiss_description ?? "Casual chit-chat, spam, or out of scope requests",
    min_confidence: router?.min_confidence ?? 0.6,
    fallback: router?.fallback ?? "",
    sticky: router?.sticky ?? true,
    context_messages: router?.context_messages ?? 5,
  };
}

/** Only the fields that are set, so an unset field keeps inheriting the bot's or organization's. */
function profileOf(row: RoutingRow): ChannelProfile {
  const profile: ChannelProfile = {};
  if (row.agent_id) profile.agent_persona = row.agent_id;
  if (row.response_mode) profile.response_mode = row.response_mode;
  if (row.allowed_tools.length) profile.allowed_tools = row.allowed_tools;
  if (row.instructions.trim()) profile.instructions = row.instructions;
  if (row.sandbox_image) profile.sandbox_image = row.sandbox_image;
  if (row.repository.trim()) profile.repository = row.repository.trim();
  if (row.branch.trim()) profile.branch = row.branch.trim();
  return profile;
}

export function channelProfilesOf(rows: RoutingRow[]): Record<string, ChannelProfile> {
  const record: Record<string, ChannelProfile> = {};
  for (const row of rows) {
    if (row.kind === "channel" && row.match.trim()) record[row.match.trim()] = profileOf(row);
  }
  return record;
}

export function routerOf(rows: RoutingRow[], classifier: ClassifierSettings, defaultAgent: string): SlackRouterConfig | null {
  const routes: SlackRoute[] = rows
    .filter((row) => row.kind === "intent")
    .map((row) => ({
      name: row.match.trim(),
      description: row.description,
      agent_id: row.agent_id,
      examples: row.examples,
      response_mode: row.response_mode,
      enabled: row.enabled,
      ...(row.repository.trim() ? { repository: row.repository.trim() } : {}),
      ...(row.branch.trim() ? { branch: row.branch.trim() } : {}),
    }));
  if (routes.length === 0) return null;
  return {
    classifier_agent_id: classifier.agent_id || defaultAgent,
    classifier_model: classifier.model.trim() || undefined,
    routes,
    allow_dismiss: classifier.allow_dismiss,
    dismiss_description: classifier.dismiss_description,
    min_confidence: Number(classifier.min_confidence),
    fallback: classifier.fallback,
    sticky: classifier.sticky,
    context_messages: classifier.context_messages,
  };
}

function duplicated(values: string[]): string | undefined {
  return values.find((value, index) => values.indexOf(value) !== index);
}

/** What stops the table from being saved, said plainly; null when it can be. */
export function routingProblem(rows: RoutingRow[]): string | null {
  const channels = rows.filter((r) => r.kind === "channel").map((r) => r.match.trim());
  const intents = rows.filter((r) => r.kind === "intent").map((r) => r.match.trim());
  if (channels.some((c) => !c)) return "Every channel row needs a channel id.";
  if (intents.some((i) => !i)) return "Every intent row needs a name.";
  if (intents.includes("dismiss")) return "'dismiss' is reserved: allow dismissal instead.";
  const twice = duplicated(channels) ?? duplicated(intents);
  if (twice) return `'${twice}' appears twice in the routing table.`;
  if (rows.some((r) => r.kind === "intent" && !r.agent_id)) return "Every intent row needs an agent.";
  return null;
}

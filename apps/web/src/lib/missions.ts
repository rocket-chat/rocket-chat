import type { OwnerScope } from "./ownership";

/** A mission's trigger: a source and, for forge and tracker events, the filter they must pass. */
export interface MissionTrigger {
  source: string;
  filter: { repositories: string[]; labels: string[]; branches: string[] };
  /** `schedule`: a five-field cron read in `timezone`. */
  schedule: string;
  timezone: string;
}

export type DeliveryKind = "session" | "chat" | "webhook" | "github_review" | "github_comment";

export interface MissionDelivery {
  kind: DeliveryKind;
  bot_id: string;
  channel: string;
  url: string;
  output_schema: Record<string, unknown>;
}

/** What a mission is made of: the body of `POST` / `PUT /v1/missions`. */
export interface MissionBody {
  name: string;
  instructions: string;
  scope: OwnerScope;
  team_id: string | null;
  triggers: MissionTrigger[];
  deliveries: MissionDelivery[];
  agent_id: string;
  permission_mode: string;
  model_tier: string;
  sandbox_type: string;
  mode: "work" | "review";
  auto_fix: boolean;
  git_repo: string;
  git_branch: string;
  enabled: boolean;
}

export interface Mission extends MissionBody {
  id: string;
  user_id: string | null;
  run_as: string;
  next_run_at: number | null;
  last_run_at: number | null;
  last_session_id: string | null;
  last_status: string | null;
  created_at: number;
  updated_at: number;
  can_manage: boolean;
  /** The mission's own inbound webhook, for a `webhook` trigger. */
  inbound_path: string | null;
  has_inbound_secret: boolean;
  has_webhook_secret: boolean;
}

/** A mission as created or replaced: secrets made by that save are shown once. */
export interface SavedMission extends Mission {
  inbound_secret?: string | null;
  webhook_secret?: string | null;
}

export interface MissionRun {
  id: string;
  session_id: string;
  source: string;
  title: string;
  status: "running" | "completed" | "failed" | "delivered" | "delivery_failed";
  deliveries: { kind: string; status: string; error?: string }[];
  started_at: number;
  finished_at: number | null;
}

export interface MissionPreset {
  id: string;
  name: string;
  description: string;
  mission: Partial<MissionBody>;
}

export const MAX_TRIGGERS = 3;
export const MAX_OUTPUT_SCHEMA_CHARS = 4000;

/** The sources a person picks from, grouped as the select shows them. */
export const TRIGGER_SOURCES: { value: string; label: string; group: string }[] = [
  { value: "schedule", label: "On a schedule", group: "Its own" },
  { value: "webhook", label: "When its webhook is called", group: "Its own" },
  { value: "github_pr", label: "A pull request is opened or updated", group: "GitHub" },
  { value: "github_issue_label", label: "An issue gets a label", group: "GitHub" },
  { value: "github_issue_comment", label: "A comment addresses the agent", group: "GitHub" },
  { value: "github_pr_review_comment", label: "A review comment addresses the agent", group: "GitHub" },
  { value: "github_workflow_failed", label: "A workflow run fails", group: "GitHub" },
  { value: "gitlab_mr", label: "A merge request is opened or updated", group: "GitLab" },
  { value: "gitlab_issue_label", label: "An issue gets a label", group: "GitLab" },
  { value: "gitlab_note", label: "A comment addresses the agent", group: "GitLab" },
  { value: "gitlab_pipeline_failed", label: "A pipeline fails", group: "GitLab" },
  { value: "jira_issue", label: "A Jira issue", group: "Trackers" },
  { value: "linear_issue", label: "A Linear issue", group: "Trackers" },
];

export const OWN_SOURCES = new Set(["schedule", "webhook", "api"]);
export const REVIEW_SOURCES = new Set(["github_pr", "gitlab_mr"]);
export const GITHUB_ITEM_SOURCES = new Set(["github_pr", "github_issue_label", "github_issue_comment", "github_pr_review_comment"]);

export const DELIVERY_OPTIONS: { kind: Exclude<DeliveryKind, "session">; label: string }[] = [
  { kind: "chat", label: "A Slack message" },
  { kind: "webhook", label: "A webhook (JSON)" },
  { kind: "github_review", label: "A pull request review" },
  { kind: "github_comment", label: "A comment on the issue or pull request" },
];

export const SCHEDULES: { label: string; cron: string }[] = [
  { label: "Every weekday at 09:00", cron: "0 9 * * 1-5" },
  { label: "Every night at 03:00", cron: "0 3 * * *" },
  { label: "Every Monday at 08:00", cron: "0 8 * * 1" },
  { label: "Every hour", cron: "0 * * * *" },
];

export const MODEL_TIERS: { value: string; label: string }[] = [
  { value: "", label: "The agent's model" },
  { value: "fast", label: "Fast" },
  { value: "default", label: "Default" },
  { value: "frontier", label: "Frontier" },
];

export function sourceLabel(source: string): string {
  const found = TRIGGER_SOURCES.find((s) => s.value === source);
  if (source === "api") return "Run by hand";
  return found ? (found.group === "Its own" ? found.label : `${found.group}: ${found.label.toLowerCase()}`) : source;
}

export function scheduleLabel(cron: string): string {
  return SCHEDULES.find((s) => s.cron === cron)?.label ?? cron;
}

export function deliveryLabel(kind: string): string {
  if (kind === "session") return "The session";
  return DELIVERY_OPTIONS.find((d) => d.kind === kind)?.label ?? kind;
}

export function blankTrigger(source = "schedule"): MissionTrigger {
  return {
    source,
    filter: { repositories: [], labels: [], branches: [] },
    schedule: source === "schedule" ? SCHEDULES[0].cron : "",
    timezone: "UTC",
  };
}

export function blankDelivery(kind: DeliveryKind): MissionDelivery {
  return { kind, bot_id: "", channel: "", url: "", output_schema: {} };
}

export function blankMission(): MissionBody {
  return {
    name: "",
    instructions: "",
    scope: "user",
    team_id: null,
    triggers: [blankTrigger()],
    deliveries: [],
    agent_id: "",
    permission_mode: "",
    model_tier: "",
    sandbox_type: "",
    mode: "work",
    auto_fix: false,
    git_repo: "",
    git_branch: "",
    enabled: true,
  };
}

/** A preset's or a stored mission's fields over a blank one (the API leaves defaults out). */
export function completeMission(partial: Partial<MissionBody>): MissionBody {
  const base = blankMission();
  return {
    ...base,
    ...partial,
    triggers: (partial.triggers ?? base.triggers).map((t) => ({
      ...blankTrigger(t.source),
      ...t,
      filter: { ...blankTrigger().filter, ...(t.filter ?? {}) },
    })),
    deliveries: (partial.deliveries ?? []).filter((d) => d.kind !== "session").map((d) => ({ ...blankDelivery(d.kind), ...d })),
    team_id: partial.team_id ?? null,
  };
}

/** Why the mission cannot be saved as it is, or null: the rules the API applies too. */
export function missionProblem(mission: MissionBody): string | null {
  if (!mission.name.trim()) return "Give the mission a name.";
  const sources = new Set(mission.triggers.map((t) => t.source));
  if (mission.triggers.length > MAX_TRIGGERS) return `A mission has at most ${MAX_TRIGGERS} triggers.`;
  if (mission.triggers.some((t) => t.source === "schedule" && t.schedule.trim().split(/\s+/).length !== 5)) {
    return "A schedule is a five-field cron expression, such as 0 9 * * 1-5.";
  }
  if (mission.mode === "review" && ![...sources].some((s) => REVIEW_SOURCES.has(s))) {
    return "A review mission needs a pull request (or merge request) trigger.";
  }
  const kinds = mission.deliveries.map((d) => d.kind);
  if (new Set(kinds).size !== kinds.length) return "Each kind of delivery appears once.";
  if (kinds.includes("github_review") && !sources.has("github_pr")) return "A pull request review needs a GitHub pull request trigger.";
  if (kinds.includes("github_comment") && ![...sources].some((s) => GITHUB_ITEM_SOURCES.has(s))) {
    return "A GitHub comment needs a GitHub issue or pull request trigger.";
  }
  for (const delivery of mission.deliveries) {
    if (delivery.kind === "chat" && !/^[CGD][A-Z0-9]{2,31}$/.test(delivery.channel.trim())) {
      return "A Slack message needs a channel id (C…, G… or D…).";
    }
    if (delivery.kind === "webhook" && !delivery.url.trim().startsWith("https://")) return "A webhook delivery needs an https:// URL.";
  }
  return null;
}

/** The webhook delivery's schema as typed: an object, or a message saying why not. */
export function parseOutputSchema(text: string): { schema: Record<string, unknown> } | { error: string } {
  if (!text.trim()) return { schema: {} };
  if (text.length > MAX_OUTPUT_SCHEMA_CHARS) return { error: `The schema is over ${MAX_OUTPUT_SCHEMA_CHARS} characters.` };
  try {
    const parsed: unknown = JSON.parse(text);
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return { error: "The schema must be a JSON object." };
    return { schema: parsed as Record<string, unknown> };
  } catch {
    return { error: "The schema is not valid JSON." };
  }
}

/** A curl call that runs the mission through its own webhook, signed as the API expects. */
export function inboundCurl(url: string, secret: string): string {
  return [
    `SECRET='${secret}'`,
    `BODY='{"text": "What this run is about"}'`,
    "TS=$(date +%s)",
    `SIG=$(printf '%s.%s' "$TS" "$BODY" | openssl dgst -sha256 -hmac "$SECRET" | sed 's/^.* //')`,
    `curl -X POST '${url}' \\`,
    "  -H 'Content-Type: application/json' \\",
    '  -H "X-Rocket-Timestamp: $TS" \\',
    '  -H "X-Rocket-Signature: sha256=$SIG" \\',
    '  -d "$BODY"',
  ].join("\n");
}

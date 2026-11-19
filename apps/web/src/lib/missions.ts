import type { OwnerScope } from "./ownership";

/** A mission's trigger: a source and, for forge and tracker events, the filter they must pass. */
export interface MissionTrigger {
  source: string;
  filter: { repositories: string[]; labels: string[]; branches: string[] };
  /** `schedule`: a five-field cron read in `timezone`. */
  schedule: string;
  timezone: string;
  /** Events and the webhook only: minutes the run waits after the event (0: at once). */
  delay_minutes: number;
}

export type DeliveryKind = "session" | "chat" | "webhook" | "github_review" | "github_comment" | "pull_request";

export interface MissionDelivery {
  kind: DeliveryKind;
  bot_id: string;
  channel: string;
  url: string;
  output_schema: Record<string, unknown>;
  /** The run may end without it: nothing to report, or no change to propose. */
  optional: boolean;
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
  /** owner/name; an event's own repository comes first. */
  git_repos: string[];
  /** Checked out in the first repository. */
  git_branch: string;
  /** Runs may push and open pull requests; otherwise they only read GitHub. */
  github_write: boolean;
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
  /** `scheduled`: waiting for its trigger's delay; `started_at` is then when it starts. */
  status: "scheduled" | "running" | "completed" | "failed" | "delivered" | "delivery_failed";
  deliveries: { kind: string; status: string; error?: string; url?: string }[];
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
export const MAX_REPOSITORIES = 10;

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
  { kind: "pull_request", label: "A pull request" },
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

/** Sources whose run may wait after the event: not a schedule, which already says when. */
export function canDelay(source: string): boolean {
  return source !== "schedule" && source !== "api";
}

export const MAX_TRIGGER_DELAY_MINUTES = 24 * 60;

/** The common waits, offered as a list; Advanced takes any number of minutes. */
export const TRIGGER_DELAYS: { minutes: number; label: string }[] = [
  { minutes: 0, label: "Right away" },
  { minutes: 15, label: "15 minutes later" },
  { minutes: 30, label: "30 minutes later" },
  { minutes: 60, label: "1 hour later" },
  { minutes: 120, label: "2 hours later" },
  { minutes: 360, label: "6 hours later" },
  { minutes: 1440, label: "24 hours later" },
];

/** "1 h later", "45 min later", "1 h 30 min later"; empty when the run starts at once. */
export function delayLabel(minutes: number): string {
  if (!minutes) return "";
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  const parts = [hours ? `${hours} h` : "", rest ? `${rest} min` : ""].filter(Boolean);
  return `${parts.join(" ")} later`;
}

/** A trigger in a few words: its source (or schedule) and, when it waits, how long. */
export function triggerLabel(trigger: MissionTrigger, schedule: (t: MissionTrigger) => string): string {
  const base = trigger.source === "schedule" ? schedule(trigger) : sourceLabel(trigger.source);
  const wait = delayLabel(trigger.delay_minutes ?? 0);
  return wait ? `${base}, ${wait}` : base;
}

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
    delay_minutes: 0,
  };
}

export function blankDelivery(kind: DeliveryKind): MissionDelivery {
  return { kind, bot_id: "", channel: "", url: "", output_schema: {}, optional: false };
}

export function blankMission(): MissionBody {
  return {
    name: "",
    instructions: "",
    scope: "user",
    team_id: null,
    triggers: [],
    deliveries: [],
    agent_id: "",
    permission_mode: "",
    model_tier: "",
    sandbox_type: "",
    mode: "work",
    auto_fix: false,
    git_repos: [],
    git_branch: "",
    github_write: false,
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

/** Whether runs must be allowed to change GitHub: they open a pull request or follow one up. */
export function needsGithubWrite(mission: Pick<MissionBody, "deliveries" | "auto_fix">): boolean {
  return mission.auto_fix || mission.deliveries.some((d) => d.kind === "pull_request");
}

/** The parts of a mission the form walks through, in order. */
export type MissionStep = "when" | "agent" | "task" | "deliver";
export const MISSION_STEPS: MissionStep[] = ["when", "agent", "task", "deliver"];

/**
 * What the person has said on purpose, beyond the fields: a mission with no trigger runs only
 * when someone presses Run now, and one with no delivery reports only to its session. Both are
 * real choices, so a blank form asks for them instead of assuming them.
 */
export interface MissionChoices {
  runByHand: boolean;
  sessionOnly: boolean;
}

const REPOSITORY = /^[\w.-]+\/[\w.-]+$|^https:\/\/github\.com\/[\w.-]+\/[\w.-]+(\.git)?\/?$/;

function whenProblem(mission: MissionBody, choices: MissionChoices): string | null {
  const sources = new Set(mission.triggers.map((t) => t.source));
  if (mission.triggers.length === 0 && !choices.runByHand) return "Choose what starts the mission, or that it runs only by hand.";
  if (mission.triggers.length > MAX_TRIGGERS) return `A mission has at most ${MAX_TRIGGERS} triggers.`;
  if (mission.triggers.some((t) => t.source === "schedule" && t.schedule.trim().split(/\s+/).length !== 5)) {
    return "A schedule is a five-field cron expression, such as 0 9 * * 1-5.";
  }
  if (mission.triggers.some((t) => !Number.isInteger(t.delay_minutes) || t.delay_minutes < 0 || t.delay_minutes > MAX_TRIGGER_DELAY_MINUTES)) {
    return "A trigger waits between 0 and 1440 minutes (24 hours).";
  }
  if (mission.mode === "review" && ![...sources].some((s) => REVIEW_SOURCES.has(s))) {
    return "A review mission needs a pull request (or merge request) trigger.";
  }
  return null;
}

function taskProblem(mission: MissionBody): string | null {
  if (mission.mode === "work" && !mission.instructions.trim()) return "Say what the agent should do.";
  if (mission.git_repos.length > MAX_REPOSITORIES) return `A mission names at most ${MAX_REPOSITORIES} repositories.`;
  const wrong = mission.git_repos.find((r) => r.trim() && !REPOSITORY.test(r.trim()));
  if (wrong) return `"${wrong}" is not a repository: use owner/name.`;
  if (needsGithubWrite(mission) && !mission.github_write) return "Allow GitHub writes: the mission opens or follows up a pull request.";
  return null;
}

function deliverProblem(mission: MissionBody, choices: MissionChoices): string | null {
  const sources = new Set(mission.triggers.map((t) => t.source));
  const kinds = mission.deliveries.map((d) => d.kind);
  if (kinds.length === 0 && !choices.sessionOnly) return "Choose where the result goes, or that its session is enough.";
  if (new Set(kinds).size !== kinds.length) return "Each kind of delivery appears once.";
  if (kinds.includes("pull_request") && mission.mode === "review") return "A review mission changes no code, so it opens no pull request.";
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

/** What each step still needs before the mission can be saved (null: the step is complete). */
export function stepProblems(mission: MissionBody, choices: MissionChoices): Record<MissionStep, string | null> {
  return { when: whenProblem(mission, choices), agent: null, task: taskProblem(mission), deliver: deliverProblem(mission, choices) };
}

/** Why the mission cannot be saved as it is, or null: the rules the API applies too. */
export function missionProblem(mission: MissionBody, choices: MissionChoices = { runByHand: true, sessionOnly: true }): string | null {
  if (!mission.name.trim()) return "Give the mission a name.";
  const problems = stepProblems(mission, choices);
  return MISSION_STEPS.map((step) => problems[step]).find((problem) => problem !== null) ?? null;
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

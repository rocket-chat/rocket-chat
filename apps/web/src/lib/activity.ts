import type { FileDiffItem, FlightLogEntry, ToolCallData } from "../types/mission";

export type ActivityKind = "run" | "read" | "edit" | "search" | "git" | "web" | "other";

export interface StepDescription {
  kind: ActivityKind;
  /** Past-tense label, e.g. "Edited". */
  done: string;
  /** Present-tense label while the call is in flight, e.g. "Editing". */
  doing: string;
  /** What it acted on: a command, a path, a query. */
  target: string;
  /** The workspace path an edit touched, used to find its diff. */
  path?: string;
}

const RUN_TOOLS = new Set(["bash_exec", "bash", "exec_command"]);
const CHECK_TOOLS: Record<string, string> = {
  run_tests: "tests",
  lint: "the linter",
  typecheck: "the type checker",
  build: "the build",
};
const EDIT_TOOLS = new Set(["file_write", "file_edit", "apply_patch", "apply_diff_file"]);
const SEARCH_TOOLS = new Set(["grep_search", "glob_files", "code_search", "kb_search", "web_search", "recall"]);

const asText = (value: unknown): string => (typeof value === "string" ? value : "");

const clip = (text: string, max = 80): string => {
  const single = text.replace(/\s+/g, " ").trim();
  return single.length > max ? `${single.slice(0, max - 1)}…` : single;
};

/** Words for a tool call a person would use, so rows read as "Edited src/app.py", not "file_edit". */
export function describeToolCall(call: ToolCallData): StepDescription {
  const args = call.arguments ?? {};
  const name = call.name;

  if (RUN_TOOLS.has(name)) {
    return { kind: "run", done: "Ran", doing: "Running", target: clip(asText(args.command) || asText(args.cmd)) };
  }
  if (name in CHECK_TOOLS) {
    return { kind: "run", done: "Ran", doing: "Running", target: CHECK_TOOLS[name] };
  }
  if (name === "package_manager") {
    return { kind: "run", done: "Ran", doing: "Running", target: clip(`${asText(args.action)} ${asText(args.packages)}`) || "the package manager" };
  }
  if (EDIT_TOOLS.has(name)) {
    const path = asText(args.path) || undefined;
    const created = name === "file_write";
    return {
      kind: "edit",
      done: created ? "Wrote" : "Edited",
      doing: created ? "Writing" : "Editing",
      target: path ?? "files",
      path,
    };
  }
  if (name === "file_read") {
    return { kind: "read", done: "Read", doing: "Reading", target: asText(args.path) };
  }
  if (name === "list_dir") {
    return { kind: "read", done: "Listed", doing: "Listing", target: asText(args.path) || "." };
  }
  if (SEARCH_TOOLS.has(name)) {
    return {
      kind: "search",
      done: "Searched",
      doing: "Searching",
      target: clip(asText(args.query) || asText(args.pattern) || asText(args.glob)),
    };
  }
  if (name.startsWith("git_")) {
    return { kind: "git", done: "Ran", doing: "Running", target: name.replace("_", " ") };
  }
  if (name === "web_fetch") {
    return { kind: "web", done: "Fetched", doing: "Fetching", target: clip(asText(args.url)) };
  }
  if (name === "browser_inspect") {
    return { kind: "web", done: "Inspected", doing: "Inspecting", target: clip(asText(args.url)) };
  }
  const readable = name.replace(/^mcp__/, "").replace(/__/g, " · ").replace(/_/g, " ");
  return { kind: "other", done: "Used", doing: "Using", target: readable };
}

export const isPending = (call: ToolCallData): boolean => call.status === "pending";
export const isFailed = (call: ToolCallData): boolean => call.status === "error";

const plural = (count: number, one: string, many: string): string => `${count} ${count === 1 ? one : many}`;

export interface GroupSummary {
  /** One line for the collapsed state, e.g. "Edited 2 files · Ran 3 commands". */
  text: string;
  failed: number;
  /** What is happening right now, while the group is still active. */
  current: string | null;
}

/** Summarises a run of agent steps so the collapsed view says what happened without opening it. */
export function summarizeGroup(entries: FlightLogEntry[]): GroupSummary {
  const counts: Record<ActivityKind, number> = { run: 0, read: 0, edit: 0, search: 0, git: 0, web: 0, other: 0 };
  const editedPaths = new Set<string>();
  let failed = 0;
  let current: string | null = null;
  let subagents = 0;

  for (const entry of entries) {
    if (entry.subagent) {
      subagents += 1;
      if (entry.subagent.status === "error") failed += 1;
      if (entry.subagent.status === "running") current = `Delegating to ${entry.subagent.role.replace(/_/g, " ")}`;
      continue;
    }
    if (!entry.toolCall) continue;
    const step = describeToolCall(entry.toolCall);
    counts[step.kind] += 1;
    if (step.kind === "edit" && step.path) editedPaths.add(step.path);
    if (isFailed(entry.toolCall)) failed += 1;
    if (isPending(entry.toolCall)) current = `${step.doing} ${step.target}`.trim();
  }

  const parts: string[] = [];
  if (editedPaths.size > 0 || counts.edit > 0) {
    parts.push(`Edited ${plural(editedPaths.size || counts.edit, "file", "files")}`);
  }
  if (counts.run + counts.git > 0) parts.push(`Ran ${plural(counts.run + counts.git, "command", "commands")}`);
  if (counts.read > 0) parts.push(`Read ${plural(counts.read, "file", "files")}`);
  if (counts.search > 0) parts.push(`Searched ${counts.search === 1 ? "once" : `${counts.search} times`}`);
  if (counts.web > 0) parts.push(`Browsed ${plural(counts.web, "page", "pages")}`);
  if (counts.other > 0) parts.push(`Used ${plural(counts.other, "tool", "tools")}`);
  if (subagents > 0) parts.push(`Delegated to ${plural(subagents, "specialist", "specialists")}`);
  if (!current) {
    const lastContentEntry = [...entries].reverse().find(
      (e) =>
        e.content &&
        !e.toolCall &&
        !e.subagent &&
        !e.content.startsWith("Executing tool:") &&
        !e.content.startsWith("Invoking tool:") &&
        !e.content.startsWith("Delegating to subagent")
    );
    if (lastContentEntry?.content) {
      current = clip(lastContentEntry.content.replace(/[#*_`>]/g, "").trim(), 60);
    }
  }

  return { text: parts.length > 0 ? parts.slice(0, 3).join(" · ") : "Thought it through", failed, current };
}

export interface DiffCounts {
  additions: number;
  deletions: number;
}

const countLines = (text: string): number => (text ? text.split("\n").length : 0);

/** Added/removed line counts for an edit: the recorded diff when there is one, else the arguments. */
export function diffCounts(call: ToolCallData, recorded?: FileDiffItem): DiffCounts | null {
  if (recorded) return { additions: recorded.additions, deletions: recorded.deletions };
  const args = call.arguments ?? {};
  if (call.name === "file_edit") {
    return { additions: countLines(asText(args.replacement_block)), deletions: countLines(asText(args.target_block)) };
  }
  if (call.name === "file_write") return { additions: countLines(asText(args.content)), deletions: 0 };
  if (call.name === "apply_patch" || call.name === "apply_diff_file") {
    const patch = asText(args.patch_content) || asText(args.diff_content);
    let additions = 0;
    let deletions = 0;
    for (const line of patch.split("\n")) {
      if (line.startsWith("+") && !line.startsWith("+++")) additions += 1;
      else if (line.startsWith("-") && !line.startsWith("---")) deletions += 1;
    }
    return { additions, deletions };
  }
  return null;
}

/** A unified diff for an edit when the server did not record one, built from the call's arguments. */
export function synthesizeDiff(call: ToolCallData): string | null {
  const args = call.arguments ?? {};
  if (call.name === "file_edit") {
    const removed = asText(args.target_block).split("\n").map((line) => `-${line}`);
    const added = asText(args.replacement_block).split("\n").map((line) => `+${line}`);
    return [...removed, ...added].join("\n");
  }
  if (call.name === "file_write") {
    return asText(args.content).split("\n").map((line) => `+${line}`).join("\n");
  }
  if (call.name === "apply_patch" || call.name === "apply_diff_file") {
    return asText(args.patch_content) || asText(args.diff_content) || null;
  }
  return null;
}

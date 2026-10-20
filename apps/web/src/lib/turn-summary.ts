import type { FlightLogEntry } from "../types/mission";

export interface TurnSummary {
  edits: number;
  commands: number;
  checks: number;
  failed: number;
  durationMs: number;
  pullRequest: string | null;
}

const EDIT_TOOLS = new Set(["file_write", "file_edit", "apply_patch", "apply_diff_file"]);
const CHECK_TOOLS = new Set(["run_tests", "lint", "typecheck", "build"]);
const PR_URL = /https:\/\/[^\s)]+\/pull\/\d+/;

/** What the latest turn did, counted from its steps (back to the person's last message). */
export function summariseLastTurn(log: FlightLogEntry[]): TurnSummary | null {
  const summary: TurnSummary = { edits: 0, commands: 0, checks: 0, failed: 0, durationMs: 0, pullRequest: null };
  let seen = 0;
  for (let index = log.length - 1; index >= 0; index--) {
    const entry = log[index];
    if (entry.role === "USER") break;
    const call = entry.toolCall;
    if (!call) continue;
    seen += 1;
    if (EDIT_TOOLS.has(call.name)) summary.edits += 1;
    else if (CHECK_TOOLS.has(call.name)) summary.checks += 1;
    else if (call.name === "bash_exec") summary.commands += 1;
    if (call.status === "error") summary.failed += 1;
    summary.durationMs += call.duration_ms ?? 0;
    if (!summary.pullRequest && typeof call.result === "string") {
      summary.pullRequest = PR_URL.exec(call.result)?.[0] ?? null;
    }
  }
  return seen === 0 || summary.edits + summary.commands + summary.checks === 0 ? null : summary;
}

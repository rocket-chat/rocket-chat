import type { FlightLogEntry } from "../types/mission";

export interface PullRequestRef {
  repo: string;
  number: number;
  url: string;
}

const PR_URL = /https:\/\/([^\s/)]+)\/([\w.-]+\/[\w.-]+)\/pull\/(\d+)/g;
// Tools that open or work on a pull request: a URL in a search or a log is not one the session handles.
const PR_TOOLS = new Set(["git_open_pr", "github_pull_request", "git_push"]);
const WAIT_TARGET = /^([\w.-]+\/[\w.-]+)#(\d+)$/;

export function pullRequestLabel(pr: Pick<PullRequestRef, "repo" | "number">): string {
  return `${pr.repo}#${pr.number}`;
}

/** The repository and number a GitHub wait names (`acme/api#42`), or null for any other target. */
export function parseWaitTarget(target: string): Pick<PullRequestRef, "repo" | "number"> | null {
  const match = WAIT_TARGET.exec(target.trim());
  return match ? { repo: match[1], number: Number(match[2]) } : null;
}

/**
 * Every pull request a session's tool results mention, each once, ordered by when it was last
 * mentioned (the one the agent worked on most recently comes last).
 */
export function collectPullRequests(log: FlightLogEntry[]): PullRequestRef[] {
  const found = new Map<string, PullRequestRef>();
  for (const entry of log) {
    if (!entry.toolCall || !PR_TOOLS.has(entry.toolCall.name)) continue;
    const result = entry.toolCall.result;
    // Every store update runs this: skip the pattern for the results that cannot match.
    if (typeof result !== "string" || !result.includes("/pull/")) continue;
    for (const match of result.matchAll(PR_URL)) {
      const ref: PullRequestRef = { repo: match[2], number: Number(match[3]), url: match[0] };
      const key = pullRequestLabel(ref).toLowerCase();
      // Re-inserting moves it to the end: Map keeps insertion order.
      found.delete(key);
      found.set(key, ref);
    }
  }
  return [...found.values()];
}

/**
 * Where a wait's pull request lives: the URL a tool result already showed (so an enterprise
 * host is respected), else github.com.
 */
export function pullRequestUrlFor(
  target: Pick<PullRequestRef, "repo" | "number">,
  known: PullRequestRef[],
): string {
  const hit = known.find(
    (pr) => pr.number === target.number && pr.repo.toLowerCase() === target.repo.toLowerCase(),
  );
  return hit?.url ?? `https://github.com/${target.repo}/pull/${target.number}`;
}

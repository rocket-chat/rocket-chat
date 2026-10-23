"use client";

import React from "react";
import { Check, Clock, ExternalLink, GitPullRequest, Hourglass, TimerOff, X } from "lucide-react";
import { useMissionStore } from "../lib/store";
import {
  collectPullRequests,
  parseWaitTarget,
  pullRequestLabel,
  pullRequestUrlFor,
} from "../lib/pull-requests";
import type { FlightLogEntry, WaitEntry } from "../types/mission";

const SUBJECT: Record<string, string> = {
  ci_status: "CI checks",
  review: "a review",
  process_exit: "a process to exit",
  time: "a timer",
};

type Tone = "waiting" | "done" | "failed" | "timeout" | "past";

// What the agent's own transcript says when a session is reloaded: the live events are gone,
// so the wait is known to have happened but not how it ended.
const RECORDED_WAIT = /^Waiting for (\w+) on (\S+?)\. Durable wait registered/;

/** The wait an entry stands for: its own record, or one read from a reloaded transcript line. */
export function waitOf(entry: FlightLogEntry): WaitEntry | null {
  if (entry.wait) return entry.wait;
  const match = entry.role === "SYSTEM" ? RECORDED_WAIT.exec(entry.content) : null;
  return match
    ? { waitId: entry.id, waitType: match[1], target: match[2], timeoutSeconds: null, status: "recorded" }
    : null;
}

function toneOf(wait: WaitEntry): Tone {
  if (wait.status === "recorded") return "past";
  if (wait.status === "waiting") return "waiting";
  if (wait.status === "timeout") return "timeout";
  return /fail|error|cancel/i.test(wait.status) ? "failed" : "done";
}

const TONE_STYLE: Record<Tone, string> = {
  waiting: "border-border bg-surface-card/50 text-foreground",
  past: "border-border bg-surface-card/50 text-foreground",
  done: "border-emerald-500/30 bg-emerald-500/5 text-foreground",
  failed: "border-rose-500/40 bg-rose-500/5 text-foreground",
  timeout: "border-amber-500/40 bg-amber-500/5 text-foreground",
};

function StatusMark({ tone }: { tone: Tone }) {
  if (tone === "past") return null;
  if (tone === "done") return <Check className="h-3.5 w-3.5 text-emerald-500" aria-hidden="true" />;
  if (tone === "failed") return <X className="h-3.5 w-3.5 text-rose-500" aria-hidden="true" />;
  if (tone === "timeout") return <TimerOff className="h-3.5 w-3.5 text-amber-500" aria-hidden="true" />;
  return <span className="h-1.5 w-1.5 rounded-full bg-brand animate-pulse" aria-hidden="true" />;
}

function describeTimeout(seconds: number | null): string | null {
  if (!seconds) return null;
  return seconds >= 120 ? `up to ${Math.round(seconds / 60)} min` : `up to ${seconds} s`;
}

/**
 * A wait the agent registered, as one inline row: what it waits for, the pull request it is
 * about (a link), and, while it waits, the latest GitHub event seen for that pull request.
 */
export const WaitCard: React.FC<{ entry: FlightLogEntry }> = ({ entry }) => {
  const wait = waitOf(entry);
  const pr = wait ? parseWaitTarget(wait.target) : null;
  // Primitives, so a token streaming elsewhere in the log does not re-render the card.
  const url = useMissionStore((state) =>
    pr ? pullRequestUrlFor(pr, collectPullRequests(state.flightLog)) : null,
  );
  const latest = useMissionStore((state) =>
    pr && wait?.status === "waiting"
      ? (state.flightLog.findLast(
          (item) => item.githubEvent?.target.toLowerCase() === pullRequestLabel(pr).toLowerCase(),
        )?.githubEvent?.headline ?? null)
      : null,
  );
  if (!wait) return null;

  const tone = toneOf(wait);
  const subject = SUBJECT[wait.waitType] ?? wait.waitType;
  const detail = tone === "waiting" ? (latest ?? describeTimeout(wait.timeoutSeconds)) : wait.summary;

  return (
    <div data-testid="waiting-card" data-wait-status={tone} className="my-2 flex justify-center">
      <div
        className={`flex w-full max-w-xl flex-wrap items-center gap-x-2.5 gap-y-1 rounded-xl border px-3 py-2 text-xs ${TONE_STYLE[tone]}`}
      >
        {pr ? (
          <GitPullRequest className="h-4 w-4 shrink-0 text-neutral-500" aria-hidden="true" />
        ) : wait.waitType === "time" ? (
          <Clock className="h-4 w-4 shrink-0 text-neutral-500" aria-hidden="true" />
        ) : (
          <Hourglass className="h-4 w-4 shrink-0 text-neutral-500" aria-hidden="true" />
        )}
        <span className="inline-flex items-center gap-1.5 font-medium">
          <StatusMark tone={tone} />
          {tone === "waiting"
            ? `Waiting for ${subject}`
            : tone === "past"
              ? `Waited for ${subject}`
              : tone === "timeout"
                ? "Gave up waiting"
                : "Wait over"}
        </span>
        {pr && <span className="font-mono text-neutral-600 dark:text-neutral-300">{pullRequestLabel(pr)}</span>}
        {detail && (
          <span data-testid="wait-detail" className="min-w-0 flex-1 truncate text-neutral-500" title={detail}>
            {detail}
          </span>
        )}
        {url && (
          <a
            href={url}
            target="_blank"
            rel="noopener noreferrer"
            data-testid="wait-open-pr"
            className="ml-auto inline-flex items-center gap-1 rounded-lg border border-border px-2 py-0.5 text-foreground hover:bg-elevated"
          >
            <ExternalLink className="h-3 w-3" aria-hidden="true" />
            Open PR
          </a>
        )}
      </div>
    </div>
  );
};

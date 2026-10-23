"use client";

import React from "react";
import { useShallow } from "zustand/react/shallow";
import { CheckCircle2, FileDiff, GitPullRequest, TriangleAlert } from "lucide-react";
import { useMissionStore } from "../lib/store";
import { summariseLastTurn } from "../lib/turn-summary";
import { collectPullRequests, pullRequestLabel, type PullRequestRef } from "../lib/pull-requests";

function plural(count: number, word: string): string {
  return `${count} ${word}${count === 1 ? "" : "s"}`;
}

function PullRequestLink({ pr, primary }: { pr: PullRequestRef; primary: boolean }) {
  return (
    <a
      href={pr.url}
      target="_blank"
      rel="noopener noreferrer"
      data-testid="turn-summary-pr"
      title={`Open ${pullRequestLabel(pr)} on GitHub`}
      className={
        primary
          ? "inline-flex items-center gap-1.5 rounded-lg bg-primary px-2.5 py-1 text-white hover:bg-primary-hover"
          : "inline-flex items-center gap-1.5 rounded-lg border border-border px-2.5 py-1 text-foreground hover:bg-elevated"
      }
    >
      <GitPullRequest className="h-3.5 w-3.5" aria-hidden="true" />
      {pullRequestLabel(pr)}
    </a>
  );
}

/** What a finished turn did, and where to look next: review the changes, or open the pull request. */
export const TurnSummaryCard: React.FC = () => {
  const { flightLog, isExecuting, changed, setTab, openPanel } = useMissionStore(
    useShallow((state) => ({
      flightLog: state.flightLog,
      isExecuting: state.isExecuting,
      changed: state.fileDiffs.length,
      setTab: state.setInspectorTab,
      openPanel: state.setIsRightPanelOpen,
    })),
  );
  if (isExecuting) return null;
  const summary = summariseLastTurn(flightLog);
  if (!summary) return null;

  // Pull requests of earlier turns stay reachable, but only this turn's are laid out.
  const thisTurn = new Set(summary.pullRequests.map((pr) => pullRequestLabel(pr).toLowerCase()));
  const earlier = collectPullRequests(flightLog).filter((pr) => !thisTurn.has(pullRequestLabel(pr).toLowerCase()));

  const parts = [
    summary.edits > 0 && `Edited ${plural(changed || summary.edits, "file")}`,
    summary.commands > 0 && `ran ${plural(summary.commands, "command")}`,
    summary.checks > 0 && `${plural(summary.checks, "check")}`,
    summary.durationMs > 0 && `${Math.max(1, Math.round(summary.durationMs / 1000))} s of tool time`,
  ].filter(Boolean);

  return (
    <div
      data-testid="turn-summary"
      className="flex flex-wrap items-center gap-x-3 gap-y-2 rounded-xl border border-border bg-surface/80 px-3.5 py-2.5 text-xs"
    >
      {summary.failed > 0 ? (
        <TriangleAlert className="h-4 w-4 shrink-0 text-amber-500" aria-hidden="true" />
      ) : (
        <CheckCircle2 className="h-4 w-4 shrink-0 text-emerald-500" aria-hidden="true" />
      )}
      <span className="min-w-0 flex-1 text-foreground">
        {parts.join(" · ")}
        {summary.failed > 0 && <span className="text-amber-600 dark:text-amber-400"> · {summary.failed} failed</span>}
      </span>
      {summary.edits > 0 && (
        <button
          type="button"
          data-testid="turn-summary-review"
          onClick={() => {
            setTab("changes");
            openPanel(true);
          }}
          className="inline-flex items-center gap-1.5 rounded-lg border border-border px-2.5 py-1 text-foreground hover:bg-elevated"
        >
          <FileDiff className="h-3.5 w-3.5" aria-hidden="true" />
          Review changes
        </button>
      )}
      {summary.pullRequests.map((pr, index) => (
        <PullRequestLink key={pullRequestLabel(pr)} pr={pr} primary={index === summary.pullRequests.length - 1} />
      ))}
      {earlier.length > 0 && (
        <details data-testid="turn-summary-earlier-prs" className="relative">
          <summary className="cursor-pointer list-none rounded-lg border border-border px-2.5 py-1 text-neutral-500 hover:bg-elevated">
            {earlier.length} other pull request{earlier.length === 1 ? "" : "s"} in this session
          </summary>
          <div className="absolute right-0 z-10 mt-1 flex min-w-48 flex-col gap-1.5 rounded-xl border border-border bg-surface p-2 shadow-lg">
            {earlier.map((pr) => (
              <PullRequestLink key={pullRequestLabel(pr)} pr={pr} primary={false} />
            ))}
          </div>
        </details>
      )}
    </div>
  );
};

"use client";

import React, { useState } from "react";
import { Archive, ChevronRight } from "lucide-react";
import { MarkdownRenderer } from "./MarkdownRenderer";
import { cn } from "./ui/cn";

/** A system message the engine wrote when it compacted the context ("Context compacted: …"). */
export const isCompactionMessage = (content: string): boolean => /^\s*context compact(ed|ion)\b/i.test(content);

/** The headline (what was compressed) and the summary that follows it, if any. */
export function parseCompaction(content: string): { headline: string; summary: string } {
  const [first, ...rest] = content.trim().split(/\n\s*\n/);
  return {
    headline: first.replace(/^\s*context compact(ed|ion):?\s*/i, "").replace(/\s+/g, " ").trim(),
    summary: rest.join("\n\n").trim(),
  };
}

/**
 * The record that the working context was compacted. The summary the agent carries on from can be
 * long, so it is folded away: what happened is one line, the summary is there when you want it.
 */
export const CompactionCard: React.FC<{ content: string; headline?: string; summary?: string }> = ({
  content,
  headline: givenHeadline,
  summary: givenSummary,
}) => {
  // A live event hands its fields over; a message restored from history is parsed from its text.
  const parsed = givenHeadline === undefined ? parseCompaction(content) : null;
  const headline = givenHeadline ?? parsed?.headline ?? "";
  const summary = givenSummary ?? parsed?.summary ?? "";
  const [open, setOpen] = useState(false);
  const lines = summary ? summary.split("\n").length : 0;

  return (
    <div data-testid="compaction-card" className="my-3 flex justify-center">
      <div className="w-full min-w-0 max-w-3xl rounded-xl border border-emerald-500/40 bg-emerald-500/10 p-3 text-foreground">
        <div className="flex items-start gap-2.5">
          <Archive className="mt-0.5 h-4 w-4 shrink-0 text-emerald-700 dark:text-emerald-400" aria-hidden="true" />
          <div className="min-w-0 flex-1">
            <p className="text-xs font-semibold uppercase tracking-wide text-emerald-800 dark:text-emerald-300">Context Compacted</p>
            {headline && (
              <p data-testid="compaction-headline" className="mt-0.5 text-xs text-neutral-700 dark:text-neutral-300">
                {headline}
              </p>
            )}
          </div>
          <span className="shrink-0 font-mono text-[10px] text-emerald-800 dark:text-emerald-400/80">Checkpoint saved</span>
        </div>

        {summary && (
          <div className="mt-2">
            <button
              type="button"
              data-testid="compaction-toggle"
              aria-expanded={open}
              onClick={() => setOpen((value) => !value)}
              className="inline-flex cursor-pointer items-center gap-1 rounded-md py-0.5 text-[11px] text-neutral-600 hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand/60 dark:text-neutral-400"
            >
              <ChevronRight className={cn("h-3.5 w-3.5 transition-transform", open && "rotate-90")} aria-hidden="true" />
              {open ? "Hide the summary" : `Show the summary the agent continued from (${lines} lines)`}
            </button>
            {open && (
              <div
                data-testid="compaction-summary"
                className="mt-2 max-h-80 overflow-y-auto rounded-lg border border-emerald-500/20 bg-surface-base/40 p-3 font-sans text-xs leading-relaxed text-neutral-700 dark:text-neutral-300"
              >
                <MarkdownRenderer content={summary} />
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
};

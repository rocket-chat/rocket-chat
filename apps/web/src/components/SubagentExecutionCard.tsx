"use client";

import React from "react";
import { Loader2 } from "lucide-react";
import type { SubagentExecutionData } from "../types/mission";
import { MarkdownRenderer } from "./MarkdownRenderer";

/** What a delegated specialist was asked and what it found, shown when its row is opened. */
export const SubagentExecutionCard: React.FC<{ subagent: SubagentExecutionData }> = ({ subagent }) => (
  <div className="space-y-3 text-sm">
    <div className="space-y-1">
      <h4 className="text-xs font-medium text-neutral-600 dark:text-neutral-400">Task</h4>
      <p className="rounded-lg border border-surface-border bg-surface-base px-3 py-2 text-neutral-700 dark:text-neutral-300">
        {subagent.task}
      </p>
    </div>

    {subagent.thought && (
      <div className="space-y-1">
        <h4 className="text-xs font-medium text-neutral-600 dark:text-neutral-400">Reasoning</h4>
        <div className="border-l-2 border-brand/40 pl-3 text-neutral-600 dark:text-neutral-400">
          <MarkdownRenderer content={subagent.thought} />
        </div>
      </div>
    )}

    {subagent.summary ? (
      <div className="space-y-1">
        <h4 className="text-xs font-medium text-neutral-600 dark:text-neutral-400">Findings</h4>
        <div className="rounded-lg border border-surface-border bg-surface-base px-3 py-2.5 leading-relaxed text-foreground">
          <MarkdownRenderer content={subagent.summary} />
        </div>
      </div>
    ) : (
      subagent.status === "running" && (
        <p className="flex items-center gap-2 text-xs text-neutral-500">
          <Loader2 className="h-3.5 w-3.5 animate-spin text-brand" />
          Working in its own context…
        </p>
      )
    )}
  </div>
);

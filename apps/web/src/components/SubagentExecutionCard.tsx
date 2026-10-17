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

    {subagent.changed_files && subagent.changed_files.length > 0 && (
      <div className="space-y-1">
        <h4 className="text-xs font-medium text-neutral-600 dark:text-neutral-400">Modified Files</h4>
        <div className="flex flex-wrap gap-1.5 font-mono text-[11px]">
          {subagent.changed_files.map((file, idx) => (
            <span key={idx} className="rounded-md border border-surface-border bg-surface-elevated px-2 py-0.5 text-neutral-400">
              {file}
            </span>
          ))}
        </div>
      </div>
    )}

    {subagent.artefacts && subagent.artefacts.length > 0 && (
      <div className="space-y-1">
        <h4 className="text-xs font-medium text-neutral-600 dark:text-neutral-400">Artefacts</h4>
        <div className="flex flex-wrap gap-1.5 font-mono text-[11px]">
          {subagent.artefacts.map((art, idx) => (
            <span key={idx} className="rounded-md border border-emerald-500/30 bg-emerald-500/10 px-2 py-0.5 text-emerald-300">
              {art}
            </span>
          ))}
        </div>
      </div>
    )}

    <div className="flex flex-wrap items-center gap-3 pt-1 text-[11px] font-mono text-neutral-500">
      {typeof subagent.duration_ms === "number" && (
        <span>{subagent.duration_ms}ms</span>
      )}
      {typeof subagent.tokens === "number" && subagent.tokens > 0 && (
        <span>{subagent.tokens} tokens</span>
      )}
      {typeof subagent.cost === "number" && subagent.cost > 0 && (
        <span className="text-emerald-400">${subagent.cost.toFixed(4)}</span>
      )}
      <span className="capitalize">{subagent.status}</span>
    </div>
  </div>
);

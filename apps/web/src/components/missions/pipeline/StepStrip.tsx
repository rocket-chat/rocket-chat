"use client";

import React from "react";
import { ArrowRight, Bot, ClipboardList, Send, Zap, type LucideIcon } from "lucide-react";

export type MissionStep = "when" | "agent" | "task" | "deliver";

const STEPS: { id: MissionStep; title: string; icon: LucideIcon }[] = [
  { id: "when", title: "When", icon: Zap },
  { id: "agent", title: "Agent", icon: Bot },
  { id: "task", title: "Task", icon: ClipboardList },
  { id: "deliver", title: "Deliver", icon: Send },
];

/** The mission as a flow of four steps, each summarised; choosing one opens it below. */
export const StepStrip: React.FC<{
  active: MissionStep;
  summaries: Record<MissionStep, string>;
  onChoose: (step: MissionStep) => void;
}> = ({ active, summaries, onChoose }) => (
  <nav aria-label="Mission steps">
    <ol className="grid gap-2 sm:grid-cols-[1fr_auto_1fr_auto_1fr_auto_1fr] sm:items-stretch">
      {STEPS.map(({ id, title, icon: Icon }, index) => (
        <React.Fragment key={id}>
          {index > 0 && (
            <li aria-hidden="true" className="hidden items-center justify-center text-neutral-400 sm:flex">
              <ArrowRight className="h-4 w-4" />
            </li>
          )}
          <li className="min-w-0">
            <button
              type="button"
              data-testid={`mission-step-${id}`}
              aria-current={active === id ? "step" : undefined}
              onClick={() => onChoose(id)}
              className={`flex h-full w-full min-w-0 flex-col gap-1 rounded-lg border p-3 text-left transition-colors ${
                active === id
                  ? "border-brand bg-brand/10"
                  : "border-surface-border bg-surface-card hover:border-neutral-400"
              }`}
            >
              <span className="flex items-center gap-1.5 text-xs font-semibold text-foreground">
                <Icon className={`h-3.5 w-3.5 ${active === id ? "text-brand" : "text-neutral-500"}`} aria-hidden="true" />
                {title}
              </span>
              <span data-testid={`mission-step-${id}-summary`} className="line-clamp-2 text-[11px] leading-snug text-neutral-600 dark:text-neutral-400">
                {summaries[id]}
              </span>
            </button>
          </li>
        </React.Fragment>
      ))}
    </ol>
  </nav>
);

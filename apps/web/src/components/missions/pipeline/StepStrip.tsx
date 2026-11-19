"use client";

import React from "react";
import { ArrowRight, Bot, CircleAlert, CircleCheck, ClipboardList, Send, Zap, type LucideIcon } from "lucide-react";
import type { MissionStep } from "../../../lib/missions";

export const STEPS: { id: MissionStep; title: string; icon: LucideIcon }[] = [
  { id: "when", title: "When", icon: Zap },
  { id: "agent", title: "Agent", icon: Bot },
  { id: "task", title: "Task", icon: ClipboardList },
  { id: "deliver", title: "Deliver", icon: Send },
];

/** The mission as a flow of four steps, each summarised and marked done or to finish; choosing one opens it below. */
export const StepStrip: React.FC<{
  active: MissionStep;
  summaries: Record<MissionStep, string>;
  problems: Record<MissionStep, string | null>;
  onChoose: (step: MissionStep) => void;
}> = ({ active, summaries, problems, onChoose }) => (
  <nav aria-label="Mission steps">
    <ol className="grid gap-2 sm:grid-cols-[1fr_auto_1fr_auto_1fr_auto_1fr] sm:items-stretch">
      {STEPS.map(({ id, title, icon: Icon }, index) => {
        const done = problems[id] === null;
        const State = done ? CircleCheck : CircleAlert;
        return (
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
                data-complete={done}
                aria-current={active === id ? "step" : undefined}
                onClick={() => onChoose(id)}
                className={`flex h-full w-full min-w-0 flex-col gap-1 rounded-lg border p-3 text-left transition-colors ${
                  active === id ? "border-brand bg-brand/10" : "border-surface-border bg-surface-card hover:border-neutral-400"
                }`}
              >
                <span className="flex items-center gap-1.5 text-xs font-semibold text-foreground">
                  <Icon className={`h-3.5 w-3.5 ${active === id ? "text-brand" : "text-neutral-500"}`} aria-hidden="true" />
                  {index + 1}. {title}
                  <State
                    className={`ml-auto h-3.5 w-3.5 ${done ? "text-emerald-600 dark:text-emerald-400" : "text-amber-600 dark:text-amber-400"}`}
                    aria-hidden="true"
                  />
                  <span className="sr-only">{done ? "(complete)" : "(to finish)"}</span>
                </span>
                <span data-testid={`mission-step-${id}-summary`} className="line-clamp-2 text-[11px] leading-snug text-neutral-600 dark:text-neutral-400">
                  {summaries[id]}
                </span>
              </button>
            </li>
          </React.Fragment>
        );
      })}
    </ol>
  </nav>
);

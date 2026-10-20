"use client";

import React from "react";
import { CheckCircle2, Circle, CircleDashed, ListChecks, XCircle } from "lucide-react";
import { useMissionStore } from "../lib/store";
import { ChecklistTaskStatus } from "../types/mission";

const STATUS_ICON: Record<ChecklistTaskStatus, React.ReactNode> = {
  pending: <Circle className="w-4 h-4 text-muted-foreground" aria-hidden />,
  in_progress: <CircleDashed className="w-4 h-4 text-primary animate-spin" aria-hidden />,
  completed: <CheckCircle2 className="w-4 h-4 text-emerald-500" aria-hidden />,
  failed: <XCircle className="w-4 h-4 text-red-500" aria-hidden />,
};

const STATUS_LABEL: Record<ChecklistTaskStatus, string> = {
  pending: "Pending",
  in_progress: "In progress",
  completed: "Completed",
  failed: "Failed",
};

export const TaskChecklist: React.FC = () => {
  const checklist = useMissionStore((state) => state.checklist);
  const goal = useMissionStore((state) => state.goal);
  if (checklist.length === 0) return null;

  const completedCount = checklist.filter((task) => task.status === "completed").length;
  const current = checklist.find((task) => task.status === "in_progress") ?? checklist.find((task) => task.status === "pending");
  const allDone = completedCount === checklist.length;

  return (
    <details
      // Open while there is work left to follow; a finished list folds itself away.
      open={!allDone}
      data-testid="task-checklist"
      className="mx-auto w-full max-w-3xl shrink-0 rounded-xl border border-border bg-surface/95 shadow-sm"
    >
      <summary className="flex cursor-pointer select-none items-center gap-2 px-3 py-2 text-xs font-semibold text-foreground">
        <ListChecks className="w-4 h-4 text-primary" aria-hidden />
        <span>Tasks</span>
        {current && !allDone && <span className="min-w-0 flex-1 truncate font-normal text-muted-foreground">Now: {current.title}</span>}
        {allDone && <span className="flex-1 font-normal text-emerald-600 dark:text-emerald-400">All done</span>}
        {goal && goal.status === "active" && (
          <span data-testid="goal-inline" className="hidden shrink-0 font-normal text-muted-foreground sm:inline">
            {goal.continuationCount}/{goal.maxContinuations || "∞"} continuations
          </span>
        )}
        <span className="ml-auto shrink-0 font-mono text-[11px] text-muted-foreground">
          {completedCount}/{checklist.length}
        </span>
      </summary>
      <ul className="max-h-48 space-y-1 overflow-y-auto px-3 pb-3">
        {checklist.map((task) => (
          <li key={task.id} className="flex items-start gap-2 text-xs" data-status={task.status}>
            <span className="mt-0.5 shrink-0" title={STATUS_LABEL[task.status]}>
              {STATUS_ICON[task.status] ?? STATUS_ICON.pending}
            </span>
            <span
              className={
                task.status === "completed"
                  ? "text-muted-foreground line-through"
                  : "text-foreground"
              }
            >
              {task.title}
              <span className="sr-only"> ({STATUS_LABEL[task.status]})</span>
            </span>
          </li>
        ))}
      </ul>
    </details>
  );
};

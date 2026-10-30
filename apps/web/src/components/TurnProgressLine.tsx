"use client";

import React, { useEffect, useState } from "react";
import { useShallow } from "zustand/react/shallow";
import { useMissionStore } from "../lib/store";
import { formatElapsed } from "../lib/elapsed";
import { describeToolCall } from "../lib/activity";

/** Below this, a turn is quick enough that the playful line is all it needs. */
export const QUICK_TURN_MS = 10_000;
/** Silence shorter than this is normal between events and is not worth mentioning. */
const QUIET_AFTER_MS = 5_000;

/** Re-renders every second while ``active``, returning the current time. */
export function useNow(active: boolean): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!active) return;
    setNow(Date.now());
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [active]);
  return now;
}

/**
 * How a running turn is going, in one line: how long it has run, which step it is on, the
 * tool running now and for how long, how long since anything happened, and the model.
 */
export const TurnProgressLine: React.FC<{ now: number }> = ({ now }) => {
  const { progress, binding } = useMissionStore(
    useShallow((state) => ({ progress: state.turnProgress, binding: state.turnBinding }))
  );
  if (progress.startedAt === null) return null;

  const parts: string[] = [formatElapsed(now - progress.startedAt)];
  if (progress.step !== null) parts.push(`step ${progress.step}`);
  if (progress.currentTool) {
    const step = describeToolCall({ name: progress.currentTool.name, arguments: {} });
    parts.push(`${step.doing} · ${formatElapsed(now - progress.currentTool.startedAt)}`);
  }
  if (progress.lastActivityAt !== null && now - progress.lastActivityAt >= QUIET_AFTER_MS) {
    parts.push(`last activity ${formatElapsed(now - progress.lastActivityAt)} ago`);
  }
  if (binding?.model) parts.push(binding.model.split("/").pop() || binding.model);

  return (
    <div data-testid="turn-progress" className="truncate font-mono text-[11px] text-neutral-500 dark:text-neutral-400">
      {parts.join(" · ")}
    </div>
  );
};

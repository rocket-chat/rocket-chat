"use client";

import React, { useEffect, useRef } from "react";
import { useMissionStore } from "../lib/store";

const VISIBLE_LINES = 6;
// Terminal colour and cursor sequences: meaningful in the terminal pane, noise in a text block.
 
const ANSI = /\u001b\[[0-9;?]*[A-Za-z]/g;

/** The latest lines a running command printed, shown under its step until the step finishes. */
export const LiveToolOutput: React.FC<{ callId?: string }> = ({ callId }) => {
  const raw = useMissionStore((state) => (callId ? state.liveToolOutput[callId] : undefined));
  const ref = useRef<HTMLPreElement>(null);
  const text = raw ? raw.replace(ANSI, "").replace(/\r(?!\n)/g, "\n").replace(/\r/g, "") : "";
  const tail = text.split("\n").filter((line) => line.trim() !== "").slice(-VISIBLE_LINES).join("\n");

  useEffect(() => {
    if (ref.current) ref.current.scrollTop = ref.current.scrollHeight;
  }, [tail]);

  if (!tail) return null;
  return (
    <pre
      ref={ref}
      data-testid="tool-live-output"
      aria-label="Live command output"
      className="ml-6 mt-1 max-h-28 overflow-auto whitespace-pre-wrap rounded-lg border border-surface-border bg-surface-base px-3 py-2 font-mono text-[11px] leading-4 text-neutral-600 dark:text-neutral-400"
    >
      {tail}
    </pre>
  );
};

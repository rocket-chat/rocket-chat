"use client";

import React, { useState } from "react";
import { cn } from "./ui/cn";

const COLLAPSED_LINES = 12;

interface InlineDiffProps {
  diff: string;
  onOpenInInspector?: () => void;
}

const lineTone = (line: string): string => {
  if (line.startsWith("+") && !line.startsWith("+++")) return "bg-diff-add-bg text-diff-add";
  if (line.startsWith("-") && !line.startsWith("---")) return "bg-diff-del-bg text-diff-del";
  if (line.startsWith("@@")) return "text-cyan";
  return "text-neutral-500 dark:text-neutral-400";
};

/** A compact unified diff: a dozen lines by default, the rest one click away. */
export const InlineDiff: React.FC<InlineDiffProps> = ({ diff, onOpenInInspector }) => {
  const [expanded, setExpanded] = useState(false);
  const lines = diff.replace(/\n$/, "").split("\n");
  const shown = expanded ? lines : lines.slice(0, COLLAPSED_LINES);
  const hidden = lines.length - shown.length;

  return (
    <div data-testid="inline-diff" className="overflow-hidden rounded-lg border border-surface-border bg-surface-base">
      <pre className="max-h-80 overflow-auto py-1.5 font-mono text-[11.5px] leading-5">
        {shown.map((line, index) => (
          <div key={index} className={cn("whitespace-pre px-3", lineTone(line))}>
            {line || " "}
          </div>
        ))}
      </pre>
      {(hidden > 0 || onOpenInInspector) && (
        <div className="flex items-center justify-between border-t border-surface-border px-3 py-1.5 text-xs">
          {hidden > 0 ? (
            <button
              type="button"
              onClick={() => setExpanded(true)}
              className="cursor-pointer text-neutral-600 hover:text-foreground dark:text-neutral-400"
            >
              Show {hidden} more {hidden === 1 ? "line" : "lines"}
            </button>
          ) : (
            <span />
          )}
          {onOpenInInspector && (
            <button
              type="button"
              onClick={onOpenInInspector}
              className="cursor-pointer text-brand hover:underline"
            >
              Open in inspector
            </button>
          )}
        </div>
      )}
    </div>
  );
};

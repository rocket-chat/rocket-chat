"use client";

import React, { useState } from "react";
import { Check, Copy } from "lucide-react";
import type { FileDiffItem, ToolCallData } from "../types/mission";
import { describeToolCall, synthesizeDiff } from "../lib/activity";
import { InlineDiff } from "./InlineDiff";
import { Disclosure } from "./ui/Disclosure";
import { cn } from "./ui/cn";

const OUTPUT_PREVIEW_LINES = 12;

interface ToolExecutionCardProps {
  toolCall: ToolCallData;
  /** The diff the server recorded for this edit, when there is one. */
  recordedDiff?: FileDiffItem;
  onOpenInInspector?: () => void;
}

const CopyButton: React.FC<{ text: string; label: string }> = ({ text, label }) => {
  const [copied, setCopied] = useState(false);
  return (
    <button
      type="button"
      aria-label={label}
      title={label}
      onClick={() => {
        void navigator.clipboard.writeText(text);
        setCopied(true);
        setTimeout(() => setCopied(false), 1500);
      }}
      className="cursor-pointer rounded p-1 text-neutral-500 hover:text-foreground"
    >
      {copied ? <Check className="h-3.5 w-3.5 text-diff-add" /> : <Copy className="h-3.5 w-3.5" />}
    </button>
  );
};

const OutputBlock: React.FC<{ text: string; failed: boolean }> = ({ text, failed }) => {
  const [showAll, setShowAll] = useState(false);
  const lines = text.split("\n");
  const clipped = !showAll && lines.length > OUTPUT_PREVIEW_LINES;
  const shown = clipped ? lines.slice(0, OUTPUT_PREVIEW_LINES).join("\n") : text;
  return (
    <div className="overflow-hidden rounded-lg border border-surface-border bg-surface-base">
      <pre
        className={cn(
          "max-h-72 overflow-auto whitespace-pre-wrap px-3 py-2 font-mono text-[11.5px] leading-5",
          failed ? "text-diff-del" : "text-neutral-700 dark:text-neutral-300"
        )}
      >
        {shown}
      </pre>
      {clipped && (
        <button
          type="button"
          onClick={() => setShowAll(true)}
          className="w-full cursor-pointer border-t border-surface-border px-3 py-1.5 text-left text-xs text-neutral-600 hover:text-foreground dark:text-neutral-400"
        >
          Show all {lines.length} lines
        </button>
      )}
    </div>
  );
};

/** What a single tool call did, shown only once its row is opened: command, diff or output. */
export const ToolExecutionCard: React.FC<ToolExecutionCardProps> = ({ toolCall, recordedDiff, onOpenInInspector }) => {
  const step = describeToolCall(toolCall);
  const failed = toolCall.status === "error";
  const output = toolCall.result || toolCall.stdout || "";
  const diffText = step.kind === "edit" ? (recordedDiff?.diff_content ?? synthesizeDiff(toolCall)) : null;
  const argsJson = JSON.stringify(toolCall.arguments ?? {}, null, 2);

  return (
    <div className="space-y-2.5 text-sm">
      <div className="flex items-center gap-2 text-xs text-neutral-500 dark:text-neutral-400">
        <code className="rounded bg-surface-elevated px-1.5 py-0.5 font-mono text-[11px]">{toolCall.name}</code>
        {toolCall.duration_ms !== undefined && <span>{toolCall.duration_ms} ms</span>}
        {toolCall.exit_code !== undefined && toolCall.exit_code !== 0 && <span>exit {toolCall.exit_code}</span>}
      </div>

      {step.kind === "run" && step.target && (
        <div className="flex items-center justify-between gap-2 rounded-lg border border-surface-border bg-surface-base px-3 py-2 font-mono text-[12px]">
          <span className="min-w-0 overflow-x-auto whitespace-pre text-foreground">
            <span className="mr-2 text-brand">$</span>
            {typeof toolCall.arguments?.command === "string" ? toolCall.arguments.command : step.target}
          </span>
          <CopyButton
            text={typeof toolCall.arguments?.command === "string" ? toolCall.arguments.command : step.target}
            label="Copy command"
          />
        </div>
      )}

      {diffText && <InlineDiff diff={diffText} onOpenInInspector={recordedDiff ? onOpenInInspector : undefined} />}

      {output ? (
        <div className="space-y-1">
          <div className="flex items-center justify-between">
            <span className="text-xs font-medium text-neutral-600 dark:text-neutral-400">
              {failed ? "Error" : "Output"}
            </span>
            <CopyButton text={output} label="Copy output" />
          </div>
          <OutputBlock text={output} failed={failed} />
        </div>
      ) : (
        toolCall.status === "pending" && <p className="text-xs text-neutral-500">Waiting for output…</p>
      )}

      {step.kind !== "run" && step.kind !== "edit" && (
        <Disclosure summary="Arguments">
          <pre className="max-h-48 overflow-auto rounded-lg border border-surface-border bg-surface-base px-3 py-2 font-mono text-[11.5px] leading-5">
            {argsJson}
          </pre>
        </Disclosure>
      )}
    </div>
  );
};

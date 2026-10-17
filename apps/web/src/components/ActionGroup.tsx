"use client";

import React, { useState } from "react";
import {
  AlertCircle,
  Bot,
  ChevronRight,
  FilePen,
  FileSearch,
  Globe,
  GitBranch,
  Loader2,
  Search,
  Sparkles,
  Terminal,
  Wrench,
} from "lucide-react";
import type { FlightLogEntry } from "../types/mission";
import { useMissionStore } from "../lib/store";
import {
  type ActivityKind,
  describeToolCall,
  diffCounts,
  isFailed,
  isPending,
  summarizeGroup,
} from "../lib/activity";
import { ToolExecutionCard } from "./ToolExecutionCard";
import { SubagentExecutionCard } from "./SubagentExecutionCard";
import { MarkdownRenderer } from "./MarkdownRenderer";
import { Badge } from "./ui/Badge";
import { cn } from "./ui/cn";

interface ActionGroupProps {
  id: string;
  entries: FlightLogEntry[];
  isActive?: boolean;
}

const KIND_ICON: Record<ActivityKind, React.ComponentType<{ className?: string }>> = {
  run: Terminal,
  read: FileSearch,
  edit: FilePen,
  search: Search,
  git: GitBranch,
  web: Globe,
  other: Wrench,
};

interface StepRowProps {
  icon: React.ReactNode;
  label: React.ReactNode;
  meta?: React.ReactNode;
  tone?: "default" | "failed";
  title?: string;
  children: React.ReactNode;
}

/** One line per step; the detail stays closed until asked for. */
const StepRow: React.FC<StepRowProps> = ({ icon, label, meta, tone = "default", title, children }) => {
  const [open, setOpen] = useState(false);
  return (
    <li>
      <button
        type="button"
        data-testid="activity-step"
        aria-expanded={open}
        title={title}
        onClick={() => setOpen((value) => !value)}
        className="group flex w-full cursor-pointer items-center gap-2.5 rounded-lg px-2 py-1.5 text-left text-sm transition-colors hover:bg-surface-elevated/70 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand/60"
      >
        <span className={cn("shrink-0", tone === "failed" ? "text-diff-del" : "text-neutral-500 dark:text-neutral-400")}>
          {icon}
        </span>
        <span className="min-w-0 flex-1 truncate text-neutral-700 dark:text-neutral-300" title={title}>{label}</span>
        {meta && <span className="flex shrink-0 items-center gap-2 text-xs text-neutral-500 dark:text-neutral-400">{meta}</span>}
        <ChevronRight
          aria-hidden="true"
          className={cn("h-3.5 w-3.5 shrink-0 text-neutral-400 transition-transform", open && "rotate-90")}
        />
      </button>
      {open && <div className="ml-[1.65rem] border-l border-surface-border py-2 pl-4">{children}</div>}
    </li>
  );
};

const DiffChips: React.FC<{ additions: number; deletions: number }> = ({ additions, deletions }) => (
  <span className="font-mono text-[11px]">
    <span className="text-diff-add">+{additions}</span> <span className="text-diff-del">−{deletions}</span>
  </span>
);

const firstLine = (text: string): string => {
  const line = text.replace(/[#*_`>]/g, "").split("\n").find((l) => l.trim()) ?? "";
  return line.length > 140 ? `${line.slice(0, 139)}…` : line;
};

export const ActionGroup: React.FC<ActionGroupProps> = ({ entries, isActive = false }) => {
  const [isOpen, setIsOpen] = useState(false);
  const fileDiffs = useMissionStore((state) => state.fileDiffs);
  const setActiveDiffIndex = useMissionStore((state) => state.setActiveDiffIndex);
  const setIsRightPanelOpen = useMissionStore((state) => state.setIsRightPanelOpen);

  const summary = summarizeGroup(entries);

  const openInInspector = (path: string | undefined) => {
    const index = fileDiffs.findIndex((diff) => diff.path === path);
    if (index < 0) return;
    setActiveDiffIndex(index);
    setIsRightPanelOpen(true);
  };

  return (
    <div className="my-1 font-sans text-sm">
      <button
        type="button"
        data-testid="action-group-toggle"
        onClick={() => setIsOpen((open) => !open)}
        aria-expanded={isOpen}
        className="group inline-flex max-w-full cursor-pointer items-center gap-2 rounded-lg px-2 py-1 text-left transition-colors hover:bg-surface-elevated/70 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand/60"
      >
        {isActive ? (
          <Loader2 aria-hidden="true" className="h-3.5 w-3.5 shrink-0 animate-spin text-brand" />
        ) : (
          <ChevronRight
            aria-hidden="true"
            className={cn("h-3.5 w-3.5 shrink-0 text-neutral-400 transition-transform", isOpen && "rotate-90")}
          />
        )}
        <span
          className="truncate text-neutral-600 dark:text-neutral-400 group-hover:text-foreground"
          title={isActive ? (summary.current ?? "Working…") : summary.text}
        >
          {isActive ? <span className="text-foreground">{summary.current ?? "Working…"}</span> : summary.text}
        </span>
        {isActive && summary.text !== "Thought it through" && (
          <span className="hidden truncate text-neutral-500 sm:inline" title={summary.text}>· {summary.text}</span>
        )}
        {summary.failed > 0 && <Badge tone="danger">{summary.failed} failed</Badge>}
      </button>

      {isOpen && (
        <ul className="mt-1 ml-1 space-y-0.5">
          {entries.map((entry) => (
            <React.Fragment key={entry.id}>
              {entry.reasoning && (
                <StepRow
                  icon={<Sparkles className="h-3.5 w-3.5" />}
                  title={entry.reasoning.trim()}
                  label={
                    <>
                      <span className="text-neutral-600 dark:text-neutral-400">Thought</span>
                      <span className="ml-2 text-neutral-500" title={entry.reasoning.trim()}>{firstLine(entry.reasoning)}</span>
                    </>
                  }
                  meta={
                    entry.timestamp ? (
                      <span suppressHydrationWarning className="font-mono text-[10px] text-neutral-500/70">
                        {entry.timestamp}
                      </span>
                    ) : undefined
                  }
                >
                  <div className="text-sm leading-relaxed text-neutral-600 dark:text-neutral-400">
                    <MarkdownRenderer content={entry.reasoning} />
                  </div>
                </StepRow>
              )}

              {entry.toolCall && (
                <ToolRow
                  call={entry.toolCall}
                  recordedDiff={fileDiffs.find((diff) => diff.path === describeToolCall(entry.toolCall!).path)}
                  onOpenInInspector={() => openInInspector(describeToolCall(entry.toolCall!).path)}
                  timestamp={entry.timestamp}
                />
              )}

              {entry.subagent && (
                <StepRow
                  icon={
                    entry.subagent.status === "running" ? (
                      <Loader2 className="h-3.5 w-3.5 animate-spin text-brand" />
                    ) : entry.subagent.status === "error" ? (
                      <AlertCircle className="h-3.5 w-3.5" />
                    ) : (
                      <Bot className="h-3.5 w-3.5" />
                    )
                  }
                  tone={entry.subagent.status === "error" ? "failed" : "default"}
                  title={`Delegated to ${entry.subagent.role.replace(/_/g, " ")}: ${entry.subagent.task}`}
                  label={
                    <>
                      <span>Delegated to </span>
                      <span className="font-medium text-foreground">{entry.subagent.role.replace(/_/g, " ")}</span>
                      <span className="ml-2 text-neutral-500" title={entry.subagent.task}>{entry.subagent.task}</span>
                    </>
                  }
                  meta={
                    <>
                      {entry.subagent.duration_ms ? <span>{(entry.subagent.duration_ms / 1000).toFixed(1)}s</span> : null}
                      {entry.timestamp ? (
                        <span suppressHydrationWarning className="font-mono text-[10px] text-neutral-500/70">
                          {entry.timestamp}
                        </span>
                      ) : null}
                    </>
                  }
                >
                  <SubagentExecutionCard subagent={entry.subagent} />
                </StepRow>
              )}
            </React.Fragment>
          ))}
        </ul>
      )}
    </div>
  );
};

const ToolRow: React.FC<{
  call: NonNullable<FlightLogEntry["toolCall"]>;
  recordedDiff?: React.ComponentProps<typeof ToolExecutionCard>["recordedDiff"];
  onOpenInInspector: () => void;
  timestamp?: string;
}> = ({ call, recordedDiff, onOpenInInspector, timestamp }) => {
  const step = describeToolCall(call);
  const Icon = KIND_ICON[step.kind];
  const failed = isFailed(call);
  const counts = step.kind === "edit" ? diffCounts(call, recordedDiff) : null;
  const toolTitle = step.target
    ? `${isPending(call) ? step.doing : step.done}: ${step.target}`
    : isPending(call)
      ? step.doing
      : step.done;

  return (
    <StepRow
      tone={failed ? "failed" : "default"}
      icon={isPending(call) ? <Loader2 className="h-3.5 w-3.5 animate-spin text-brand" /> : failed ? <AlertCircle className="h-3.5 w-3.5" /> : <Icon className="h-3.5 w-3.5" />}
      title={toolTitle}
      label={
        <>
          <span>{isPending(call) ? step.doing : step.done}</span>
          {step.target && (
            <code className="ml-2 rounded bg-surface-elevated px-1.5 py-0.5 font-mono text-[12px] text-neutral-700 dark:text-neutral-300" title={step.target}>
              {step.target}
            </code>
          )}
        </>
      }
      meta={
        <>
          {counts && <DiffChips additions={counts.additions} deletions={counts.deletions} />}
          {failed && <span className="text-diff-del">failed</span>}
          {timestamp && (
            <span suppressHydrationWarning className="font-mono text-[10px] text-neutral-500/70">
              {timestamp}
            </span>
          )}
        </>
      }
    >
      <ToolExecutionCard toolCall={call} recordedDiff={recordedDiff} onOpenInInspector={onOpenInInspector} />
    </StepRow>
  );
};

"use client";

import React, { useState } from "react";
import { ChevronRight, GitBranch, Loader2 } from "lucide-react";
import type { SubagentExecutionData, SubagentFinding, SubagentReport, SubagentStep } from "../types/mission";
import { MarkdownRenderer } from "./MarkdownRenderer";
import { formatElapsed } from "../lib/elapsed";

const SEVERITY_ORDER = ["critical", "high", "medium", "low", "info"];
const SEVERITY_STYLE: Record<string, string> = {
  critical: "border-red-500/40 bg-red-500/10 text-red-700 dark:text-red-300",
  high: "border-orange-500/40 bg-orange-500/10 text-orange-700 dark:text-orange-300",
  medium: "border-amber-500/40 bg-amber-500/10 text-amber-800 dark:text-amber-300",
  low: "border-sky-500/40 bg-sky-500/10 text-sky-800 dark:text-sky-300",
  info: "border-surface-border bg-surface-elevated text-neutral-700 dark:text-neutral-300",
};
export const SUBAGENT_STATUS_LABEL: Record<string, string> = {
  running: "Running",
  completed: "Completed",
  partial: "Partial",
  blocked: "Blocked",
  error: "Failed",
  cancelled: "Cancelled",
};

const Section: React.FC<{ title: string; children: React.ReactNode }> = ({ title, children }) => (
  <div className="space-y-1">
    <h4 className="text-xs font-medium text-neutral-600 dark:text-neutral-400">{title}</h4>
    {children}
  </div>
);

const StepRow: React.FC<{ step: SubagentStep }> = ({ step }) => {
  const [open, setOpen] = useState(false);
  const tone =
    step.status === "error"
      ? "text-red-700 dark:text-red-300"
      : step.status === "pending"
        ? "text-brand"
        : "text-neutral-600 dark:text-neutral-400";
  return (
    <li>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="flex w-full items-center gap-1.5 rounded px-1 py-0.5 text-left font-mono text-[11px] hover:bg-surface-elevated"
      >
        <ChevronRight className={`h-3 w-3 shrink-0 transition-transform ${open ? "rotate-90" : ""}`} aria-hidden />
        {step.status === "pending" && <Loader2 className="h-3 w-3 animate-spin text-brand" aria-hidden />}
        <span className={tone}>{step.name}</span>
        {typeof step.duration_ms === "number" && (
          <span className="ml-auto text-neutral-500">{formatElapsed(step.duration_ms)}</span>
        )}
      </button>
      {open && (
        <pre className="mt-1 max-h-48 overflow-auto whitespace-pre-wrap rounded border border-surface-border bg-surface-base p-2 text-[11px] text-neutral-700 dark:text-neutral-300">
          {JSON.stringify(step.arguments ?? {}, null, 2)}
          {step.result ? `\n\n${step.result}` : ""}
        </pre>
      )}
    </li>
  );
};

const Findings: React.FC<{ findings: SubagentFinding[] }> = ({ findings }) => {
  const ranked = [...findings].sort(
    (a, b) => SEVERITY_ORDER.indexOf(a.severity) - SEVERITY_ORDER.indexOf(b.severity)
  );
  return (
    <ul className="space-y-1.5">
      {ranked.map((finding, idx) => (
        <li key={idx} className="rounded-lg border border-surface-border bg-surface-base px-3 py-2">
          <div className="flex flex-wrap items-center gap-2 text-[11px]">
            <span className={`rounded border px-1.5 py-0.5 font-medium uppercase ${SEVERITY_STYLE[finding.severity] ?? SEVERITY_STYLE.info}`}>
              {finding.severity}
            </span>
            {finding.file && (
              <span className="font-mono text-neutral-600 dark:text-neutral-400">
                {finding.file}
                {finding.line ? `:${finding.line}` : ""}
              </span>
            )}
          </div>
          <p className="mt-1 whitespace-pre-wrap text-neutral-700 dark:text-neutral-300">{finding.evidence}</p>
        </li>
      ))}
    </ul>
  );
};

const TextList: React.FC<{ items: string[] }> = ({ items }) => (
  <ul className="list-disc space-y-0.5 pl-5 text-neutral-700 dark:text-neutral-300">
    {items.map((item, idx) => (
      <li key={idx}>{item}</li>
    ))}
  </ul>
);

/** A report's findings by severity, open questions and next steps (its summary is shown apart). */
export const ReportDetails: React.FC<{ report: SubagentReport }> = ({ report }) => (
  <>
    {report.findings.length > 0 && (
      <Section title={`Findings (${report.findings.length})`}>
        <Findings findings={report.findings} />
      </Section>
    )}
    {report.open_questions.length > 0 && (
      <Section title="Open questions">
        <TextList items={report.open_questions} />
      </Section>
    )}
    {report.next_steps.length > 0 && (
      <Section title="Next steps">
        <TextList items={report.next_steps} />
      </Section>
    )}
  </>
);

/** What a delegated subagent was asked, the steps it took, and the report it ended with. */
export const SubagentExecutionCard: React.FC<{ subagent: SubagentExecutionData }> = ({ subagent }) => {
  const report = subagent.report ?? null;
  const summary = report?.summary || subagent.summary;
  const steps = subagent.steps ?? [];
  const branch = report?.changes?.branch ?? subagent.branch;
  return (
    <div className="space-y-3 text-sm">
      <Section title="Task">
        <p className="rounded-lg border border-surface-border bg-surface-base px-3 py-2 text-neutral-700 dark:text-neutral-300">
          {subagent.task}
        </p>
      </Section>

      {steps.length > 0 && (
        <Section title={`Steps (${steps.length})`}>
          <ul className="space-y-0.5" data-testid="subagent-steps">
            {steps.map((step) => (
              <StepRow key={step.id} step={step} />
            ))}
          </ul>
        </Section>
      )}

      {subagent.thought && subagent.status === "running" && (
        <Section title="Reasoning">
          <div className="border-l-2 border-brand/40 pl-3 text-neutral-600 dark:text-neutral-400">
            <MarkdownRenderer content={subagent.thought} />
          </div>
        </Section>
      )}

      {summary ? (
        <Section title="Report">
          <div className="rounded-lg border border-surface-border bg-surface-base px-3 py-2.5 leading-relaxed text-foreground">
            <MarkdownRenderer content={summary} />
          </div>
        </Section>
      ) : (
        subagent.status === "running" && (
          <p className="flex items-center gap-2 text-xs text-neutral-600 dark:text-neutral-400">
            <Loader2 className="h-3.5 w-3.5 animate-spin text-brand" aria-hidden />
            Working in its own context…
          </p>
        )
      )}

      {report && <ReportDetails report={report} />}

      {branch && (
        <Section title="Changes">
          <p className="flex items-center gap-1.5 font-mono text-[11px] text-neutral-700 dark:text-neutral-300">
            <GitBranch className="h-3.5 w-3.5" aria-hidden />
            {branch} (not merged until the agent merges it)
          </p>
          {report?.changes?.diff_stat && (
            <pre className="max-h-40 overflow-auto rounded border border-surface-border bg-surface-base p-2 text-[11px] text-neutral-700 dark:text-neutral-300">
              {report.changes.diff_stat}
            </pre>
          )}
        </Section>
      )}

      {!branch && subagent.changed_files && subagent.changed_files.length > 0 && (
        <Section title="Modified files">
          <div className="flex flex-wrap gap-1.5 font-mono text-[11px]">
            {subagent.changed_files.map((file, idx) => (
              <span key={idx} className="rounded-md border border-surface-border bg-surface-elevated px-2 py-0.5 text-neutral-700 dark:text-neutral-300">
                {file}
              </span>
            ))}
          </div>
        </Section>
      )}

      <div className="flex flex-wrap items-center gap-3 pt-1 font-mono text-[11px] text-neutral-600 dark:text-neutral-400">
        {subagent.model && <span>{subagent.model}</span>}
        {typeof subagent.duration_ms === "number" && <span>{formatElapsed(subagent.duration_ms)}</span>}
        {typeof subagent.tokens === "number" && subagent.tokens > 0 && <span>{subagent.tokens} tokens</span>}
        {typeof subagent.cost === "number" && subagent.cost > 0 && <span>${subagent.cost.toFixed(4)}</span>}
        {subagent.background && <span>background</span>}
        <span data-testid="subagent-status">{SUBAGENT_STATUS_LABEL[subagent.status] ?? subagent.status}</span>
      </div>
    </div>
  );
};

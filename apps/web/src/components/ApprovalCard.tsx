"use client";

import React, { useEffect } from "react";
import { ShieldAlert, ShieldCheck } from "lucide-react";

import { ApprovalRequestInfo } from "../types/mission";

interface ApprovalCardProps {
  approval: ApprovalRequestInfo;
  onApprove: (actionId: string) => void;
  onReject: (actionId: string) => void;
}

const RISK_STYLES: Record<string, string> = {
  critical: "border-rose-500/60 bg-rose-500/10",
  high: "border-amber-500/60 bg-amber-500/10",
  medium: "border-amber-500/50 bg-amber-500/5",
  low: "border-cyan/40 bg-cyan/5",
};

const RISK_LABEL: Record<string, string> = {
  critical: "Irreversible",
  high: "High risk",
  medium: "Medium risk",
  low: "Low risk",
};

/** Whether a key press is meant for the page, not for a field the person is typing in. */
function isForThePage(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return true;
  return !target.closest("input, textarea, select, [contenteditable='true']");
}

export const ApprovalCard: React.FC<ApprovalCardProps> = ({ approval, onApprove, onReject }) => {
  const riskClass = RISK_STYLES[approval.risk_level] || RISK_STYLES.medium;

  // Enter approves and Escape denies, but only when nothing is being typed: the composer keeps
  // its own Enter and Escape.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.defaultPrevented || event.metaKey || event.ctrlKey || event.altKey || !isForThePage(event.target)) return;
      if (event.key === "Enter" && !(event.target instanceof HTMLButtonElement || event.target instanceof HTMLAnchorElement)) {
        event.preventDefault();
        onApprove(approval.action_id);
      } else if (event.key === "Escape") {
        event.preventDefault();
        onReject(approval.action_id);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [approval.action_id, onApprove, onReject]);

  return (
    <div
      data-testid="approval-card"
      className={`rounded-xl border ${riskClass} px-4 py-3 shadow-sm`}
      role="group"
      aria-label={`Approval required: ${approval.action_type}`}
    >
      <div className="flex flex-wrap items-center gap-2">
        <ShieldAlert className="h-4 w-4 shrink-0 text-amber-600 dark:text-amber-400" aria-hidden="true" />
        <span className="text-sm font-semibold text-foreground">Approve {approval.action_type}?</span>
        <span className="ml-auto rounded-full border border-current px-2 py-0.5 text-[10px] font-medium text-amber-700 dark:text-amber-300">
          {RISK_LABEL[approval.risk_level] ?? approval.risk_level}
        </span>
      </div>

      {approval.description && (
        <p className="mt-2 max-h-40 overflow-auto whitespace-pre-wrap rounded-lg bg-surface-base/70 p-2 font-mono text-xs text-foreground">
          {approval.description}
        </p>
      )}

      <div className="mt-3 flex flex-wrap items-center gap-2">
        <button
          type="button"
          data-testid="approval-approve"
          onClick={() => onApprove(approval.action_id)}
          className="flex cursor-pointer items-center gap-1.5 rounded-lg bg-emerald-600 px-3.5 py-1.5 text-xs font-semibold text-white transition-colors hover:bg-emerald-500"
        >
          <ShieldCheck className="h-3.5 w-3.5" aria-hidden="true" />
          <span>Approve</span>
          <kbd className="rounded bg-white/20 px-1 text-[10px] font-mono">Enter</kbd>
        </button>
        <button
          type="button"
          data-testid="approval-reject"
          onClick={() => onReject(approval.action_id)}
          className="flex cursor-pointer items-center gap-1.5 rounded-lg border border-border bg-surface-elevated px-3.5 py-1.5 text-xs font-semibold text-foreground transition-colors hover:bg-surface-border"
        >
          <span>Deny</span>
          <kbd className="rounded border border-border px-1 text-[10px] font-mono text-muted-foreground">Esc</kbd>
        </button>
      </div>
    </div>
  );
};

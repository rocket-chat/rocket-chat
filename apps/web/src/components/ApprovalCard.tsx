"use client";

import React, { useState } from "react";
import { ShieldAlert, ShieldCheck } from "lucide-react";

import type { ApprovalRememberScope, ApprovalRequestInfo } from "../types/mission";
import { InlineDiff } from "./InlineDiff";
import { PLAN_APPROVAL_ACTION, PlanCard } from "./PlanCard";
import { Field, Input } from "./ui/Field";

interface ApprovalCardProps {
  approval: ApprovalRequestInfo;
  onApprove: (actionId: string, remember?: ApprovalRememberScope) => void;
  onReject: (actionId: string) => void;
}

const REMEMBER_LABEL: Record<ApprovalRememberScope, string> = {
  session: "Allow for this session",
  repository: "Always for this command in this repository",
};

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

const CONFIRM_WORD = "approve";

export const ApprovalCard: React.FC<ApprovalCardProps> = (props) =>
  props.approval.action_type === PLAN_APPROVAL_ACTION ? <PlanCard approval={props.approval} /> : <GateCard {...props} />;

const GateCard: React.FC<ApprovalCardProps> = ({ approval, onApprove, onReject }) => {
  const riskClass = RISK_STYLES[approval.risk_level] || RISK_STYLES.medium;
  const critical = approval.risk_level === "critical";
  const [confirmation, setConfirmation] = useState("");
  const confirmed = !critical || confirmation.trim().toLowerCase() === CONFIRM_WORD;

  // Enter approves and Escape denies only while focus is inside the card: a key pressed
  // anywhere else on the page (the composer, the sidebar, a dialog) must never answer a gate.
  // An irreversible action is never approved by a key alone: the word has to be typed.
  const onKeyDown = (event: React.KeyboardEvent<HTMLDivElement>) => {
    if (event.defaultPrevented || event.metaKey || event.ctrlKey || event.altKey) return;
    if (event.key === "Escape") {
      event.preventDefault();
      onReject(approval.action_id);
      return;
    }
    const onControl = event.target instanceof HTMLButtonElement || event.target instanceof HTMLAnchorElement;
    if (event.key === "Enter" && !onControl && confirmed) {
      event.preventDefault();
      onApprove(approval.action_id);
    }
  };

  return (
    <div
      data-testid="approval-card"
      className={`rounded-xl border ${riskClass} px-4 py-3 shadow-sm`}
      role="group"
      aria-label={`Approval required: ${approval.action_type}`}
      tabIndex={-1}
      onKeyDown={onKeyDown}
    >
      <div className="flex flex-wrap items-center gap-2">
        <ShieldAlert className="h-4 w-4 shrink-0 text-amber-600 dark:text-amber-400" aria-hidden="true" />
        <span className="text-sm font-semibold text-foreground">
          {approval.subagent_role ? `The ${approval.subagent_role.replace(/_/g, " ")} subagent asks: ` : ""}
          Approve {approval.action_type}?
        </span>
        <span className="ml-auto rounded-full border border-current px-2 py-0.5 text-[10px] font-medium text-amber-700 dark:text-amber-300">
          {RISK_LABEL[approval.risk_level] ?? approval.risk_level}
        </span>
      </div>

      {approval.description && (
        <p className="mt-2 max-h-40 overflow-auto whitespace-pre-wrap rounded-lg bg-surface-base/70 p-2 font-mono text-xs text-foreground">
          {approval.description}
        </p>
      )}

      {approval.diff ? (
        <div className="mt-2" data-testid="approval-diff">
          <InlineDiff diff={approval.diff} />
        </div>
      ) : (
        approval.arguments &&
        Object.keys(approval.arguments).length > 0 && (
          <details className="mt-2 text-xs" data-testid="approval-arguments">
            <summary className="cursor-pointer text-neutral-700 dark:text-neutral-300">
              Arguments{approval.tool ? ` of ${approval.tool}` : ""}
            </summary>
            <pre className="mt-1 max-h-48 overflow-auto whitespace-pre-wrap rounded-lg bg-surface-base/70 p-2 font-mono text-foreground">
              {JSON.stringify(approval.arguments, null, 2)}
            </pre>
          </details>
        )
      )}

      {critical && (
        <Field label={`This cannot be undone. Type "${CONFIRM_WORD}" to allow it`} className="mt-3">
          {(control) => (
            <Input
              {...control}
              data-testid="approval-confirm"
              value={confirmation}
              onChange={(event) => setConfirmation(event.target.value)}
              autoComplete="off"
              spellCheck={false}
              className="w-40 font-mono"
            />
          )}
        </Field>
      )}

      <div className="mt-3 flex flex-wrap items-center gap-2">
        <button
          type="button"
          data-testid="approval-approve"
          disabled={!confirmed}
          onClick={() => onApprove(approval.action_id)}
          className="flex cursor-pointer items-center gap-1.5 rounded-lg bg-emerald-600 px-3.5 py-1.5 text-xs font-semibold text-white transition-colors hover:bg-emerald-500 disabled:cursor-not-allowed disabled:opacity-50"
        >
          <ShieldCheck className="h-3.5 w-3.5" aria-hidden="true" />
          <span>Approve</span>
          {!critical && <kbd className="rounded bg-white/20 px-1 text-[10px] font-mono">Enter</kbd>}
        </button>
        {!critical &&
          (approval.remember_options ?? []).map((scope) => (
            <button
              key={scope}
              type="button"
              data-testid={`approval-remember-${scope}`}
              onClick={() => onApprove(approval.action_id, scope)}
              title={scope === "repository" && approval.repository ? `In ${approval.repository}` : undefined}
              className="cursor-pointer rounded-lg border border-emerald-600/60 px-3 py-1.5 text-xs font-semibold text-emerald-800 transition-colors hover:bg-emerald-500/10 dark:text-emerald-300"
            >
              {REMEMBER_LABEL[scope]}
            </button>
          ))}
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

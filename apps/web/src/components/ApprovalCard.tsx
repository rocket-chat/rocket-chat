import React from "react";
import { ShieldAlert, ShieldCheck } from "lucide-react";

import { ApprovalRequestInfo } from "../types/mission";

interface ApprovalCardProps {
  approval: ApprovalRequestInfo;
  onApprove: (actionId: string) => void;
  onReject: (actionId: string) => void;
}

const RISK_STYLES: Record<string, string> = {
  critical: "border-rose-500/50 bg-rose-500/5 text-rose-400",
  high: "border-amber-500/50 bg-amber-500/5 text-amber-400",
  medium: "border-amber-500/40 bg-amber-500/5 text-amber-400",
  low: "border-cyan/40 bg-cyan/5 text-cyan",
};

export const ApprovalCard: React.FC<ApprovalCardProps> = ({ approval, onApprove, onReject }) => {
  const riskClass = RISK_STYLES[approval.risk_level] || RISK_STYLES.medium;

  return (
    <div
      data-testid="approval-card"
      className={`my-3 rounded-lg border ${riskClass} px-4 py-3 shadow-sm`}
      role="group"
      aria-label={`Approval required: ${approval.action_type}`}
    >
      <div className="flex items-center gap-2">
        <ShieldAlert className="w-4 h-4 shrink-0" />
        <span className="text-xs font-mono font-semibold uppercase tracking-wider">
          Approval Required — {approval.action_type}
        </span>
        <span className="ml-auto px-1.5 py-0.5 rounded border border-current text-[9px] font-mono uppercase opacity-80">
          {approval.risk_level} risk
        </span>
      </div>

      {approval.description && (
        <p className="mt-2 text-xs font-mono text-foreground/80 whitespace-pre-wrap">
          {approval.description}
        </p>
      )}

      <div className="mt-3 flex items-center gap-2">
        <button
          type="button"
          data-testid="approval-approve"
          onClick={() => onApprove(approval.action_id)}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded border border-emerald-500/40 bg-emerald-500/10 hover:bg-emerald-500/20 text-emerald-400 text-[11px] font-mono font-semibold transition-colors cursor-pointer"
        >
          <ShieldCheck className="w-3.5 h-3.5" />
          <span>APPROVE</span>
        </button>
        <button
          type="button"
          data-testid="approval-reject"
          onClick={() => onReject(approval.action_id)}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded border border-border bg-surface-elevated hover:bg-surface-border text-neutral-300 text-[11px] font-mono font-semibold transition-colors cursor-pointer"
        >
          <span>REJECT</span>
        </button>
      </div>
    </div>
  );
};

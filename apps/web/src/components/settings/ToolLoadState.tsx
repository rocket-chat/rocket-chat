import React from "react";
import { Layers, Zap } from "lucide-react";
import { Badge } from "../ui/Badge";
import type { ToolLoadingPreview } from "../../lib/useToolLoadingPreview";

const MODE_LABEL: Record<ToolLoadingPreview["mode"], string> = {
  all: "Every tool loaded",
  on_demand: "On demand",
  auto: "Automatic",
};

/** One tool's state: sent with every step, or listed by name until the agent loads it. */
export const ToolLoadBadge: React.FC<{ deferred: boolean }> = ({ deferred }) => (
  <Badge
    tone={deferred ? "warning" : "success"}
    data-testid={deferred ? "tool-state-deferred" : "tool-state-active"}
    title={
      deferred
        ? "Deferred: listed by name; the agent loads it when it needs it"
        : "Active: its definition is sent with every step"
    }
  >
    {deferred ? <Layers className="h-2.5 w-2.5" aria-hidden="true" /> : <Zap className="h-2.5 w-2.5" aria-hidden="true" />}
    {deferred ? "Deferred" : "Active"}
  </Badge>
);

/** What the agent's tool loading amounts to right now, and why. */
export const ToolLoadSummary: React.FC<{ preview: ToolLoadingPreview | null; allowed: number; failed: boolean }> = ({
  preview,
  allowed,
  failed,
}) => {
  if (!preview) {
    return failed ? (
      <p className="text-xs text-neutral-500 dark:text-neutral-400">Could not work out which tools are loaded.</p>
    ) : null;
  }
  const deferred = preview.deferred.length;
  const active = Math.max(0, allowed - deferred);
  const share = allowed ? Math.round((active / allowed) * 100) : 100;
  return (
    <div data-testid="tool-load-summary" className="space-y-2 rounded-lg border border-surface-border bg-surface-card p-3">
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <strong className="text-foreground">{MODE_LABEL[preview.mode]}</strong>
        <Badge tone="neutral">{preview.source === "agent" ? "Set on this agent" : "Organization default"}</Badge>
        <span className="text-neutral-600 dark:text-neutral-400">
          <span data-testid="tool-count-active">{active}</span> active ·{" "}
          <span data-testid="tool-count-deferred">{deferred}</span> deferred
        </span>
      </div>
      <div
        role="img"
        aria-label={`${active} of ${allowed} allowed tools are active`}
        className="flex h-1.5 overflow-hidden rounded-full bg-amber-500/30"
      >
        <div className="bg-emerald-500" style={{ width: `${share}%` }} />
      </div>
      <p className="text-xs leading-relaxed text-neutral-600 dark:text-neutral-400">{preview.reason}</p>
    </div>
  );
};

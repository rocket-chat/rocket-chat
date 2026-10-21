import React from "react";
import { AlertTriangle, Layers, Zap } from "lucide-react";
import { Badge } from "../ui/Badge";
import type { ToolLoadingPreview } from "../../lib/useToolLoadingPreview";

/** One tool's state: core (always sent), sent with every step, or listed until the agent loads it. */
export const ToolLoadBadge: React.FC<{ deferred: boolean; core?: boolean }> = ({ deferred, core = false }) => {
  if (core) {
    return (
      <Badge tone="info" data-testid="tool-state-core" title="Core: sent with every step of every agent">
        <Zap className="h-2.5 w-2.5" aria-hidden="true" />
        Core
      </Badge>
    );
  }
  return (
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
};

/** What the agent's tool loading amounts to right now, and why. */
export const ToolLoadSummary: React.FC<{ preview: ToolLoadingPreview | null; failed: boolean }> = ({
  preview,
  failed,
}) => {
  if (!preview) {
    return failed ? (
      <p className="text-xs text-neutral-500 dark:text-neutral-400">Could not work out which tools are sent.</p>
    ) : null;
  }
  const deferred = preview.deferred.length;
  const share = preview.total ? Math.round((preview.sent / preview.total) * 100) : 100;
  const tooMany = preview.sent > preview.warning_at;
  return (
    <div data-testid="tool-load-summary" className="space-y-2 rounded-lg border border-surface-border bg-surface-card p-3">
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <strong className="text-foreground">Core tools and the ones kept active</strong>
        <span className="text-neutral-600 dark:text-neutral-400">
          <span data-testid="tool-count-active">{preview.sent}</span> sent with every step ·{" "}
          <span data-testid="tool-count-deferred">{deferred}</span> loaded when needed
        </span>
      </div>
      <div
        role="img"
        aria-label={`${preview.sent} of ${preview.total} allowed tools are sent with every step`}
        className="flex h-1.5 overflow-hidden rounded-full bg-amber-500/30"
      >
        <div className={tooMany ? "bg-amber-500" : "bg-emerald-500"} style={{ width: `${share}%` }} />
      </div>
      {tooMany && (
        <p
          data-testid="tool-load-warning"
          role="status"
          className="flex items-start gap-1.5 text-xs text-amber-700 dark:text-amber-300"
        >
          <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
          More than {preview.warning_at} tools are sent with every step. Each one is paid for at every step, and models
          choose worse among many tools: keep active only what this agent uses on most tasks.
        </p>
      )}
      <p className="text-xs leading-relaxed text-neutral-600 dark:text-neutral-400">{preview.reason}</p>
    </div>
  );
};

"use client";

import React, { useEffect, useRef, useState } from "react";
import { Coins, Sparkles } from "lucide-react";
import { useMissionStore } from "../lib/store";
import { useClampIntoViewport } from "../lib/useClampIntoViewport";
import { useSessionUsage } from "../lib/useSessionUsage";
import {
  cacheHitPercent,
  describeTurnMetrics,
  formatCount,
  formatDuration,
  formatUsd,
  type UsageTotals,
} from "../lib/session-usage";
import { Badge } from "./ui/Badge";

const Figures: React.FC<{ totals: UsageTotals }> = ({ totals }) => (
  <dl className="grid grid-cols-4 gap-2 text-[11px]">
    <div>
      <dt className="text-neutral-500">In</dt>
      <dd className="font-mono text-foreground">{formatCount(totals.prompt_tokens)}</dd>
    </div>
    <div>
      <dt className="text-neutral-500">Cached</dt>
      <dd className="font-mono text-foreground">{cacheHitPercent(totals)}%</dd>
    </div>
    <div>
      <dt className="text-neutral-500">Out</dt>
      <dd className="font-mono text-foreground">{formatCount(totals.completion_tokens)}</dd>
    </div>
    <div>
      <dt className="text-neutral-500">Calls</dt>
      <dd className="font-mono text-foreground">{totals.calls}</dd>
    </div>
  </dl>
);

type UsageTab = "overview" | "context" | "costs";

/**
 * The session's tokens and cost in the header, and what they are made of one click away: each turn
 * (the running one included, since the ledger is written call by call) and each model.
 */
export const SessionUsageChip: React.FC<{ sessionId: string | undefined; running: boolean }> = ({
  sessionId,
  running,
}) => {
  const usage = useSessionUsage(sessionId, running);
  const liveContextTokens = useMissionStore((state) => state.contextTokens);
  const liveContextLimit = useMissionStore((state) => state.contextLimit);
  const liveContextModelLimit = useMissionStore((state) => state.contextModelLimit);
  const liveContextTargetTokens = useMissionStore((state) => state.contextTargetTokens);
  const liveContextSource = useMissionStore((state) => state.contextSource);
  const liveContextTier2Tokens = useMissionStore((state) => state.contextTier2Tokens);
  const liveContextTier3Tokens = useMissionStore((state) => state.contextTier3Tokens);

  const contextTokens = liveContextTokens ?? usage?.active_context?.tokens ?? null;
  const contextLimit = liveContextLimit ?? usage?.active_context?.limit ?? null;
  const contextModelLimit = liveContextModelLimit ?? usage?.active_context?.model_limit ?? null;
  const contextTargetTokens = liveContextTargetTokens ?? usage?.active_context?.target_tokens ?? null;
  const contextSource = liveContextSource ?? usage?.active_context?.source ?? null;
  const contextTier2Tokens = liveContextTier2Tokens ?? usage?.active_context?.tier2_tokens ?? null;
  const contextTier3Tokens = liveContextTier3Tokens ?? usage?.active_context?.tier3_tokens ?? null;

  const [activeTab, setActiveTab] = useState<UsageTab>("overview");
  const [compacting, setCompacting] = useState(false);
  const [compactMessage, setCompactMessage] = useState<string | null>(null);
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const popoverRef = useRef<HTMLDivElement>(null);
  useClampIntoViewport(popoverRef, open);

  useEffect(() => {
    if (!open) return;
    const away = (event: MouseEvent) => {
      if (ref.current && !ref.current.contains(event.target as Node)) setOpen(false);
    };
    const escape = (event: KeyboardEvent) => event.key === "Escape" && setOpen(false);
    document.addEventListener("mousedown", away);
    document.addEventListener("keydown", escape);
    return () => {
      document.removeEventListener("mousedown", away);
      document.removeEventListener("keydown", escape);
    };
  }, [open]);

  if (!usage || usage.totals.calls === 0) return null;
  const { totals } = usage;
  const contextFillPct =
    contextTokens != null && contextLimit != null && contextLimit > 0
      ? Math.min(100, Math.round((contextTokens / contextLimit) * 100))
      : null;
  const isCappedByTarget =
    contextTargetTokens != null &&
    contextTargetTokens > 0 &&
    contextModelLimit != null &&
    contextTargetTokens < contextModelLimit;
  const isOverride = contextSource === "override";

  return (
    <div className="relative" ref={ref}>
      <button
        type="button"
        data-testid="session-usage-chip"
        aria-expanded={open}
        aria-haspopup="dialog"
        onClick={() => setOpen((value) => !value)}
        title="Tokens and cost of this session"
        className="inline-flex h-8 cursor-pointer items-center gap-1.5 rounded-lg border border-surface-border bg-surface-card px-2.5 text-[11px] text-neutral-600 transition-colors hover:bg-surface-elevated dark:text-neutral-300"
      >
        <Coins className="h-3.5 w-3.5 text-brand" aria-hidden="true" />
        <span data-testid="session-usage-cost" className="font-mono font-medium text-foreground">
          {formatUsd(totals.cost_usd)}
        </span>
        <span data-testid="session-usage-tokens" className="hidden font-mono sm:inline">
          · {formatCount(totals.total_tokens)} tok
        </span>
        {contextFillPct != null && (
          <span
            data-testid="session-usage-ctx-fill"
            className={`hidden font-mono md:inline ${
              contextFillPct >= 85
                ? "text-rose-500 font-semibold"
                : contextFillPct >= 70
                  ? "text-amber-500 font-medium"
                  : "text-neutral-500"
            }`}
          >
            · {contextFillPct}% ctx
          </span>
        )}
      </button>

      {open && (
        <div
          ref={popoverRef}
          role="dialog"
          aria-label="Session usage"
          data-testid="session-usage-popover"
          className="absolute right-0 top-full z-50 mt-2 w-[min(28rem,calc(100vw-1.5rem))] space-y-3 rounded-xl border border-surface-border bg-surface-elevated p-3.5 text-xs shadow-xl"
        >
          <div className="flex items-baseline justify-between gap-3">
            <span className="font-display text-sm font-semibold text-foreground">This session</span>
            <span className="font-mono text-base font-semibold text-foreground" data-testid="session-usage-total">
              {formatUsd(totals.cost_usd)}
            </span>
          </div>

          {/* Tab Selector */}
          <div className="flex items-center gap-1 border-b border-surface-border pb-1.5 text-xs">
            <button
              type="button"
              data-testid="session-usage-tab-overview"
              onClick={() => setActiveTab("overview")}
              className={`rounded-md px-2.5 py-1 font-medium transition-colors cursor-pointer ${
                activeTab === "overview"
                  ? "bg-surface-card text-foreground shadow-sm border border-surface-border"
                  : "text-neutral-500 hover:text-foreground"
              }`}
            >
              Overview
            </button>
            <button
              type="button"
              data-testid="session-usage-tab-context"
              onClick={() => setActiveTab("context")}
              className={`rounded-md px-2.5 py-1 font-medium transition-colors cursor-pointer ${
                activeTab === "context"
                  ? "bg-surface-card text-foreground shadow-sm border border-surface-border"
                  : "text-neutral-500 hover:text-foreground"
              }`}
            >
              Context
              {contextFillPct != null && (
                <span className="ml-1 text-[10px] font-mono opacity-80">
                  ({contextFillPct}%)
                </span>
              )}
            </button>
            <button
              type="button"
              data-testid="session-usage-tab-costs"
              onClick={() => setActiveTab("costs")}
              className={`rounded-md px-2.5 py-1 font-medium transition-colors cursor-pointer ${
                activeTab === "costs"
                  ? "bg-surface-card text-foreground shadow-sm border border-surface-border"
                  : "text-neutral-500 hover:text-foreground"
              }`}
            >
              Costs & Turns
            </button>
          </div>

          {/* TAB 1: OVERVIEW */}
          <div className={activeTab === "overview" ? "space-y-3" : "hidden"}>
            {contextTokens != null && contextLimit != null && (
              <div className="rounded-lg border border-surface-border bg-surface-card p-2.5 space-y-2">
                <div className="flex items-center justify-between text-[11px]">
                  <span className="font-semibold text-foreground">Active Context Window</span>
                  <span className="font-mono text-neutral-600 dark:text-neutral-300">
                    {formatCount(contextTokens)} / {formatCount(contextLimit)} ({contextFillPct}%)
                  </span>
                </div>
                <div className="relative h-1.5 w-full overflow-hidden rounded-full bg-neutral-200 dark:bg-neutral-800">
                  <div
                    className={`h-full transition-all duration-300 ${
                      (contextFillPct ?? 0) >= 85
                        ? "bg-rose-500"
                        : (contextFillPct ?? 0) >= 70
                          ? "bg-amber-500"
                          : "bg-emerald-500"
                    }`}
                    style={{ width: `${contextFillPct ?? 0}%` }}
                  />
                  <div
                    className="absolute top-0 bottom-0 w-0.5 bg-neutral-400/40 dark:bg-neutral-500/40"
                    style={{ left: "70%" }}
                  />
                  <div
                    className="absolute top-0 bottom-0 w-0.5 bg-neutral-400/60 dark:bg-neutral-500/60"
                    style={{ left: "85%" }}
                  />
                </div>
                <div className="flex items-center justify-between text-[10px]">
                  <span className="text-neutral-500">
                    Pruning at 70% · Compaction at 85%
                  </span>
                  <button
                    type="button"
                    onClick={() => setActiveTab("context")}
                    className="text-brand hover:underline cursor-pointer"
                  >
                    Details &rarr;
                  </button>
                </div>
              </div>
            )}

            <Figures totals={totals} />

            {totals.own_key_cost_usd > 0 && (
              <p className="text-[11px] text-neutral-500">
                {formatUsd(totals.own_key_cost_usd)} of it was paid with your own provider key.
              </p>
            )}

            {usage.models.length > 0 && (
              <div>
                <h3 className="mb-1 text-[11px] font-semibold text-neutral-600 dark:text-neutral-300">By model</h3>
                <ul className="space-y-0.5 text-[11px]">
                  {usage.models.map((model) => (
                    <li key={model.model} className="flex items-center justify-between gap-3">
                      <span className="min-w-0 truncate font-mono text-neutral-600 dark:text-neutral-300" title={model.model}>
                        {model.model.split("/").slice(-2).join("/")}
                      </span>
                      <span className="shrink-0 font-mono text-foreground">
                        {formatUsd(model.cost_usd)} · {model.calls} calls
                      </span>
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </div>

          {/* TAB 2: CONTEXT */}
          <div className={activeTab === "context" ? "space-y-3" : "hidden"}>
            {contextTokens != null && contextLimit != null ? (
              <div className="rounded-lg border border-surface-border bg-surface-card p-3 space-y-2.5">
                <div className="flex items-center justify-between text-[11px]">
                  <div className="flex items-center gap-1.5">
                    <span className="font-semibold text-foreground">Active Context Window</span>
                    {isCappedByTarget ? (
                      <span className="rounded bg-amber-500/10 px-1.5 py-0.5 text-[10px] font-medium text-amber-500 border border-amber-500/20">
                        Capped Target
                      </span>
                    ) : isOverride ? (
                      <span className="rounded bg-sky-500/10 px-1.5 py-0.5 text-[10px] font-medium text-sky-400 border border-sky-500/20">
                        Override
                      </span>
                    ) : null}
                  </div>
                  <span className="font-mono text-neutral-600 dark:text-neutral-300">
                    {formatCount(contextTokens)} / {formatCount(contextLimit)} ({contextFillPct}%)
                  </span>
                </div>

                <div className="relative h-2 w-full overflow-hidden rounded-full bg-neutral-200 dark:bg-neutral-800">
                  <div
                    className={`h-full transition-all duration-300 ${
                      (contextFillPct ?? 0) >= 85
                        ? "bg-rose-500"
                        : (contextFillPct ?? 0) >= 70
                          ? "bg-amber-500"
                          : "bg-emerald-500"
                    }`}
                    style={{ width: `${contextFillPct ?? 0}%` }}
                  />
                  <div
                    className="absolute top-0 bottom-0 w-0.5 bg-neutral-400/40 dark:bg-neutral-500/40"
                    style={{ left: "70%" }}
                    title="Pruning threshold (70%)"
                  />
                  <div
                    className="absolute top-0 bottom-0 w-0.5 bg-neutral-400/60 dark:bg-neutral-500/60"
                    style={{ left: "85%" }}
                    title="Compaction threshold (85%)"
                  />
                </div>

                <div className="grid grid-cols-2 gap-2 text-[10px]">
                  <div className="rounded border border-surface-border/60 bg-surface-elevated/40 p-2">
                    <div className="text-neutral-500">Tier 2 Pruning (70%)</div>
                    <div className="font-mono font-medium text-foreground">
                      ~{formatCount(contextTier2Tokens ?? Math.round(contextLimit * 0.7))} tok
                    </div>
                    <div className="text-[9px] text-neutral-400 mt-0.5">
                      Verbose tool outputs pruned to receipts
                    </div>
                  </div>
                  <div className="rounded border border-surface-border/60 bg-surface-elevated/40 p-2">
                    <div className="text-neutral-500">Tier 3 Compaction (85%)</div>
                    <div className="font-mono font-medium text-foreground">
                      ~{formatCount(contextTier3Tokens ?? Math.round(contextLimit * 0.85))} tok
                    </div>
                    <div className="text-[9px] text-neutral-400 mt-0.5">
                      History summarized & memory anchored
                    </div>
                  </div>
                </div>

                <div className="rounded border border-surface-border/60 bg-surface-elevated/30 p-2 text-[10px] text-neutral-500 space-y-1">
                  <div className="flex items-center justify-between text-neutral-400">
                    <span>Model Raw Window:</span>
                    <span className="font-mono text-foreground">
                      {formatCount(contextModelLimit ?? contextLimit)} tok
                      {contextSource ? ` (${contextSource})` : ""}
                    </span>
                  </div>
                  {isCappedByTarget && (
                    <p className="leading-snug text-neutral-400">
                      Thresholds operate against the <strong className="text-foreground">{formatCount(contextLimit)} tok</strong> working target (<code className="text-neutral-300">models.context_target_tokens</code>) rather than the model capacity to keep turns fast and prevent degradation.
                    </p>
                  )}
                  {isOverride && (
                    <p className="leading-snug text-neutral-400">
                      Thresholds use the organization window override (<code className="text-neutral-300">models.context_windows</code>).
                    </p>
                  )}
                </div>

                <div className="flex items-center justify-between pt-0.5">
                  <span className="text-[10px] text-neutral-500">Pruning at 70% · Compaction at 85%</span>
                  {!running && sessionId && (
                    <button
                      type="button"
                      disabled={compacting}
                      onClick={async () => {
                        setCompacting(true);
                        setCompactMessage(null);
                        try {
                          const res = await fetch(`/v1/sessions/${sessionId}/compact`, { method: "POST" });
                          if (res.ok) {
                            const data = await res.json();
                            setCompactMessage(data.status === "compacted" ? "Compacted!" : "Skipped");
                          }
                        } catch {
                          setCompactMessage("Failed");
                        } finally {
                          setCompacting(false);
                        }
                      }}
                      className="inline-flex items-center gap-1 rounded bg-brand/10 px-2 py-0.5 text-[10px] font-medium text-brand hover:bg-brand/20 transition-colors disabled:opacity-50 cursor-pointer"
                    >
                      <Sparkles className="h-3 w-3" />
                      {compacting ? "Compacting..." : compactMessage || "Compact now"}
                    </button>
                  )}
                </div>
              </div>
            ) : (
              <p className="text-neutral-500 text-[11px] p-2">Context metrics will appear once session data is available.</p>
            )}
          </div>

          {/* TAB 3: COSTS & TURNS */}
          <div className={activeTab === "costs" ? "space-y-3" : "hidden"}>
            <Figures totals={totals} />

            {totals.own_key_cost_usd > 0 && (
              <p className="text-[11px] text-neutral-500">
                {formatUsd(totals.own_key_cost_usd)} of it was paid with your own provider key.
              </p>
            )}

            {usage.turns.length > 0 && (
              <div>
                <h3 className="mb-1 text-[11px] font-semibold text-neutral-600 dark:text-neutral-300">By turn</h3>
                <div className="max-h-56 overflow-y-auto rounded-lg border border-surface-border">
                  <table className="w-full text-left text-[11px]" data-testid="session-usage-turns">
                    <caption className="sr-only">Tokens and cost of each turn</caption>
                    <thead className="sticky top-0 bg-surface-card text-neutral-500">
                      <tr>
                        <th scope="col" className="px-2 py-1 font-medium">Turn</th>
                        <th scope="col" className="px-2 py-1 text-right font-medium">Calls</th>
                        <th scope="col" className="px-2 py-1 text-right font-medium">Time</th>
                        <th scope="col" className="px-2 py-1 text-right font-medium">In</th>
                        <th scope="col" className="px-2 py-1 text-right font-medium">Out</th>
                        <th scope="col" className="px-2 py-1 text-right font-medium">Cost</th>
                      </tr>
                    </thead>
                    <tbody>
                      {usage.turns.map((turn) => (
                        <tr key={turn.turn_index} className="border-t border-surface-border" data-testid="session-usage-turn">
                          <td className="px-2 py-1 text-foreground">
                            #{turn.turn_index}
                            {turn.status === "running" && (
                              <Badge tone="info" className="ml-1.5">running</Badge>
                            )}
                          </td>
                          <td className="px-2 py-1 text-right font-mono">{turn.calls}</td>
                          <td
                            className="px-2 py-1 text-right font-mono"
                            title={turn.metrics ? describeTurnMetrics(turn.metrics) : undefined}
                            data-testid="session-usage-turn-time"
                          >
                            {turn.ended_at ? formatDuration(turn.ended_at - turn.started_at) : "…"}
                          </td>
                          <td className="px-2 py-1 text-right font-mono">{formatCount(turn.prompt_tokens)}</td>
                          <td className="px-2 py-1 text-right font-mono">{formatCount(turn.completion_tokens)}</td>
                          <td className="px-2 py-1 text-right font-mono text-foreground">{formatUsd(turn.cost_usd)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            )}

            <p className="text-[10px] leading-relaxed text-neutral-500">
              Cost as billed by the provider; cached tokens cost less. A turn includes the calls made when it ends
              (title, summary, suggested prompt).
            </p>
          </div>
        </div>
      )}
    </div>
  );
};

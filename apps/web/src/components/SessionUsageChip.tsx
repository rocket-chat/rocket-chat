"use client";

import React, { useEffect, useRef, useState } from "react";
import { Coins } from "lucide-react";
import { useClampIntoViewport } from "../lib/useClampIntoViewport";
import { useSessionUsage } from "../lib/useSessionUsage";
import { cacheHitPercent, formatCount, formatUsd, type UsageTotals } from "../lib/session-usage";
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

/**
 * The session's tokens and cost in the header, and what they are made of one click away: each turn
 * (the running one included, since the ledger is written call by call) and each model.
 */
export const SessionUsageChip: React.FC<{ sessionId: string | undefined; running: boolean }> = ({
  sessionId,
  running,
}) => {
  const usage = useSessionUsage(sessionId, running);
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
      </button>

      {open && (
        <div
          ref={popoverRef}
          role="dialog"
          aria-label="Session usage"
          data-testid="session-usage-popover"
          className="absolute right-0 top-full z-50 mt-2 w-[min(26rem,calc(100vw-1.5rem))] space-y-3 rounded-xl border border-surface-border bg-surface-elevated p-3.5 text-xs shadow-xl"
        >
          <div className="flex items-baseline justify-between gap-3">
            <span className="font-display text-sm font-semibold text-foreground">This session</span>
            <span className="font-mono text-base font-semibold text-foreground" data-testid="session-usage-total">
              {formatUsd(totals.cost_usd)}
            </span>
          </div>
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

          <p className="text-[10px] leading-relaxed text-neutral-500">
            Cost as billed by the provider; cached tokens cost less. A turn includes the calls made when it ends
            (title, summary, suggested prompt).
          </p>
        </div>
      )}
    </div>
  );
};

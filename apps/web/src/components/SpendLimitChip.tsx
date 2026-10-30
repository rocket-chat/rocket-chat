"use client";

import React, { useEffect, useState } from "react";
import { Wallet } from "lucide-react";

interface LimitView {
  label: string;
  mode: string;
  remaining: number;
  amount: number;
  resets_at: number | null;
}

const PERIOD_WORDS: Record<string, string> = {
  day: "today",
  week: "this week",
  month: "this month",
  rolling_24h: "in 24 h",
  rolling_7d: "in 7 days",
  session: "in this session",
  turn: "this turn",
};

/** The tightest spend limit for this person in this session: "$3.10 left today". */
export const SpendLimitChip: React.FC<{ sessionId: string | undefined; running: boolean }> = ({ sessionId, running }) => {
  const [tightest, setTightest] = useState<(LimitView & { period: string }) | null>(null);

  useEffect(() => {
    if (!sessionId) {
      setTightest(null);
      return;
    }
    let cancelled = false;
    fetch(`/v1/sessions/${sessionId}/spend`)
      .then((res) => (res.ok ? res.json() : null))
      .then((body: { tightest?: (LimitView & { period: string }) | null } | null) => {
        if (!cancelled) setTightest(body?.tightest ?? null);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
    // Refreshed when a turn starts or ends: that is when the spend moves.
  }, [sessionId, running]);

  if (!tightest) return null;
  const low = tightest.amount > 0 && tightest.remaining / tightest.amount < 0.2;
  return (
    <div
      data-testid="spend-limit-chip"
      title={`${tightest.label}: $${tightest.remaining.toFixed(2)} of $${tightest.amount.toFixed(2)} left${
        tightest.resets_at ? `, resets ${new Date(tightest.resets_at * 1000).toLocaleString()}` : ""
      }`}
      className={`hidden items-center gap-1.5 rounded-md border px-2.5 py-1 text-[11px] md:flex ${
        low
          ? "border-amber-500/40 bg-amber-500/10 text-amber-700 dark:text-amber-300"
          : "border-surface-border bg-surface-card text-neutral-600 dark:text-neutral-400"
      }`}
    >
      <Wallet className="h-3 w-3" aria-hidden="true" />
      ${tightest.remaining.toFixed(2)} left {PERIOD_WORDS[tightest.period] ?? ""}
    </div>
  );
};

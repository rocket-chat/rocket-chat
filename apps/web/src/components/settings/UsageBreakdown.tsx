"use client";

import React, { useEffect, useState } from "react";
import { tabKeyHandler } from "../../lib/tab-keys";

interface Group {
  key: string;
  cost_usd: number;
  calls: number;
  prompt_tokens: number;
  completion_tokens: number;
}

interface ModelUsage {
  prompt_tokens: number;
  completion_tokens: number;
  cost_usd: number;
}

interface Usage {
  total_cost_usd: number;
  total_tokens: number;
  calls: number;
  own_key_cost_usd: number;
  models: Record<string, ModelUsage>;
  by_agent?: Group[];
  by_credential?: Group[];
  by_member?: Group[];
}

const VIEWS = [
  { key: "by_agent", label: "By agent" },
  { key: "by_credential", label: "By credential" },
  { key: "by_member", label: "By member" },
  { key: "models", label: "By model" },
] as const;

type View = (typeof VIEWS)[number]["key"];

/** This month's spend, in total and grouped by agent, credential, member or model. */
export const UsageBreakdown: React.FC<{ scope: "org" | "user" }> = ({ scope }) => {
  const [usage, setUsage] = useState<Usage | null>(null);
  const [view, setView] = useState<View>(scope === "org" ? "by_agent" : "models");
  const [names, setNames] = useState<Record<string, string>>({});

  useEffect(() => {
    fetch(scope === "org" ? "/v1/settings/org/usage" : "/v1/settings/user/usage")
      .then((res) => (res.ok ? res.json() : null))
      .then(setUsage)
      .catch(() => setUsage(null));
    fetch("/v1/agents")
      .then((res) => (res.ok ? res.json() : []))
      .then((agents: { id: string; name: string }[]) =>
        setNames(Object.fromEntries(agents.map((a) => [a.id, a.name]))),
      )
      .catch(() => undefined);
  }, [scope]);

  const rows: Group[] =
    view === "models"
      ? Object.entries(usage?.models ?? {}).map(([key, m]) => ({
          key,
          cost_usd: m.cost_usd,
          calls: 0,
          prompt_tokens: m.prompt_tokens,
          completion_tokens: m.completion_tokens,
        }))
      : (usage?.[view] ?? []);
  const views = scope === "org" ? VIEWS : VIEWS.filter((v) => v.key === "models");

  return (
    <section className="space-y-3" data-testid={`usage-breakdown-${scope}`}>
      <h2 className="font-display text-base font-semibold text-foreground">
        {scope === "org" ? "This month's spend" : "Your usage this month"}
      </h2>
      <div className="grid gap-3 sm:grid-cols-3">
        {[
          ["Spent", `$${(usage?.total_cost_usd ?? 0).toFixed(2)}`],
          ["Tokens", (usage?.total_tokens ?? 0).toLocaleString()],
          ["On members' own keys", `$${(usage?.own_key_cost_usd ?? 0).toFixed(2)}`],
        ].map(([label, value]) => (
          <div key={label} className="rounded-xl border border-surface-border bg-surface-card p-4">
            <p className="text-[11px] text-neutral-500">{label}</p>
            <p className="font-display text-lg font-semibold text-foreground">{value}</p>
          </div>
        ))}
      </div>
      {views.length > 1 && (
        <div role="tablist" aria-label="Group spend" className="flex gap-1">
          {views.map((v) => (
            <button
              key={v.key}
              type="button"
              role="tab"
              id={`usage-tab-${v.key}`}
              aria-selected={view === v.key}
              aria-controls="usage-tabpanel"
              tabIndex={view === v.key ? 0 : -1}
              onClick={() => setView(v.key)}
              onKeyDown={tabKeyHandler(views.map((x) => x.key), view, setView, (key) => `usage-tab-${key}`)}
              className={`rounded-lg px-2.5 py-1 text-xs ${view === v.key ? "bg-surface-elevated font-semibold text-foreground" : "text-neutral-500 hover:text-foreground"}`}
            >
              {v.label}
            </button>
          ))}
        </div>
      )}
      <div
        id="usage-tabpanel"
        role={views.length > 1 ? "tabpanel" : undefined}
        aria-labelledby={views.length > 1 ? `usage-tab-${view}` : undefined}
        className="overflow-x-auto rounded-xl border border-surface-border bg-surface-card"
      >
        <table className="w-full text-xs">
          <thead className="text-[10px] text-muted-foreground">
            <tr>
              <th className="px-4 py-2 text-left">{VIEWS.find((v) => v.key === view)?.label.replace("By ", "")}</th>
              <th className="px-4 py-2 text-right">Tokens</th>
              <th className="px-4 py-2 text-right">Cost</th>
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 && (
              <tr>
                <td colSpan={3} className="px-4 py-4 text-center text-neutral-500">
                  Nothing spent yet this month.
                </td>
              </tr>
            )}
            {rows.map((row) => (
              <tr key={row.key} className="border-t border-surface-border/60">
                <td className="px-4 py-2 text-foreground">
                  {row.key === "unattributed" ? "Unattributed (before attribution)" : view === "by_agent" ? (names[row.key] ?? row.key) : row.key}
                </td>
                <td className="px-4 py-2 text-right font-mono">{(row.prompt_tokens + row.completion_tokens).toLocaleString()}</td>
                <td className="px-4 py-2 text-right font-mono">${row.cost_usd.toFixed(4)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
};

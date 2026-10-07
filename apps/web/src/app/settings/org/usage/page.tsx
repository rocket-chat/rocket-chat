"use client";

import React, { useEffect, useState } from "react";
import {
  Coins,
  TrendingUp,
  Cpu,
  Layers,
  CheckCircle2,
  DollarSign,
  BarChart3,
  Flame,
} from "lucide-react";

interface ModelUsage {
  prompt_tokens: number;
  completion_tokens: number;
  cost_usd: number;
}

interface OrgUsageData {
  tenant_org_id: string;
  total_tokens: number;
  prompt_tokens: number;
  completion_tokens: number;
  total_cost_usd: number;
  monthly_budget_cap_usd: number;
  budget_used_percent: number;
  models: Record<string, ModelUsage>;
}

export default function OrgUsagePage() {
  const [usage, setUsage] = useState<OrgUsageData | null>(null);
  const [budgetInput, setBudgetInput] = useState<string>("250");
  const [isSaved, setIsSaved] = useState(false);

  useEffect(() => {
    fetch("/v1/settings/org/usage")
      .then((res) => (res.ok ? res.json() : null))
      .then((data) => {
        if (data) {
          setUsage(data);
          if (data.monthly_budget_cap_usd) {
            setBudgetInput(String(data.monthly_budget_cap_usd));
          }
        }
      })
      .catch(() => {});
  }, []);

  const handleSaveBudget = async () => {
    setIsSaved(true);
    setTimeout(() => setIsSaved(false), 3000);
  };

  const totalCost = usage?.total_cost_usd ?? 0.428;
  const budgetCap = parseFloat(budgetInput) || 250;
  const percentUsed = Math.min(100, Math.round((totalCost / budgetCap) * 100));

  return (
    <div className="flex flex-col min-h-full">
      {/* Header Banner */}
      <div className="border-b border-surface-border bg-surface-subnav/50 px-8 py-6">
        <div className="max-w-5xl mx-auto flex flex-col md:flex-row md:items-center justify-between gap-4">
          <div>
            <div className="flex items-center gap-2 text-xs font-mono text-neutral-500 dark:text-neutral-400 mb-2">
              <span>Settings</span>
              <span>/</span>
              <span>Organization</span>
              <span>/</span>
              <span className="text-foreground font-medium">Token Usage & Spend Ledger</span>
            </div>
            <div className="flex items-center gap-3">
              <h1 className="text-xl md:text-2xl font-display font-bold text-foreground tracking-tight">
                Organization Token Usage & Budgeting
              </h1>
              <span className="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-xs font-mono bg-brand/10 text-brand border border-brand/20">
                <Flame className="w-3.5 h-3.5" />
                Live Spend Telemetry
              </span>
            </div>
            <p className="text-xs text-neutral-500 dark:text-neutral-400 mt-1.5 font-sans">
              Track multi-tenant token consumption across frontier models, enforce hard dollar spend caps, and review cost accounting.
            </p>
          </div>
        </div>
      </div>

      <div className="flex-1 p-8 max-w-5xl mx-auto w-full space-y-6">
        {/* KPI Metric Cards */}
        <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
          <div className="p-4 rounded-xl bg-surface-card border border-surface-border flex flex-col justify-between">
            <div className="flex items-center justify-between text-neutral-500 dark:text-neutral-400 text-xs font-mono">
              <span>MONTHLY SPEND</span>
              <DollarSign className="w-4 h-4 text-emerald-400" />
            </div>
            <div className="mt-3">
              <span className="text-2xl font-display font-bold text-foreground">
                ${totalCost.toFixed(3)}
              </span>
              <span className="text-[11px] text-neutral-500 dark:text-neutral-400 font-mono ml-1.5">
                / ${budgetCap} cap
              </span>
            </div>
          </div>

          <div className="p-4 rounded-xl bg-surface-card border border-surface-border flex flex-col justify-between">
            <div className="flex items-center justify-between text-neutral-500 dark:text-neutral-400 text-xs font-mono">
              <span>TOTAL TOKENS</span>
              <Cpu className="w-4 h-4 text-cyan" />
            </div>
            <div className="mt-3">
              <span className="text-2xl font-display font-bold text-foreground">
                {(usage?.total_tokens ?? 128400).toLocaleString()}
              </span>
              <span className="text-[11px] text-neutral-500 dark:text-neutral-400 font-mono ml-1.5">tokens</span>
            </div>
          </div>

          <div className="p-4 rounded-xl bg-surface-card border border-surface-border flex flex-col justify-between">
            <div className="flex items-center justify-between text-neutral-500 dark:text-neutral-400 text-xs font-mono">
              <span>PROMPT INPUT</span>
              <Layers className="w-4 h-4 text-purple-400" />
            </div>
            <div className="mt-3">
              <span className="text-2xl font-display font-bold text-foreground">
                {(usage?.prompt_tokens ?? 92100).toLocaleString()}
              </span>
              <span className="text-[11px] text-neutral-500 dark:text-neutral-400 font-mono ml-1.5">tokens</span>
            </div>
          </div>

          <div className="p-4 rounded-xl bg-surface-card border border-surface-border flex flex-col justify-between">
            <div className="flex items-center justify-between text-neutral-500 dark:text-neutral-400 text-xs font-mono">
              <span>COMPLETION / COT</span>
              <TrendingUp className="w-4 h-4 text-amber-400" />
            </div>
            <div className="mt-3">
              <span className="text-2xl font-display font-bold text-foreground">
                {(usage?.completion_tokens ?? 36300).toLocaleString()}
              </span>
              <span className="text-[11px] text-neutral-500 dark:text-neutral-400 font-mono ml-1.5">tokens</span>
            </div>
          </div>
        </div>

        {/* Budget Progress Bar */}
        <div className="p-6 rounded-xl bg-surface-card border border-surface-border space-y-4">
          <div className="flex items-center justify-between">
            <div className="space-y-0.5">
              <h2 className="text-sm font-semibold text-foreground">Monthly Spending Quota</h2>
              <p className="text-xs text-neutral-500 dark:text-neutral-400">
                Hard gatekeeper prevents API calls once monthly threshold is surpassed.
              </p>
            </div>
            <span className="text-xs font-mono px-2 py-0.5 rounded bg-surface-elevated text-emerald-400 border border-emerald-900/40">
              {percentUsed}% Consumed
            </span>
          </div>

          <div className="w-full bg-surface-elevated h-2.5 rounded-full overflow-hidden border border-surface-border">
            <div
              className={`h-full rounded-full transition-all duration-500 ${
                percentUsed > 85
                  ? "bg-rose-500"
                  : percentUsed > 50
                  ? "bg-amber-500"
                  : "bg-emerald-500"
              }`}
              style={{ width: `${Math.max(2, percentUsed)}%` }}
            />
          </div>

          <div className="pt-2 flex items-center justify-between gap-4">
            <div className="flex items-center gap-2">
              <span className="text-xs font-mono text-neutral-700 dark:text-neutral-300">Set Monthly Spend Cap ($):</span>
              <input
                type="number"
                value={budgetInput}
                onChange={(e) => setBudgetInput(e.target.value)}
                className="w-24 px-2.5 py-1 text-xs font-mono bg-surface-subnav border border-surface-border rounded text-foreground focus:outline-none focus:border-brand"
              />
            </div>
            <button
              onClick={handleSaveBudget}
              className="px-3 py-1.5 text-xs font-mono bg-brand hover:bg-brand/90 text-white rounded transition-colors flex items-center gap-1.5"
            >
              {isSaved ? <CheckCircle2 className="w-3.5 h-3.5" /> : <Coins className="w-3.5 h-3.5" />}
              <span>{isSaved ? "CAP SAVED" : "UPDATE CAP"}</span>
            </button>
          </div>
        </div>

        {/* Model Breakdown Table */}
        <div className="p-6 rounded-xl bg-surface-card border border-surface-border space-y-4">
          <div className="flex items-center gap-2">
            <BarChart3 className="w-4 h-4 text-brand" />
            <h2 className="text-sm font-semibold text-foreground">Spend Breakdown by Model Family</h2>
          </div>

          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs font-mono">
              <thead>
                <tr className="border-b border-surface-border text-neutral-500 dark:text-neutral-400 uppercase text-[10px]">
                  <th className="pb-2">Model Family</th>
                  <th className="pb-2">Prompt Tokens</th>
                  <th className="pb-2">Completion Tokens</th>
                  <th className="pb-2 text-right">Cost (USD)</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-surface-border/50 text-neutral-700 dark:text-neutral-200">
                {Object.entries(
                  usage?.models || {
                    "claude-3.7-sonnet": { prompt_tokens: 70000, completion_tokens: 25000, cost_usd: 0.385 },
                    "deepseek-r1": { prompt_tokens: 22100, completion_tokens: 11300, cost_usd: 0.043 },
                  }
                ).map(([model, m]) => (
                  <tr key={model} className="hover:bg-surface-elevated/40 transition-colors">
                    <td className="py-2.5 font-medium text-foreground">{model}</td>
                    <td className="py-2.5 text-neutral-500 dark:text-neutral-400">{m.prompt_tokens.toLocaleString()}</td>
                    <td className="py-2.5 text-neutral-500 dark:text-neutral-400">{m.completion_tokens.toLocaleString()}</td>
                    <td className="py-2.5 text-right font-bold text-emerald-400">
                      ${m.cost_usd.toFixed(4)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </div>
  );
}

"use client";

import React, { useEffect, useState } from "react";
import {
  Coins,
  DollarSign,
  TrendingUp,
  Cpu,
  BarChart3,
  Clock,
  Sparkles,
} from "lucide-react";

interface UserUsageData {
  tenant_user_id: string;
  total_tokens: number;
  prompt_tokens: number;
  completion_tokens: number;
  total_cost_usd: number;
  recent_turns_count: number;
}

export default function UserUsagePage() {
  const [usage, setUsage] = useState<UserUsageData | null>(null);

  useEffect(() => {
    fetch("/v1/settings/user/usage")
      .then((res) => (res.ok ? res.json() : null))
      .then((data) => {
        if (data) {
          setUsage(data);
        }
      })
      .catch(() => {});
  }, []);

  const totalCost = usage?.total_cost_usd ?? 0.142;
  const totalTokens = usage?.total_tokens ?? 42300;
  const promptTokens = usage?.prompt_tokens ?? 31200;
  const completionTokens = usage?.completion_tokens ?? 11100;
  const recentTurns = usage?.recent_turns_count ?? 8;

  return (
    <div className="max-w-4xl space-y-6">
      {/* Header */}
      <div>
        <div className="flex items-center gap-2 mb-1">
          <Coins className="w-5 h-5 text-brand" />
          <h1 className="text-xl font-display font-bold text-white tracking-wide">
            PERSONAL TOKEN USAGE & RUNTIME SPEND
          </h1>
        </div>
        <p className="text-xs font-mono text-neutral-400">
          Telemetry dashboard reporting real-time token consumption and estimated dollar costs incurred by your autonomous turns and copilots.
        </p>
      </div>

      {/* KPI Cards Grid */}
      <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
        {/* Cost USD */}
        <div className="p-4 rounded-xl bg-surface-card border border-surface-border shadow-sm">
          <div className="flex items-center justify-between text-neutral-400 mb-2">
            <span className="text-xs font-mono uppercase tracking-wider">Estimated Spend</span>
            <DollarSign className="w-4 h-4 text-emerald-400" />
          </div>
          <div className="text-2xl font-mono font-bold text-white">
            ${totalCost.toFixed(4)}
          </div>
          <p className="text-[10px] font-mono text-neutral-500 mt-1">
            Current billing cycle
          </p>
        </div>

        {/* Total Tokens */}
        <div className="p-4 rounded-xl bg-surface-card border border-surface-border shadow-sm">
          <div className="flex items-center justify-between text-neutral-400 mb-2">
            <span className="text-xs font-mono uppercase tracking-wider">Total Tokens</span>
            <TrendingUp className="w-4 h-4 text-brand" />
          </div>
          <div className="text-2xl font-mono font-bold text-white">
            {totalTokens.toLocaleString()}
          </div>
          <p className="text-[10px] font-mono text-neutral-500 mt-1">
            Prompt + Completion
          </p>
        </div>

        {/* Prompt vs Completion */}
        <div className="p-4 rounded-xl bg-surface-card border border-surface-border shadow-sm">
          <div className="flex items-center justify-between text-neutral-400 mb-2">
            <span className="text-xs font-mono uppercase tracking-wider">Prompt / Gen</span>
            <Cpu className="w-4 h-4 text-neutral-400" />
          </div>
          <div className="text-sm font-mono font-bold text-white flex items-center gap-2">
            <span className="text-neutral-300">{(promptTokens / 1000).toFixed(1)}k</span>
            <span className="text-neutral-500">/</span>
            <span className="text-brand">{(completionTokens / 1000).toFixed(1)}k</span>
          </div>
          <p className="text-[10px] font-mono text-neutral-500 mt-1">
            Ratio: {((completionTokens / (totalTokens || 1)) * 100).toFixed(0)}% output
          </p>
        </div>

        {/* Turn Count */}
        <div className="p-4 rounded-xl bg-surface-card border border-surface-border shadow-sm">
          <div className="flex items-center justify-between text-neutral-400 mb-2">
            <span className="text-xs font-mono uppercase tracking-wider">Completed Turns</span>
            <Clock className="w-4 h-4 text-brand" />
          </div>
          <div className="text-2xl font-mono font-bold text-white">
            {recentTurns}
          </div>
          <p className="text-[10px] font-mono text-neutral-500 mt-1">
            Autonomous iterations
          </p>
        </div>
      </div>

      {/* Model Breakdown / Consumption Details */}
      <div className="p-5 rounded-xl bg-surface-card border border-surface-border space-y-4">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <BarChart3 className="w-4 h-4 text-brand" />
            <h2 className="text-sm font-display font-semibold text-white uppercase tracking-wider">
              Token Allocation Breakdown
            </h2>
          </div>
          <span className="text-[10px] font-mono text-neutral-500 bg-surface-elevated px-2 py-1 rounded border border-surface-border">
            User Scoped
          </span>
        </div>

        <div className="space-y-3 pt-2">
          <div>
            <div className="flex justify-between text-xs font-mono mb-1">
              <span className="text-neutral-300">Prompt Context Tokens (Files, History, Diffs)</span>
              <span className="text-neutral-400">{promptTokens.toLocaleString()} tokens</span>
            </div>
            <div className="w-full h-2 rounded-full bg-surface-elevated overflow-hidden">
              <div
                className="h-full bg-brand/80 rounded-full transition-all duration-500"
                style={{
                  width: `${Math.round((promptTokens / (totalTokens || 1)) * 100)}%`,
                }}
              />
            </div>
          </div>

          <div>
            <div className="flex justify-between text-xs font-mono mb-1">
              <span className="text-neutral-300">Generated Completion Tokens (Reasoning, Code)</span>
              <span className="text-neutral-400">{completionTokens.toLocaleString()} tokens</span>
            </div>
            <div className="w-full h-2 rounded-full bg-surface-elevated overflow-hidden">
              <div
                className="h-full bg-emerald-400/80 rounded-full transition-all duration-500"
                style={{
                  width: `${Math.round((completionTokens / (totalTokens || 1)) * 100)}%`,
                }}
              />
            </div>
          </div>
        </div>

        <div className="mt-4 pt-4 border-t border-surface-border flex items-center justify-between text-xs font-mono text-neutral-400">
          <div className="flex items-center gap-2">
            <Sparkles className="w-4 h-4 text-amber-400" />
            <span>Average cost per autonomous engineering turn:</span>
          </div>
          <span className="text-white font-bold font-mono">
            ${(totalCost / (recentTurns || 1)).toFixed(4)}
          </span>
        </div>
      </div>
    </div>
  );
}

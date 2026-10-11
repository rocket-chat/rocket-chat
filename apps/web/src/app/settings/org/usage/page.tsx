"use client";

import React, { useEffect, useState } from "react";
import {
  Coins,
  TrendingUp,
  Cpu,
  CheckCircle2,
  AlertCircle,
  DollarSign,
  BarChart3,
  Clock,
  Save,
} from "lucide-react";
import { useSession } from "../../../../components/AuthProvider";
import {
  UserScopeSection,
  ScopeDivider,
  OrgScopeSection,
} from "../../../../components/settings/ScopedSettingsSection";

interface UserUsageData {
  tenant_user_id: string;
  total_tokens: number;
  prompt_tokens: number;
  completion_tokens: number;
  total_cost_usd: number;
  recent_turns_count: number;
}

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

export default function UsagePage() {
  const { data: session } = useSession();
  const userRoles = (session?.user as unknown as { roles?: string[] })?.roles || [];
  const isAdmin = !session || userRoles.includes("admin") || userRoles.includes("owner");

  const [userUsage, setUserUsage] = useState<UserUsageData | null>(null);
  const [orgUsage, setOrgUsage] = useState<OrgUsageData | null>(null);
  const [budgetInput, setBudgetInput] = useState<string>("0");
  const [savingBudget, setSavingBudget] = useState(false);
  const [successBanner, setSuccessBanner] = useState<string | null>(null);
  const [errorBanner, setErrorBanner] = useState<string | null>(null);

  const fetchUsageData = async () => {
    try {
      const [uRes, oRes] = await Promise.all([
        fetch("/v1/settings/user/usage"),
        fetch("/v1/settings/org/usage"),
      ]);

      if (uRes.ok) {
        const uData = await uRes.json();
        setUserUsage(uData);
      }
      if (oRes.ok) {
        const oData = await oRes.json();
        setOrgUsage(oData);
        if (oData.monthly_budget_cap_usd) {
          setBudgetInput(String(oData.monthly_budget_cap_usd));
        }
      }
    } catch (err) {
      console.warn("Failed loading usage telemetry:", err);
    }
  };

  useEffect(() => {
    fetchUsageData();
  }, []);

  const handleSaveBudget = async () => {
    if (!isAdmin) return;
    const parsedCap = parseFloat(budgetInput);
    if (isNaN(parsedCap) || parsedCap < 0) {
      setErrorBanner("Please specify a monthly budget cap of $0 (no cap) or more");
      return;
    }
    setSavingBudget(true);
    setErrorBanner(null);
    try {
      const res = await fetch("/v1/admin/budget", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ monthly_budget_cap_usd: parsedCap }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || `Request failed with HTTP ${res.status}`);
      }
      setSuccessBanner(
        parsedCap === 0 ? "Monthly spend cap removed" : `Monthly spend cap updated to $${parsedCap.toFixed(2)}`
      );
      fetchUsageData();
      setTimeout(() => setSuccessBanner(null), 3500);
    } catch (err: unknown) {
      setErrorBanner(err instanceof Error ? err.message : "Failed to update monthly budget cap");
      setTimeout(() => setErrorBanner(null), 5000);
    } finally {
      setSavingBudget(false);
    }
  };

  const userCost = userUsage?.total_cost_usd ?? 0.0;
  const orgCost = orgUsage?.total_cost_usd ?? 0.0;
  const budgetCap = parseFloat(budgetInput) || 0;
  const percentUsed = budgetCap > 0 ? Math.min(100, Math.round((orgCost / budgetCap) * 100)) : 0;

  return (
    <div className="max-w-5xl mx-auto space-y-6 px-4 py-8 sm:px-8">
      {/* Header */}
      <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4 pb-4 border-b border-border/70">
        <div>
          <h1 className="font-display text-xl font-semibold tracking-tight text-foreground flex items-center gap-2.5">
            <Coins className="w-5 h-5 text-emerald-400" />
            Token usage &amp; spend
          </h1>
          <p className="max-w-2xl text-sm leading-relaxed text-neutral-600 dark:text-neutral-400 mt-1">
            Personal runtime token consumption, mission cost breakdown, and organization monthly spend cap ledger.
          </p>
        </div>
      </div>

      {errorBanner && (
        <div className="flex items-center gap-2 p-3 rounded-lg bg-rose-950/60 border border-rose-800/60 text-rose-400 text-xs font-mono">
          <AlertCircle className="w-4 h-4 shrink-0" />
          <span>{errorBanner}</span>
        </div>
      )}

      {successBanner && (
        <div className="flex items-center gap-2 p-3 rounded-lg bg-emerald-950/60 border border-emerald-800/60 text-emerald-400 text-xs font-mono">
          <CheckCircle2 className="w-4 h-4 shrink-0" />
          <span>{successBanner}</span>
        </div>
      )}

      {/* TIER 1: USER-SPECIFIC USAGE */}
      <UserScopeSection
        title="Personal Token Usage & Cost"
        description="Tokens consumed and dollar spend incurred by your missions, copilots, and interactive sessions."
      >
        <div className="grid grid-cols-1 sm:grid-cols-4 gap-3">
          <div className="p-3.5 rounded-lg border border-border bg-surface-card space-y-1">
            <div className="flex items-center gap-2 text-muted-foreground text-[10px] font-mono uppercase">
              <DollarSign className="w-3.5 h-3.5 text-emerald-400" />
              <span>Personal Spend</span>
            </div>
            <div className="text-xl font-bold font-mono text-foreground">
              ${userCost.toFixed(4)}
            </div>
            <div className="text-[10px] font-mono text-muted-foreground">USD incurred</div>
          </div>

          <div className="p-3.5 rounded-lg border border-border bg-surface-card space-y-1">
            <div className="flex items-center gap-2 text-muted-foreground text-[10px] font-mono uppercase">
              <Cpu className="w-3.5 h-3.5 text-cyan" />
              <span>Total Tokens</span>
            </div>
            <div className="text-xl font-bold font-mono text-foreground">
              {(userUsage?.total_tokens ?? 0).toLocaleString()}
            </div>
            <div className="text-[10px] font-mono text-muted-foreground">
              Prompt: {(userUsage?.prompt_tokens ?? 0).toLocaleString()}
            </div>
          </div>

          <div className="p-3.5 rounded-lg border border-border bg-surface-card space-y-1">
            <div className="flex items-center gap-2 text-muted-foreground text-[10px] font-mono uppercase">
              <TrendingUp className="w-3.5 h-3.5 text-brand" />
              <span>Completion</span>
            </div>
            <div className="text-xl font-bold font-mono text-foreground">
              {(userUsage?.completion_tokens ?? 0).toLocaleString()}
            </div>
            <div className="text-[10px] font-mono text-muted-foreground">Generated tokens</div>
          </div>

          <div className="p-3.5 rounded-lg border border-border bg-surface-card space-y-1">
            <div className="flex items-center gap-2 text-muted-foreground text-[10px] font-mono uppercase">
              <Clock className="w-3.5 h-3.5 text-amber-400" />
              <span>Active Turns</span>
            </div>
            <div className="text-xl font-bold font-mono text-foreground">
              {userUsage?.recent_turns_count ?? 0}
            </div>
            <div className="text-[10px] font-mono text-muted-foreground">Executed turns</div>
          </div>
        </div>
      </UserScopeSection>

      {/* VISUAL SEPARATOR */}
      <ScopeDivider label="Organization Scope" />

      {/* TIER 2: ORGANIZATION-WIDE USAGE */}
      <OrgScopeSection
        title="Organization Budget & Model Spend"
        description="Aggregated token consumption and enforcement of workspace monthly spend caps."
        action={
          isAdmin ? (
            <button
              type="button"
              onClick={handleSaveBudget}
              disabled={savingBudget}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded border border-cyan/40 bg-cyan text-slate-950 hover:bg-cyan/90 text-xs font-mono font-semibold transition-all cursor-pointer shadow-sm"
            >
              <Save className="w-3.5 h-3.5" />
              <span>{savingBudget ? "SAVING..." : "UPDATE SPEND CAP"}</span>
            </button>
          ) : undefined
        }
      >
        <div className="space-y-4">
          <div className="p-4 rounded-lg border border-border bg-surface-card space-y-3">
            <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
              <div>
                <span className="text-xs font-mono font-semibold text-foreground uppercase">
                  Monthly Budget Cap Enforcement
                </span>
                <p className="text-[11px] font-mono text-muted-foreground mt-0.5">
                  Missions automatically throttle when consumption hits the hard monthly cap.
                </p>
              </div>

              <div className="flex items-center gap-2">
                <span className="text-xs font-mono text-muted-foreground">$</span>
                <input
                  type="number"
                  disabled={!isAdmin}
                  value={budgetInput}
                  onChange={(e) => setBudgetInput(e.target.value)}
                  className="w-24 px-2 py-1 rounded border border-border bg-void text-xs font-mono text-foreground focus:outline-none focus:border-cyan disabled:opacity-60"
                />
                <span className="text-xs font-mono text-muted-foreground">USD / mo</span>
              </div>
            </div>

            {/* Progress Bar */}
            <div className="space-y-1">
              <div className="flex justify-between text-[10px] font-mono text-muted-foreground">
                <span>Total Spend: ${orgCost.toFixed(2)}</span>
                <span>{budgetCap > 0 ? `${percentUsed}% of $${budgetCap.toFixed(2)} Cap` : "No monthly cap (0 = unlimited)"}</span>
              </div>
              <div className="w-full h-2 rounded-full bg-surface border border-border overflow-hidden">
                <div
                  className={`h-full transition-all duration-300 ${
                    percentUsed > 90 ? "bg-rose-500" : percentUsed > 70 ? "bg-amber-400" : "bg-emerald-400"
                  }`}
                  style={{ width: `${percentUsed}%` }}
                />
              </div>
            </div>
          </div>

          {/* Model Breakdown */}
          {orgUsage?.models && Object.keys(orgUsage.models).length > 0 && (
            <div className="p-4 rounded-lg border border-border bg-surface-card space-y-3">
              <div className="text-xs font-mono font-semibold text-foreground uppercase tracking-wider flex items-center gap-2">
                <BarChart3 className="w-4 h-4 text-cyan" />
                <span>Spend Breakdown by Model</span>
              </div>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                {Object.entries(orgUsage.models).map(([modelId, mData]) => (
                  <div
                    key={modelId}
                    className="p-2.5 rounded border border-border/70 bg-surface/40 flex items-center justify-between text-xs font-mono"
                  >
                    <div className="min-w-0">
                      <span className="font-semibold text-foreground truncate block">
                        {modelId}
                      </span>
                      <span className="text-[10px] text-muted-foreground">
                        {(mData.prompt_tokens + mData.completion_tokens).toLocaleString()} tokens
                      </span>
                    </div>
                    <span className="font-semibold text-emerald-400 shrink-0">
                      ${mData.cost_usd.toFixed(4)}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      </OrgScopeSection>
    </div>
  );
}

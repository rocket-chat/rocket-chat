"use client";

import React, { useCallback, useEffect, useState } from "react";
import { AlertCircle, Plus, Trash2 } from "lucide-react";
import { Field, Input, Select } from "../ui/Field";
import { hasCapability, useAccess } from "../../lib/access";

export interface SpendLimit {
  id: string;
  subject_kind: string;
  subject_id: string | null;
  member_id: string | null;
  period: string;
  amount_usd: number;
  mode: string;
  warn_at_percent: number;
  is_ceiling: boolean;
  label: string;
  spent_usd: number | null;
  resets_at: number | null;
}

interface Named {
  id: string;
  name: string;
}

const SUBJECTS: { value: string; label: string }[] = [
  { value: "org", label: "The organization" },
  { value: "member", label: "Each member" },
  { value: "agent", label: "An agent" },
  { value: "agent_member", label: "An agent, for each member" },
  { value: "slack_workspace", label: "A Slack workspace" },
];
const PERIODS: { value: string; label: string }[] = [
  { value: "day", label: "Per day" },
  { value: "week", label: "Per week" },
  { value: "month", label: "Per month" },
  { value: "rolling_24h", label: "Rolling 24 hours" },
  { value: "rolling_7d", label: "Rolling 7 days" },
  { value: "session", label: "Per session" },
  { value: "turn", label: "Per turn" },
];

function used(limit: SpendLimit): string {
  if (limit.spent_usd === null) return "per subject";
  const percent = limit.amount_usd > 0 ? Math.round((limit.spent_usd / limit.amount_usd) * 100) : 0;
  return `$${limit.spent_usd.toFixed(2)} (${percent}%)`;
}

/** The organization's spend limits: subject, period, amount, mode and what is used. */
export const SpendLimitsPanel: React.FC = () => {
  const access = useAccess();
  const canManage = hasCapability(access, "manage_spend_limits");
  const [limits, setLimits] = useState<SpendLimit[]>([]);
  const [agents, setAgents] = useState<Named[]>([]);
  const [subject, setSubject] = useState("org");
  const [subjectId, setSubjectId] = useState("");
  const [period, setPeriod] = useState("month");
  const [amount, setAmount] = useState("100");
  const [mode, setMode] = useState("block");
  const [ceiling, setCeiling] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    const res = await fetch("/v1/admin/spend-limits");
    if (res.ok) setLimits(await res.json());
    const list = await fetch("/v1/agents");
    if (list.ok) setAgents(((await list.json()) as Named[]).map((a) => ({ id: a.id, name: a.name })));
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const agentName = (id: string | null) => agents.find((a) => a.id === id)?.name ?? id ?? "";

  const create = async (event: React.FormEvent) => {
    event.preventDefault();
    setError(null);
    const needsSubject = ["agent", "agent_member", "slack_workspace"].includes(subject);
    const res = await fetch("/v1/admin/spend-limits", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        subject_kind: subject,
        subject_id: needsSubject ? subjectId : null,
        period,
        amount_usd: Number(amount),
        mode,
        is_ceiling: ceiling && (subject === "member" || subject === "agent_member"),
      }),
    });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      const detail = Array.isArray(body.detail) ? body.detail.map((d: { msg: string }) => d.msg).join("; ") : body.detail;
      setError(typeof detail === "string" ? detail : "The limit was not saved.");
      return;
    }
    await load();
  };

  const remove = async (id: string) => {
    await fetch(`/v1/admin/spend-limits/${id}`, { method: "DELETE" });
    await load();
  };

  const toggleMode = async (limit: SpendLimit) => {
    await fetch(`/v1/admin/spend-limits/${limit.id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mode: limit.mode === "block" ? "warn" : "block" }),
    });
    await load();
  };

  return (
    <section className="space-y-4" data-testid="spend-limits-panel">
      <div>
        <h2 className="font-display text-base font-semibold text-foreground">Spend limits</h2>
        <p className="mt-1 text-xs text-neutral-600 dark:text-neutral-400">
          A block limit refuses the model call that would cross it, before anything is bought; a warn
          limit tells the member. Calls on a member&apos;s own key count against no limit.
        </p>
      </div>
      {error && (
        <p role="alert" className="flex items-center gap-2 text-xs text-rose-500">
          <AlertCircle className="h-4 w-4" aria-hidden="true" /> {error}
        </p>
      )}
      <div className="overflow-x-auto rounded-xl border border-surface-border bg-surface-card">
        <table className="w-full text-xs">
          <thead className="text-[10px] text-muted-foreground">
            <tr>
              <th className="px-4 py-2 text-left">Subject</th>
              <th className="px-4 py-2 text-left">Period</th>
              <th className="px-4 py-2 text-right">Amount</th>
              <th className="px-4 py-2 text-left">Mode</th>
              <th className="px-4 py-2 text-right">Used</th>
              <th className="px-4 py-2" aria-label="Actions" />
            </tr>
          </thead>
          <tbody>
            {limits.length === 0 && (
              <tr>
                <td colSpan={6} className="px-4 py-4 text-center text-neutral-500">
                  No limits: spend is not capped.
                </td>
              </tr>
            )}
            {limits.map((limit) => (
              <tr key={limit.id} className="border-t border-surface-border/60" data-testid={`spend-limit-${limit.id}`}>
                <td className="px-4 py-2 text-foreground">
                  {SUBJECTS.find((s) => s.value === limit.subject_kind)?.label ?? limit.subject_kind}
                  {limit.subject_id && (
                    <span className="text-neutral-500">
                      {" "}
                      · {limit.subject_kind.startsWith("agent") ? agentName(limit.subject_id) : limit.subject_id}
                    </span>
                  )}
                  {limit.is_ceiling && <span className="ml-1 text-[10px] text-neutral-500">(ceiling)</span>}
                </td>
                <td className="px-4 py-2">{PERIODS.find((p) => p.value === limit.period)?.label ?? limit.period}</td>
                <td className="px-4 py-2 text-right font-mono">${limit.amount_usd.toFixed(2)}</td>
                <td className="px-4 py-2">
                  <button
                    type="button"
                    disabled={!canManage}
                    onClick={() => void toggleMode(limit)}
                    className="rounded border border-surface-border px-1.5 py-0.5 text-[10px] capitalize disabled:opacity-60"
                  >
                    {limit.mode}
                  </button>
                </td>
                <td className="px-4 py-2 text-right text-neutral-500">{used(limit)}</td>
                <td className="px-4 py-2 text-right">
                  {canManage && (
                    <button
                      type="button"
                      onClick={() => void remove(limit.id)}
                      aria-label={`Delete the limit ${limit.label}`}
                      className="p-1 text-neutral-500 hover:text-rose-500"
                    >
                      <Trash2 className="h-3.5 w-3.5" aria-hidden="true" />
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {canManage && (
        <form
          onSubmit={create}
          className="grid gap-3 rounded-xl border border-surface-border bg-surface-card p-4 sm:grid-cols-3"
          data-testid="spend-limit-form"
        >
          <Field label="Subject">
            {(control) => (
              <Select {...control} value={subject} data-testid="spend-limit-subject" onChange={(e) => setSubject(e.target.value)}>
                {SUBJECTS.map((s) => (
                  <option key={s.value} value={s.value}>
                    {s.label}
                  </option>
                ))}
              </Select>
            )}
          </Field>
          {(subject === "agent" || subject === "agent_member") && (
            <Field label="Agent">
              {(control) => (
                <Select {...control} value={subjectId} onChange={(e) => setSubjectId(e.target.value)}>
                  <option value="">Choose an agent</option>
                  {agents.map((a) => (
                    <option key={a.id} value={a.id}>
                      {a.name}
                    </option>
                  ))}
                </Select>
              )}
            </Field>
          )}
          {subject === "slack_workspace" && (
            <Field label="Slack team id">
              {(control) => <Input {...control} value={subjectId} onChange={(e) => setSubjectId(e.target.value)} />}
            </Field>
          )}
          <Field label="Period">
            {(control) => (
              <Select {...control} value={period} data-testid="spend-limit-period" onChange={(e) => setPeriod(e.target.value)}>
                {PERIODS.map((p) => (
                  <option key={p.value} value={p.value}>
                    {p.label}
                  </option>
                ))}
              </Select>
            )}
          </Field>
          <Field label="Amount (USD)">
            {(control) => (
              <Input
                {...control}
                type="number"
                min={0.01}
                step="0.01"
                value={amount}
                data-testid="spend-limit-amount"
                onChange={(e) => setAmount(e.target.value)}
              />
            )}
          </Field>
          <Field label="Mode">
            {(control) => (
              <Select {...control} value={mode} onChange={(e) => setMode(e.target.value)}>
                <option value="block">Block</option>
                <option value="warn">Warn</option>
              </Select>
            )}
          </Field>
          {(subject === "member" || subject === "agent_member") && (
            <label className="flex items-center gap-2 text-xs text-foreground">
              <input type="checkbox" checked={ceiling} onChange={(e) => setCeiling(e.target.checked)} />
              A ceiling: a limit for one member may only be lower
            </label>
          )}
          <div className="sm:col-span-3">
            <button
              type="submit"
              data-testid="spend-limit-save"
              className="inline-flex items-center gap-1.5 rounded-lg bg-brand px-4 py-2 text-xs font-semibold text-white hover:bg-brand-hover"
            >
              <Plus className="h-3.5 w-3.5" aria-hidden="true" /> Add limit
            </button>
          </div>
        </form>
      )}
    </section>
  );
};

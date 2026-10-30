"use client";

import React, { useCallback, useEffect, useState } from "react";
import { Trash2 } from "lucide-react";

interface Rule {
  id: string;
  scope: string;
  tool: string;
  command: string;
  repository: string;
  last_used_at: number | null;
}

/** The approvals you chose to remember for a repository, each revocable. */
export const RememberedApprovalsPanel: React.FC = () => {
  const [rules, setRules] = useState<Rule[] | null>(null);
  const [allowed, setAllowed] = useState<string[]>([]);

  const load = useCallback(async () => {
    const res = await fetch("/v1/approval-rules").catch(() => null);
    if (!res || !res.ok) return setRules([]);
    const body = (await res.json()) as { allowed_scopes: string[]; rules: Rule[] };
    setRules(body.rules);
    setAllowed(body.allowed_scopes);
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const revoke = async (id: string) => {
    await fetch(`/v1/approval-rules/${id}`, { method: "DELETE" }).catch(() => null);
    await load();
  };

  return (
    <section className="space-y-2" data-testid="remembered-approvals">
      <h2 className="font-display text-base font-semibold text-foreground">Remembered approvals</h2>
      <p className="text-xs text-neutral-600 dark:text-neutral-400">
        {allowed.includes("repository")
          ? "Commands you allowed for good in a repository. Approvals remembered for one session end with it."
          : "Your organization does not let approvals be remembered for a repository."}
      </p>
      {rules && rules.length > 0 && (
        <ul className="divide-y divide-border rounded-lg border border-border text-xs">
          {rules.map((rule) => (
            <li key={rule.id} className="flex items-center gap-3 px-3 py-2">
              <span className="font-mono text-foreground">{rule.command || `every ${rule.tool} call`}</span>
              <span className="text-neutral-600 dark:text-neutral-400">{rule.repository}</span>
              <button
                type="button"
                aria-label={`Revoke ${rule.command || rule.tool} in ${rule.repository}`}
                onClick={() => void revoke(rule.id)}
                className="ml-auto rounded p-1 text-neutral-600 hover:bg-surface-elevated dark:text-neutral-400"
              >
                <Trash2 className="h-3.5 w-3.5" aria-hidden="true" />
              </button>
            </li>
          ))}
        </ul>
      )}
      {rules && rules.length === 0 && <p className="text-xs text-neutral-600 dark:text-neutral-400">None.</p>}
    </section>
  );
};

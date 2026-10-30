"use client";

import React, { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { AlertTriangle, CheckCircle2, HelpCircle, RefreshCw, XCircle } from "lucide-react";

interface HealthCheck {
  id: string;
  title: string;
  status: "ok" | "warning" | "error" | "unknown";
  detail: string;
  fix_label: string;
  fix_href: string;
}

const ICON = {
  ok: <CheckCircle2 className="h-4 w-4 text-emerald-700 dark:text-emerald-400" aria-label="OK" />,
  warning: <AlertTriangle className="h-4 w-4 text-amber-700 dark:text-amber-400" aria-label="Needs attention" />,
  error: <XCircle className="h-4 w-4 text-red-700 dark:text-red-400" aria-label="Missing" />,
  unknown: <HelpCircle className="h-4 w-4 text-neutral-600 dark:text-neutral-400" aria-label="Unknown" />,
};

/** What the organization is missing (a provider key, embeddings, web search, the GitHub App,
 * the sandbox images), each with where to fix it. */
export const SetupHealthCard: React.FC = () => {
  const [checks, setChecks] = useState<HealthCheck[] | null>(null);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    const res = await fetch("/v1/admin/setup-health").catch(() => null);
    setChecks(res && res.ok ? await res.json() : []);
    setLoading(false);
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  if (checks !== null && checks.length === 0) return null;
  const open = (checks ?? []).filter((c) => c.status === "error" || c.status === "warning").length;
  return (
    <section data-testid="setup-health" aria-label="Setup health" className="rounded-xl border border-surface-border bg-surface-card p-4">
      <div className="mb-2 flex items-center gap-2">
        <h2 className="font-display text-base font-semibold text-foreground">Setup health</h2>
        <span className="text-xs text-neutral-600 dark:text-neutral-400">
          {checks === null ? "Checking…" : open === 0 ? "Everything is set up" : `${open} to look at`}
        </span>
        <button type="button" aria-label="Check again" onClick={() => void load()} className="ml-auto rounded p-1 hover:bg-surface-elevated">
          <RefreshCw className={`h-3.5 w-3.5 ${loading ? "animate-spin" : ""}`} aria-hidden="true" />
        </button>
      </div>
      <ul className="divide-y divide-border">
        {(checks ?? []).map((check) => (
          <li key={check.id} data-testid={`setup-health-${check.id}`} className="flex items-start gap-3 py-2 text-sm">
            <span className="mt-0.5">{ICON[check.status]}</span>
            <div className="min-w-0 flex-1">
              <p className="font-medium text-foreground">{check.title}</p>
              <p className="text-xs text-neutral-700 dark:text-neutral-300">{check.detail}</p>
            </div>
            {check.fix_href &&
              (check.fix_href.startsWith("/") ? (
                <Link href={check.fix_href} className="shrink-0 text-xs text-brand hover:underline">
                  {check.fix_label}
                </Link>
              ) : (
                <a href={check.fix_href} target="_blank" rel="noreferrer" className="shrink-0 text-xs text-brand hover:underline">
                  {check.fix_label}
                </a>
              ))}
          </li>
        ))}
      </ul>
    </section>
  );
};

"use client";

import React, { useCallback, useEffect, useState } from "react";
import { RefreshCw } from "lucide-react";
import type { RouteDecisionItem } from "../types";

/** The bot's recent routing decisions, newest first. */
export const ActivityTab: React.FC<{ botId: string }> = ({ botId }) => {
  const [decisions, setDecisions] = useState<RouteDecisionItem[]>([]);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await fetch(`/v1/admin/slack/bots/${botId}/decisions?limit=50`);
      if (res.ok) setDecisions(await res.json());
    } catch {
      // The list stays as it was; Refresh tries again.
    } finally {
      setLoading(false);
    }
  }, [botId]);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <span className="text-xs font-semibold text-foreground">Recent Routing Decisions</span>
        <button type="button" onClick={() => void load()} disabled={loading} className="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground">
          <RefreshCw className={`h-3 w-3 ${loading ? "animate-spin" : ""}`} aria-hidden="true" /> <span>Refresh</span>
        </button>
      </div>
      {decisions.length === 0 ? (
        <p className="p-4 text-center text-xs text-muted-foreground">No decisions recorded yet.</p>
      ) : (
        <div className="overflow-hidden rounded-lg border border-border">
          <table className="w-full text-left text-xs">
            <thead className="border-b border-border bg-surface-subnav/80 text-muted-foreground">
              <tr>
                <th className="p-2.5">Time</th>
                <th className="p-2.5">Route</th>
                <th className="p-2.5">Confidence</th>
                <th className="p-2.5">Excerpt</th>
                <th className="p-2.5">Latency</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border/60">
              {decisions.map((decision) => (
                <tr key={decision.id} className="hover:bg-surface-elevated/40">
                  <td className="whitespace-nowrap p-2.5 text-muted-foreground">{new Date(decision.created_at * 1000).toLocaleTimeString()}</td>
                  <td className="p-2.5 font-medium text-brand">{decision.applied || decision.route}</td>
                  <td className="p-2.5">{Math.round(decision.confidence * 100)}%</td>
                  <td className="max-w-xs truncate p-2.5" title={decision.excerpt}>
                    {decision.excerpt || "—"}
                  </td>
                  <td className="p-2.5 text-muted-foreground">{decision.latency_ms}ms</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
};

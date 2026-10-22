"use client";

import React, { useCallback, useEffect, useState } from "react";
import { ScrollText, AlertCircle, RefreshCw } from "lucide-react";
import { useSession } from "../../../../components/AuthProvider";
import { Field, Select } from "../../../../components/ui/Field";

interface AuditEvent {
  id: string;
  event_type: string;
  actor_user_id: string | null;
  session_id: string | null;
  tool_name: string | null;
  risk_level: string | null;
  approved_by: string | null;
  result_summary: string | null;
  created_at: number;
  redacted: boolean;
  redaction_count: number;
}

interface AuditPage {
  events: AuditEvent[];
  next_cursor: string | null;
  retention: { audit_days: number | null };
}

const EVENT_TYPES = [
  "tool_call",
  "approval_request",
  "approval_decision",
  "model_call",
  "credential_change",
  "settings_change",
  "git_publish",
];

export default function AuditLogPage() {
  const { data: session } = useSession();
  const userRoles = (session?.user as unknown as { roles?: string[] })?.roles || [];
  const isAdmin = !session || userRoles.includes("admin") || userRoles.includes("owner");

  const [events, setEvents] = useState<AuditEvent[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [auditDays, setAuditDays] = useState<number | null>(null);
  const [typeFilter, setTypeFilter] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async (after: string | null) => {
    setLoading(true);
    setError(null);
    try {
      const query = new URLSearchParams({ limit: "50" });
      if (after) query.set("cursor", after);
      const res = await fetch(`/v1/admin/audit?${query}`);
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || `Failed to load the audit log (${res.status})`);
      const page = data as AuditPage;
      setEvents((previous) => (after ? [...previous, ...page.events] : page.events));
      setCursor(page.next_cursor);
      setAuditDays(page.retention.audit_days);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load the audit log");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (isAdmin) void load(null);
  }, [isAdmin, load]);

  if (!isAdmin) {
    return (
      <div className="max-w-5xl mx-auto px-4 py-8 sm:px-8 text-sm text-neutral-500">
        The audit log is managed by organization administrators.
      </div>
    );
  }

  const visible = typeFilter ? events.filter((event) => event.event_type === typeFilter) : events;

  return (
    <div className="max-w-5xl mx-auto space-y-6 px-4 py-8 sm:px-8">
      <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4 pb-4 border-b border-border/70">
        <div>
          <h1 className="font-display text-xl font-semibold tracking-tight text-foreground flex items-center gap-2.5">
            <ScrollText className="w-5 h-5 text-brand" />
            Audit log
          </h1>
          <p className="max-w-2xl text-sm leading-relaxed text-neutral-600 dark:text-neutral-400 mt-1">
            Approvals, tool calls, credential and settings changes, newest first.{" "}
            {auditDays === null
              ? "Events are kept indefinitely."
              : `Events are kept for ${auditDays} days (Settings → Security & data).`}
          </p>
        </div>
        <button
          type="button"
          onClick={() => void load(null)}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-border bg-surface-elevated text-xs text-neutral-300"
        >
          <RefreshCw className={`w-3.5 h-3.5 ${loading ? "animate-spin" : ""}`} />
          <span>Refresh</span>
        </button>
      </div>

      {error && (
        <div role="alert" className="flex items-center gap-2 p-3 rounded-lg border border-rose-500/30 bg-rose-500/10 text-xs text-rose-500">
          <AlertCircle className="w-4 h-4 shrink-0" />
          <span>{error}</span>
        </div>
      )}

      <Field label="Event type" className="max-w-xs">
        {(props) => (
          <Select {...props} value={typeFilter} onChange={(e) => setTypeFilter(e.target.value)} data-testid="audit-type-filter">
            <option value="">All events</option>
            {EVENT_TYPES.map((type) => (
              <option key={type} value={type}>
                {type}
              </option>
            ))}
          </Select>
        )}
      </Field>

      <div className="border border-border rounded-lg overflow-x-auto">
        <table className="w-full text-left text-xs" data-testid="audit-table">
          <thead className="bg-surface-subnav/80 border-b border-border text-muted-foreground">
            <tr>
              <th className="p-2.5">Time</th>
              <th className="p-2.5">Event</th>
              <th className="p-2.5">Actor</th>
              <th className="p-2.5">Target</th>
              <th className="p-2.5">Risk</th>
              <th className="p-2.5">Summary</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-border/60">
            {visible.map((event) => (
              <tr key={event.id}>
                <td className="p-2.5 whitespace-nowrap text-muted-foreground">
                  {new Date(event.created_at * 1000).toLocaleString()}
                </td>
                <td className="p-2.5 font-medium text-brand">{event.event_type}</td>
                <td className="p-2.5 font-mono">{event.actor_user_id || "—"}</td>
                <td className="p-2.5 font-mono">{event.tool_name || "—"}</td>
                <td className="p-2.5">{event.risk_level || "—"}</td>
                <td className="p-2.5 max-w-xs truncate" title={event.result_summary || undefined}>
                  {event.result_summary || "—"}
                  {event.redacted && <span className="ml-1 text-muted-foreground">({event.redaction_count} redacted)</span>}
                </td>
              </tr>
            ))}
            {visible.length === 0 && !loading && (
              <tr>
                <td colSpan={6} className="p-4 text-center text-muted-foreground">
                  No events recorded.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {cursor && (
        <button
          type="button"
          onClick={() => void load(cursor)}
          disabled={loading}
          className="px-3 py-1.5 rounded-lg border border-border text-xs text-foreground hover:bg-surface-elevated disabled:opacity-50"
        >
          Load older events
        </button>
      )}
    </div>
  );
}

"use client";

import React, { useCallback, useEffect, useState } from "react";
import { KeyRound } from "lucide-react";
import { Button } from "../../ui/Button";

interface Variable {
  name: string;
  has_default: boolean;
  overridden: boolean;
}

interface McpMemberEnvProps {
  serverId: string;
  /** Called after the member's values changed, so the list can show which are set. */
  onChanged: () => void;
}

/** Lets a member run an organization command server with their own values (for example their own API key). */
export const McpMemberEnv: React.FC<McpMemberEnvProps> = ({ serverId, onChanged }) => {
  const [variables, setVariables] = useState<Variable[]>([]);
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    const res = await fetch(`/v1/mcp/servers/${serverId}/env`);
    if (res.ok) setVariables((await res.json()).variables);
  }, [serverId]);

  useEffect(() => {
    void load();
  }, [load]);

  const save = async (values: Record<string, string>, done: string) => {
    setBusy(true);
    setError(null);
    setMessage(null);
    try {
      const res = await fetch(`/v1/mcp/servers/${serverId}/env`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ values }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        setError(typeof body.detail === "string" ? body.detail : `HTTP ${res.status}`);
        return;
      }
      setVariables((await res.json()).variables);
      setDrafts({});
      setMessage(done);
      onChanged();
    } finally {
      setBusy(false);
    }
  };

  if (variables.length === 0) return null;

  return (
    <div data-testid="member-env" className="space-y-2 rounded border border-border/60 bg-surface-card p-3">
      <div className="flex items-center gap-1.5 text-xs font-medium text-neutral-600 dark:text-neutral-300">
        <KeyRound className="h-3.5 w-3.5" aria-hidden="true" /> Your own values
      </div>
      <p className="text-[11px] text-neutral-500">
        This server runs with the organization&apos;s values unless you set your own. Yours apply only to your sessions and are
        never shown again.
      </p>
      {variables.map((variable) => (
        <div key={variable.name} className="grid grid-cols-1 items-center gap-2 sm:grid-cols-[12rem_1fr_auto]">
          <label htmlFor={`env-${serverId}-${variable.name}`} className="font-mono text-xs text-foreground">
            {variable.name}
          </label>
          <input
            id={`env-${serverId}-${variable.name}`}
            data-testid={`member-env-${variable.name}`}
            type="password"
            autoComplete="off"
            className="min-w-0 rounded border border-border bg-void px-2.5 py-1 font-mono text-xs"
            placeholder={
              variable.overridden
                ? "Set - type to replace"
                : variable.has_default
                  ? "Using the organization's value"
                  : "Required - the administrator left this to you"
            }
            value={drafts[variable.name] ?? ""}
            onChange={(e) => setDrafts({ ...drafts, [variable.name]: e.target.value })}
          />
          {variable.overridden && (
            <Button
              size="sm"
              variant="ghost"
              data-testid={`member-env-clear-${variable.name}`}
              disabled={busy}
              onClick={() => save({ [variable.name]: "" }, `Removed your ${variable.name}.`)}
            >
              Clear
            </Button>
          )}
        </div>
      ))}
      <div className="flex items-center gap-3">
        <Button
          size="sm"
          variant="primary"
          data-testid="member-env-save"
          disabled={busy || Object.values(drafts).every((v) => !v)}
          onClick={() => save(Object.fromEntries(Object.entries(drafts).filter(([, v]) => v)), "Saved.")}
        >
          Save my values
        </Button>
        <span aria-live="polite" className="text-xs">
          {message && <span className="text-neutral-600 dark:text-neutral-300">{message}</span>}
          {error && <span className="text-rose-600 dark:text-rose-400">{error}</span>}
        </span>
      </div>
    </div>
  );
};

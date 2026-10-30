"use client";

import React, { useState } from "react";
import Link from "next/link";
import { AlertTriangle, RotateCcw, Settings } from "lucide-react";
import type { TurnErrorInfo } from "../types/mission";
import { useShallow } from "zustand/react/shallow";
import { useMissionStore } from "../lib/store";
import { Field, Select } from "./ui/Field";

/** Where the fix for each kind of failure lives, when a setting can fix it. */
const SETTINGS_FOR: Record<string, { href: string; label: string }> = {
  provider: { href: "/settings/org/models", label: "Open model settings" },
  model: { href: "/settings/org/models", label: "Open model settings" },
  budget: { href: "/settings/org/spend", label: "Open spend limits" },
};

const HEADLINE: Record<string, string> = {
  provider: "The model provider refused or failed the request.",
  model: "This model is unknown or not allowed.",
  budget: "A spend limit stopped the turn.",
  access: "You do not have access to this conversation's agent.",
  sandbox: "The workspace failed.",
  internal: "The turn failed.",
};

/** A failed turn, where it happened, with the ways out: retry, retry on another model, or fix a setting. */
export const TurnErrorCard: React.FC<{
  error: TurnErrorInfo;
  onRetry?: (prompt: string, model?: string) => void;
}> = ({ error, onRetry }) => {
  const models = useMissionStore((state) => state.availableModels);
  const [model, setModel] = useState("");
  const settings = SETTINGS_FOR[error.kind];
  const canRetry = Boolean(onRetry && error.prompt);
  const { agents, selectAgent } = useMissionStore(
    useShallow((state) => ({ agents: state.availableAgents, selectAgent: state.selectAgent })),
  );
  const [agentId, setAgentId] = useState("");

  return (
    <div
      role="alert"
      data-testid="turn-error-card"
      data-error-kind={error.kind}
      className="rounded-xl border border-rose-500/50 bg-rose-500/5 px-4 py-3 text-sm"
    >
      <div className="flex items-center gap-2 font-semibold text-rose-700 dark:text-rose-300">
        <AlertTriangle className="h-4 w-4 shrink-0" aria-hidden="true" />
        {HEADLINE[error.kind] ?? HEADLINE.internal}
      </div>
      {error.limit ? (
        <p data-testid="turn-error-limit" className="mt-1.5 text-xs text-neutral-700 dark:text-neutral-300">
          ${error.limit.spent.toFixed(2)} of ${error.limit.amount.toFixed(2)} spent ({error.limit.subject},{" "}
          {error.limit.period.replace("_", " ")}).{" "}
          {error.limit.resets_at
            ? `It resets ${new Date(error.limit.resets_at * 1000).toLocaleString()}.`
            : "It does not reset: start another session or turn."}{" "}
          An administrator with the manage_spend_limits capability can raise it.
        </p>
      ) : (
        <p className="mt-1.5 max-h-32 overflow-auto whitespace-pre-wrap break-words font-mono text-xs text-neutral-700 dark:text-neutral-300">
          {error.message}
        </p>
      )}
      <div className="mt-3 flex flex-wrap items-end gap-2">
        {error.kind === "access" && (
          <>
            <Field label="Another agent" className="w-56">
              {(control) => (
                <Select {...control} data-testid="turn-error-agent" value={agentId} onChange={(e) => setAgentId(e.target.value)}>
                  <option value="">Choose an agent…</option>
                  {agents
                    .filter((agent) => agent.id !== error.agent?.id && agent.enabled !== false)
                    .map((agent) => (
                      <option key={agent.id} value={agent.id}>
                        {agent.name}
                      </option>
                    ))}
                </Select>
              )}
            </Field>
            <button
              type="button"
              data-testid="turn-error-retry-agent"
              disabled={!agentId}
              onClick={() => {
                const chosen = agents.find((agent) => agent.id === agentId);
                if (chosen) selectAgent(chosen);
                if (canRetry) onRetry?.(error.prompt as string);
              }}
              className="rounded-lg bg-brand px-3 py-1.5 text-xs font-semibold text-white hover:opacity-90 disabled:opacity-50"
            >
              Use this agent
            </button>
          </>
        )}
        {canRetry && error.kind !== "access" && (
          <button
            type="button"
            data-testid="turn-error-retry"
            onClick={() => onRetry?.(error.prompt as string)}
            className="flex items-center gap-1.5 rounded-lg bg-brand px-3 py-1.5 text-xs font-semibold text-white hover:opacity-90"
          >
            <RotateCcw className="h-3.5 w-3.5" aria-hidden="true" />
            Retry
          </button>
        )}
        {canRetry && models.length > 1 && (
          <>
            <Field label="Another model" className="w-56">
              {(control) => (
                <Select {...control} data-testid="turn-error-model" value={model} onChange={(e) => setModel(e.target.value)}>
                  <option value="">Choose a model…</option>
                  {models.map((option) => (
                    <option key={option.id} value={option.id}>
                      {option.name}
                    </option>
                  ))}
                </Select>
              )}
            </Field>
            <button
              type="button"
              data-testid="turn-error-retry-model"
              disabled={!model}
              onClick={() => onRetry?.(error.prompt as string, model)}
              className="rounded-lg border border-surface-border bg-surface-elevated px-3 py-1.5 text-xs font-semibold text-foreground hover:bg-surface-border disabled:cursor-not-allowed disabled:opacity-50"
            >
              Retry with this model
            </button>
          </>
        )}
        {settings && (
          <Link
            href={settings.href}
            data-testid="turn-error-settings"
            className="flex items-center gap-1.5 rounded-lg border border-surface-border px-3 py-1.5 text-xs font-semibold text-foreground hover:bg-surface-elevated"
          >
            <Settings className="h-3.5 w-3.5" aria-hidden="true" />
            {settings.label}
          </Link>
        )}
      </div>
    </div>
  );
};

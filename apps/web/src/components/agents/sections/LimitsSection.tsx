"use client";

import React, { useEffect, useState } from "react";
import Link from "next/link";
import { Lock } from "lucide-react";
import { Field, Input, Select } from "../../ui/Field";
import type { PermissionMode } from "../../../types/mission";
import type { SpendLimit } from "../../settings/SpendLimitsPanel";
import type { SectionProps } from "./types";
import { SubagentLimits } from "./SubagentFields";

const MODES: { value: PermissionMode | ""; label: string }[] = [
  { value: "", label: "Member / organization default" },
  { value: "plan", label: "Plan (read-only)" },
  { value: "ask", label: "Ask (approve every action)" },
  { value: "auto_edit", label: "Auto-edit (approve commands and publishing)" },
  { value: "auto", label: "Auto (only irreversible actions wait)" },
  { value: "bypass", label: "Bypass (nothing waits)" },
];

/** Steps per turn (under the organization's ceiling), default permission mode and spend. */
export const LimitsSection: React.FC<SectionProps> = (props) => {
  const { draft, patch, canEdit, isAdmin, registries } = props;
  const [modeLocked, setModeLocked] = useState<string | null>(null);
  const [limits, setLimits] = useState<SpendLimit[]>([]);
  const ceiling = registries.orgMaxTurns;

  useEffect(() => {
    fetch("/v1/settings/user_preferences")
      .then((res) => (res.ok ? res.json() : null))
      .then((body: { locked_keys?: string[]; effective?: { permission_mode?: string } } | null) =>
        setModeLocked(body?.locked_keys?.includes("permission_mode") ? (body.effective?.permission_mode ?? "") : null),
      )
      .catch(() => undefined);
    if (isAdmin && draft.kind === "agent") {
      fetch("/v1/admin/spend-limits")
        .then((res) => (res.ok ? res.json() : []))
        .then((all: SpendLimit[]) => setLimits(all.filter((l) => l.subject_id === draft.id)))
        .catch(() => undefined);
    }
  }, [isAdmin, draft.id, draft.kind]);

  return (
    <div className="space-y-4">
      <Field
        label="Max steps per turn"
        hint={
          ceiling === 0
            ? "Empty or 0: no limit (the organization sets none)."
            : ceiling !== null
              ? `Empty uses the organization's limit (${ceiling}); it may be lower, never higher.`
              : "Empty uses the organization's limit."
        }
      >
        {(control) => (
          <Input
            {...control}
            data-testid="agent-max-turns"
            type="number"
            min={0}
            max={ceiling || undefined}
            disabled={!canEdit}
            value={draft.max_turns ?? ""}
            onChange={(e) => patch({ max_turns: e.target.value === "" ? null : Number(e.target.value) })}
            placeholder={ceiling ? String(ceiling) : "Organization limit"}
            className="max-w-[10rem]"
          />
        )}
      </Field>

      {draft.kind === "agent" && (
        <Field
          label="Default permission mode"
          hint={modeLocked !== null ? undefined : "How much this agent's sessions wait for a person, unless the session chooses."}
        >
          {(control) =>
            modeLocked !== null ? (
              <span className="inline-flex items-center gap-1 rounded-full border border-border px-2 py-1 text-[11px] text-muted-foreground">
                <Lock className="h-3 w-3" aria-hidden="true" /> Locked by admin: {modeLocked || "default"}
              </span>
            ) : (
              <Select
                {...control}
                data-testid="agent-permission-mode"
                disabled={!canEdit}
                value={draft.default_permission_mode}
                onChange={(e) => patch({ default_permission_mode: e.target.value as PermissionMode | "" })}
                className="max-w-sm"
              >
                {MODES.map((mode) => (
                  <option key={mode.value} value={mode.value}>
                    {mode.label}
                  </option>
                ))}
              </Select>
            )
          }
        </Field>
      )}

      {draft.kind === "subagent" && <SubagentLimits {...props} />}

      {draft.kind === "agent" && isAdmin && (
        <section aria-label="Spend limits of this agent" className="space-y-1">
          <h4 className="text-xs font-semibold text-foreground">Spend limits</h4>
          {limits.length === 0 ? (
            <p className="text-xs text-neutral-500">None of its own; the organization&apos;s and members&apos; limits apply.</p>
          ) : (
            <ul className="space-y-0.5 text-xs">
              {limits.map((limit) => (
                <li key={limit.id} className="text-foreground">
                  {limit.label}: ${limit.amount_usd.toFixed(2)} ({limit.mode})
                </li>
              ))}
            </ul>
          )}
          <Link href="/settings/org/spend" className="text-xs text-brand hover:underline">
            Manage spend limits
          </Link>
        </section>
      )}
    </div>
  );
};

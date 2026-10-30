"use client";

import React from "react";
import { Field, Input, Select, Textarea } from "../../ui/Field";
import type { SubagentInherit, SubagentIsolation, SubagentTier } from "../../../types/mission";
import { DEFAULT_SUBAGENT_FIELDS, parsedOutputSchema, type SubagentFields } from "../draft";
import type { SectionProps } from "./types";

const TIERS: { value: SubagentTier; label: string }[] = [
  { value: "fast", label: "Fast (the organization's fast model)" },
  { value: "default", label: "Default (what routing gives subagents)" },
  { value: "frontier", label: "Frontier (the session's own model)" },
];
const ISOLATIONS: { value: SubagentIsolation; label: string }[] = [
  { value: "none", label: "Shared tree (reads, or small edits in place)" },
  { value: "worktree", label: "Own worktree (changes come back as a branch to merge)" },
  { value: "sandbox", label: "Own sandbox (heavy builds; changes come back as a branch)" },
];
const INHERITABLE: { value: SubagentInherit; label: string }[] = [
  { value: "agents_md", label: "The repository's AGENTS.md" },
  { value: "memory", label: "Memories relevant to its task" },
  { value: "checklist", label: "The main agent's checklist (read-only)" },
  { value: "skills", label: "Skills the main agent names" },
];

/** The tier a role runs on when it names no model. */
export const SubagentTierField: React.FC<SectionProps> = ({ draft, patch, canEdit }) => {
  const fields = draft.subagent ?? DEFAULT_SUBAGENT_FIELDS;
  return (
    <Field label="Tier" hint="Used when no model is set: a provider-independent choice of cost and capability.">
      {(control) => (
        <Select
          {...control}
          data-testid="subagent-tier"
          disabled={!canEdit}
          value={fields.tier}
          onChange={(e) => patch({ subagent: { ...fields, tier: e.target.value as SubagentTier } })}
          className="max-w-sm"
        >
          {TIERS.map((tier) => (
            <option key={tier.value} value={tier.value}>
              {tier.label}
            </option>
          ))}
        </Select>
      )}
    </Field>
  );
};

/** Isolation, budgets, whether it may ask the person, what it inherits, and its output schema. */
export const SubagentLimits: React.FC<SectionProps> = ({ draft, patch, canEdit }) => {
  const fields = draft.subagent ?? DEFAULT_SUBAGENT_FIELDS;
  const set = (changes: Partial<SubagentFields>) => patch({ subagent: { ...fields, ...changes } });
  const schemaInvalid = fields.output_schema.trim() !== "" && parsedOutputSchema(fields.output_schema) === null;
  return (
    <div className="space-y-4">
      <Field label="Isolation" hint="Roles that edit or run commands should work in a tree of their own.">
        {(control) => (
          <Select
            {...control}
            data-testid="subagent-isolation"
            disabled={!canEdit}
            value={fields.isolation}
            onChange={(e) => set({ isolation: e.target.value as SubagentIsolation })}
            className="max-w-md"
          >
            {ISOLATIONS.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </Select>
        )}
      </Field>
      <div className="flex flex-wrap gap-4">
        <Field label="Spend cap per run (USD)" hint="0: no cap of its own; the turn's still applies.">
          {(control) => (
            <Input
              {...control}
              data-testid="subagent-max-cost"
              type="number"
              min={0}
              step={0.5}
              disabled={!canEdit}
              value={fields.max_cost_usd}
              onChange={(e) => set({ max_cost_usd: Math.max(0, Number(e.target.value) || 0) })}
              className="max-w-[10rem]"
            />
          )}
        </Field>
        <Field label="Time cap per run (seconds)" hint="0: none.">
          {(control) => (
            <Input
              {...control}
              data-testid="subagent-max-wall"
              type="number"
              min={0}
              disabled={!canEdit}
              value={fields.max_wall_seconds}
              onChange={(e) => set({ max_wall_seconds: Math.max(0, Math.floor(Number(e.target.value) || 0)) })}
              className="max-w-[10rem]"
            />
          )}
        </Field>
      </div>
      <label className="flex items-center gap-2 text-sm text-foreground">
        <input
          type="checkbox"
          data-testid="subagent-can-ask"
          disabled={!canEdit}
          checked={fields.can_ask_user}
          onChange={(e) => set({ can_ask_user: e.target.checked })}
        />
        May ask the person (approvals and questions); otherwise they are refused and it reports instead
      </label>
      <fieldset className="space-y-1">
        <legend className="text-xs font-semibold text-foreground">Starts with</legend>
        {INHERITABLE.map((option) => (
          <label key={option.value} className="flex items-center gap-2 text-sm text-foreground">
            <input
              type="checkbox"
              disabled={!canEdit}
              checked={fields.inherit.includes(option.value)}
              onChange={(e) =>
                set({
                  inherit: e.target.checked
                    ? [...fields.inherit, option.value]
                    : fields.inherit.filter((value) => value !== option.value),
                })
              }
            />
            {option.label}
          </label>
        ))}
      </fieldset>
      <Field
        label="Output schema (JSON Schema, optional)"
        hint="The `data` its report must carry, checked before the report is accepted."
        error={schemaInvalid ? "Not a JSON object." : undefined}
      >
        {(control) => (
          <Textarea
            {...control}
            data-testid="subagent-output-schema"
            disabled={!canEdit}
            rows={4}
            value={fields.output_schema}
            onChange={(e) => set({ output_schema: e.target.value })}
            className="font-mono text-xs"
          />
        )}
      </Field>
    </div>
  );
};

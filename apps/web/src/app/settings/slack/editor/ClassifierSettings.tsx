"use client";

import React from "react";
import { Field, Input, Select } from "../../../../components/ui/Field";
import { ModelPicker } from "../../../../components/ui/ModelPicker";
import type { ClassifierSettings as Settings, RoutingRow } from "./routing";

interface ClassifierSettingsProps {
  value: Settings;
  rows: RoutingRow[];
  agentOptions: { value: string; label: string }[];
  onChange: (next: Settings) => void;
}

const CHECKBOX = "rounded border-border text-brand focus:ring-brand";

/** How the bot recognises the intents of the routing table, and what it does when unsure. */
export const ClassifierSettings: React.FC<ClassifierSettingsProps> = ({ value, rows, agentOptions, onChange }) => {
  const patch = (changes: Partial<Settings>) => onChange({ ...value, ...changes });
  const intents = rows.filter((row) => row.kind === "intent" && row.match.trim());
  return (
    <fieldset className="space-y-4 rounded-lg border border-border bg-surface-subnav/20 p-4" data-testid="classifier-settings">
      <legend className="px-1 text-xs font-semibold text-foreground">Recognising intents</legend>
      <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
        <Field label="Classifier agent" hint="Provides the classifier's defaults and context. Empty: the default row's agent.">
          {(props) => (
            <Select {...props} data-testid="classifier-agent-select" value={value.agent_id} onChange={(e) => patch({ agent_id: e.target.value })}>
              <option value="">(Default row&apos;s agent)</option>
              {agentOptions.map((opt) => (
                <option key={opt.value} value={opt.value}>
                  {opt.label}
                </option>
              ))}
            </Select>
          )}
        </Field>
        <Field label="Classifier model" hint="Empty uses the classifier agent's model. OpenRouter System One models return a probability per intent.">
          {(props) => (
            <ModelPicker {...props} data-testid="classifier-model" value={value.model} onChange={(model) => patch({ model })} inheritLabel="Classifier agent's model" />
          )}
        </Field>
        <Field label="Minimum confidence (0.0 to 1.0)">
          {(props) => (
            <Input
              {...props}
              type="number"
              step="0.05"
              min="0"
              max="1"
              value={value.min_confidence}
              onChange={(e) => patch({ min_confidence: Number(e.target.value) })}
            />
          )}
        </Field>
        <Field label="When unsure" hint="Used when the confidence is below the minimum.">
          {(props) => (
            <Select {...props} value={value.fallback} onChange={(e) => patch({ fallback: e.target.value })}>
              <option value="">The default row</option>
              {intents.map((row) => (
                <option key={row.key} value={row.match.trim()}>
                  Intent: {row.match.trim()}
                </option>
              ))}
              {value.allow_dismiss && <option value="dismiss">Dismiss</option>}
            </Select>
          )}
        </Field>
      </div>
      <label className="flex cursor-pointer items-center gap-2 text-xs text-foreground">
        <input type="checkbox" checked={value.allow_dismiss} onChange={(e) => patch({ allow_dismiss: e.target.checked })} className={CHECKBOX} />
        <span>Allow dismissal (no session created)</span>
      </label>
      {value.allow_dismiss && (
        <Field label="Dismissal criteria">
          {(props) => (
            <Input
              {...props}
              value={value.dismiss_description}
              onChange={(e) => patch({ dismiss_description: e.target.value })}
              placeholder="Small talk, casual greetings, or out-of-scope messages"
            />
          )}
        </Field>
      )}
      <label className="flex cursor-pointer items-center gap-2 text-xs text-foreground">
        <input type="checkbox" checked={value.sticky} onChange={(e) => patch({ sticky: e.target.checked })} className={CHECKBOX} />
        <span>Sticky (follow-ups in a thread keep its intent)</span>
      </label>
    </fieldset>
  );
};

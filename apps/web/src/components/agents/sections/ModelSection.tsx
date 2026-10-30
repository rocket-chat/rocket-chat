"use client";

import React, { useState } from "react";
import { ChevronRight, KeyRound, Trash2 } from "lucide-react";
import { Field, Input, Select } from "../../ui/Field";
import { ModelPicker } from "../../ui/ModelPicker";
import type { ReasoningEffort } from "../draft";
import type { SectionProps } from "./types";
import { SubagentTierField } from "./SubagentFields";

const EFFORTS: { value: ReasoningEffort | ""; label: string }[] = [
  { value: "", label: "Organization default" },
  { value: "none", label: "Off (fastest)" },
  { value: "minimal", label: "Minimal" },
  { value: "low", label: "Low" },
  { value: "medium", label: "Medium" },
  { value: "high", label: "High" },
  { value: "xhigh", label: "Extra high" },
  { value: "max", label: "Max" },
];

/** The model, one Reasoning control, the agent's own credentials, and (advanced) temperature. */
export const ModelSection: React.FC<SectionProps> = (props) => {
  const { draft, patch, canEdit, isAdmin, registries } = props;
  const [advanced, setAdvanced] = useState(false);
  const [provider, setProvider] = useState("");
  const [credentialId, setCredentialId] = useState("");
  // Only administrators bind organization agents; a personal agent binds its owner's keys.
  const mayBind = draft.kind === "agent" && canEdit && (draft.scope === "user" || isAdmin);
  const bindable = registries.credentials.filter(
    (c) =>
      (draft.scope === "user" ? c.owner === "user" : c.owner === "org" || c.shared_with_org_agents.includes(draft.id)) &&
      (!provider || c.provider === provider),
  );
  const providers = Array.from(new Set(registries.credentials.map((c) => c.provider))).sort();
  const labelOf = (id: string) => registries.credentials.find((c) => c.id === id)?.label ?? id;

  const bind = () => {
    const chosen = registries.credentials.find((c) => c.id === credentialId);
    if (!chosen) return;
    const others = draft.credential_bindings.filter((b) => b.provider !== chosen.provider);
    patch({ credential_bindings: [...others, { provider: chosen.provider, credential_id: chosen.id }] });
    setCredentialId("");
  };

  return (
    <div className="space-y-4">
      <Field label="Model" hint="Empty uses the member's preferred model, else the organization's default.">
        {(control) => (
          <ModelPicker
            {...control}
            data-testid="agent-model"
            disabled={!canEdit || (draft.kind === "agent" && draft.scope === "org" && !isAdmin)}
            value={draft.model}
            onChange={(model) => patch({ model })}
            inheritLabel="Organization default"
          />
        )}
      </Field>
      {draft.kind === "subagent" && <SubagentTierField {...props} />}
      <Field
        label="Reasoning"
        hint="How much the model thinks before it acts. Claude's thinking budget follows this level."
      >
        {(control) => (
          <Select
            {...control}
            data-testid="agent-reasoning-effort"
            disabled={!canEdit}
            value={draft.reasoning_effort}
            onChange={(e) => patch({ reasoning_effort: e.target.value as ReasoningEffort | "" })}
            className="max-w-xs"
          >
            {EFFORTS.map((effort) => (
              <option key={effort.value} value={effort.value}>
                {effort.label}
              </option>
            ))}
          </Select>
        )}
      </Field>

      {draft.kind === "agent" && (
        <fieldset className="space-y-3 rounded-xl border border-surface-border p-4" data-testid="agent-credentials">
          <legend className="flex items-center gap-1.5 px-1 text-xs font-semibold text-foreground">
            <KeyRound className="h-3.5 w-3.5 text-brand" aria-hidden="true" /> Credentials
          </legend>
          <p className="text-[11px] text-neutral-600 dark:text-neutral-400">
            A call on a bound provider uses the bound key; other calls use the usual chain unless the binding is strict.
          </p>
          {draft.credential_bindings.length === 0 && <p className="text-xs text-neutral-500">No binding: the usual keys.</p>}
          <ul className="space-y-1">
            {draft.credential_bindings.map((binding) => (
              <li key={binding.provider} className="flex items-center gap-2 text-xs" data-testid={`agent-binding-${binding.provider}`}>
                <span className="rounded border border-surface-border px-1.5 py-0.5 text-[10px]">{binding.provider}</span>
                <span className="text-foreground">{labelOf(binding.credential_id)}</span>
                {mayBind && (
                  <button
                    type="button"
                    aria-label={`Unbind the ${binding.provider} credential`}
                    onClick={() =>
                      patch({ credential_bindings: draft.credential_bindings.filter((b) => b.provider !== binding.provider) })
                    }
                    className="p-1 text-neutral-500 hover:text-rose-500"
                  >
                    <Trash2 className="h-3.5 w-3.5" aria-hidden="true" />
                  </button>
                )}
              </li>
            ))}
          </ul>
          {mayBind && (
            <div className="flex flex-wrap items-end gap-2">
              <Field label="Provider">
                {(control) => (
                  <Select {...control} value={provider} onChange={(e) => setProvider(e.target.value)}>
                    <option value="">Any</option>
                    {providers.map((p) => (
                      <option key={p} value={p}>
                        {p}
                      </option>
                    ))}
                  </Select>
                )}
              </Field>
              <Field label="Credential">
                {(control) => (
                  <Select {...control} data-testid="agent-bind-credential" value={credentialId} onChange={(e) => setCredentialId(e.target.value)}>
                    <option value="">Choose a credential</option>
                    {bindable.map((c) => (
                      <option key={c.id} value={c.id}>
                        {c.label} ({c.provider}
                        {c.restricted_to_agents ? ", bound agents only" : ""})
                      </option>
                    ))}
                  </Select>
                )}
              </Field>
              <button
                type="button"
                disabled={!credentialId}
                onClick={bind}
                data-testid="agent-bind"
                className="rounded-lg border border-surface-border px-3 py-2 text-xs text-foreground hover:bg-surface-elevated disabled:opacity-50"
              >
                Bind
              </button>
            </div>
          )}
          <label className="flex items-center gap-2 text-xs text-foreground">
            <input
              type="checkbox"
              data-testid="agent-strict-binding"
              disabled={!mayBind}
              checked={draft.strict_binding}
              onChange={(e) => patch({ strict_binding: e.target.checked })}
            />
            Strict: refuse any call on a provider without a binding (never billed elsewhere)
          </label>
        </fieldset>
      )}

      <button
        type="button"
        aria-expanded={advanced}
        onClick={() => setAdvanced((value) => !value)}
        className="flex items-center gap-1 text-xs text-neutral-500 hover:text-foreground"
      >
        <ChevronRight className={`h-3.5 w-3.5 transition-transform ${advanced ? "rotate-90" : ""}`} aria-hidden="true" />
        Advanced
      </button>
      {advanced && (
        <Field label="Sampling temperature" hint="Empty uses the organization's value; reasoning models take the provider's own.">
          {(control) => (
            <Input
              {...control}
              data-testid="agent-temperature"
              type="number"
              min={0}
              max={2}
              step={0.1}
              disabled={!canEdit}
              value={draft.temperature ?? ""}
              onChange={(e) => patch({ temperature: e.target.value === "" ? null : Number(e.target.value) })}
              className="max-w-[10rem]"
            />
          )}
        </Field>
      )}
    </div>
  );
};

"use client";

import React from "react";
import { Plus, Trash2 } from "lucide-react";
import { Button } from "../ui/Button";

export interface SandboxEnvRow {
  name: string;
  value: string;
  secret: boolean;
  /** Editing: a secret value is stored (never shown); leaving the field empty keeps it. */
  stored?: boolean;
}

/** What the API returns for a variable: a secret's value never comes back. */
export interface SandboxEnvVar {
  name: string;
  value: string | null;
  secret: boolean;
}

export const rowsOf = (env: SandboxEnvVar[] | undefined): SandboxEnvRow[] =>
  (env ?? []).map((v) => ({ name: v.name, value: v.secret ? "" : (v.value ?? ""), secret: v.secret, stored: v.secret }));

export const envPayload = (rows: SandboxEnvRow[]) =>
  rows
    .filter((row) => row.name.trim())
    .map((row) => ({
      name: row.name.trim(),
      // An untouched stored secret is sent empty: the server keeps what it has.
      value: row.secret && row.stored && !row.value ? null : row.value,
      secret: row.secret,
    }));

const FIELD =
  "min-w-0 rounded-lg border border-surface-border bg-surface-subnav px-2.5 py-1.5 text-xs text-foreground focus:border-brand focus:outline-none";

interface SandboxEnvEditorProps {
  rows: SandboxEnvRow[];
  onChange: (rows: SandboxEnvRow[]) => void;
}

/** Variables every command of the type's sessions gets; a secret is write-only. */
export const SandboxEnvEditor: React.FC<SandboxEnvEditorProps> = ({ rows, onChange }) => {
  const update = (index: number, patch: Partial<SandboxEnvRow>) =>
    onChange(rows.map((row, i) => (i === index ? { ...row, ...patch } : row)));

  return (
    <fieldset data-testid="sandbox-env" className="md:col-span-2 space-y-2 rounded-lg border border-surface-border p-3">
      <legend className="px-1 text-xs font-medium text-foreground">Environment variables</legend>
      <p className="text-[11px] text-neutral-500 dark:text-neutral-400">
        Set for every command of this type&apos;s sessions, for example <code>GOPRIVATE</code>. A secret is stored
        encrypted, never shown again and never given to the model; the agent only learns its name.
      </p>
      {rows.map((row, index) => (
        <div key={index} data-testid="sandbox-env-row" className="grid grid-cols-1 items-center gap-2 sm:grid-cols-[1fr_1.4fr_auto_auto]">
          <input
            aria-label={`Variable ${index + 1} name`}
            className={FIELD}
            placeholder="NAME (e.g. GOPRIVATE)"
            value={row.name}
            onChange={(e) => update(index, { name: e.target.value })}
          />
          <input
            aria-label={`Variable ${index + 1} value`}
            className={FIELD}
            type={row.secret ? "password" : "text"}
            placeholder={row.stored ? "Unchanged (type to replace)" : "Value"}
            autoComplete="off"
            value={row.value}
            onChange={(e) => update(index, { value: e.target.value })}
          />
          <label className="flex cursor-pointer items-center gap-1 text-[11px] text-neutral-600 dark:text-neutral-300">
            <input
              type="checkbox"
              checked={row.secret}
              onChange={(e) => update(index, { secret: e.target.checked, stored: false })}
            />
            Secret
          </label>
          <button
            type="button"
            aria-label={`Remove variable ${index + 1}`}
            onClick={() => onChange(rows.filter((_, i) => i !== index))}
            className="justify-self-end text-neutral-500 hover:text-diff-del"
          >
            <Trash2 className="h-3.5 w-3.5" />
          </button>
        </div>
      ))}
      <Button
        size="sm"
        data-testid="sandbox-env-add"
        onClick={() => onChange([...rows, { name: "", value: "", secret: false }])}
      >
        <Plus className="h-3.5 w-3.5" aria-hidden="true" /> Add variable
      </Button>
    </fieldset>
  );
};

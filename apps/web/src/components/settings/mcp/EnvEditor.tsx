"use client";

import React from "react";
import { Plus, Trash2 } from "lucide-react";
import { Button } from "../../ui/Button";

export interface EnvRow {
  name: string;
  value: string;
  is_secret: boolean;
  /** Each member may set their own value; an empty value then means they must. */
  member_override: boolean;
}

interface EnvEditorProps {
  rows: EnvRow[];
  onChange: (rows: EnvRow[]) => void;
}

const FIELD =
  "min-w-0 rounded border border-border bg-void px-2.5 py-1 font-mono text-xs text-foreground focus:border-cyan focus:outline-none";

/** The environment variables of a command server, one editable row each. */
export const EnvEditor: React.FC<EnvEditorProps> = ({ rows, onChange }) => {
  const update = (index: number, patch: Partial<EnvRow>) =>
    onChange(rows.map((row, i) => (i === index ? { ...row, ...patch } : row)));

  return (
    <div data-testid="env-editor" className="space-y-2 rounded border border-border bg-surface-card p-3">
      <div className="text-xs font-medium text-neutral-600 dark:text-neutral-300">Environment variables</div>
      <p className="text-[11px] text-neutral-500">
        Tick <em>Members can set their own</em> for a credential each person should provide themselves (for example
        their own API key). Leave its value empty to require it; fill it in to give members a default.
      </p>
      {rows.map((row, index) => (
        <div key={index} data-testid="env-row" className="grid grid-cols-1 items-center gap-2 sm:grid-cols-[1fr_1.4fr_auto_auto_auto]">
          <input
            aria-label={`Variable ${index + 1} name`}
            className={FIELD}
            placeholder="NAME (e.g. API_KEY)"
            value={row.name}
            onChange={(e) => update(index, { name: e.target.value })}
          />
          <input
            aria-label={`Variable ${index + 1} value`}
            className={FIELD}
            type={row.is_secret ? "password" : "text"}
            placeholder={row.member_override ? "Empty: each member provides it" : "Value"}
            autoComplete="off"
            value={row.value}
            onChange={(e) => update(index, { value: e.target.value })}
          />
          <label className="flex cursor-pointer items-center gap-1 text-[11px] text-neutral-600 dark:text-neutral-300">
            <input type="checkbox" checked={row.is_secret} onChange={(e) => update(index, { is_secret: e.target.checked })} />
            Secret
          </label>
          <label className="flex cursor-pointer items-center gap-1 text-[11px] text-neutral-600 dark:text-neutral-300">
            <input
              type="checkbox"
              checked={row.member_override}
              onChange={(e) => update(index, { member_override: e.target.checked })}
            />
            Members can set their own
          </label>
          <button
            type="button"
            aria-label={`Remove variable ${index + 1}`}
            onClick={() => onChange(rows.filter((_, i) => i !== index))}
            className="justify-self-end text-neutral-500 hover:text-rose-400"
          >
            <Trash2 className="h-3.5 w-3.5" />
          </button>
        </div>
      ))}
      <Button
        size="sm"
        data-testid="env-add"
        onClick={() => onChange([...rows, { name: "", value: "", is_secret: true, member_override: false }])}
      >
        <Plus className="h-3.5 w-3.5" aria-hidden="true" /> Add variable
      </Button>
    </div>
  );
};

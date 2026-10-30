"use client";

import React, { useState } from "react";
import { AlertTriangle, Edit3, Eye } from "lucide-react";
import { Field, Input, Select } from "../../ui/Field";
import { MarkdownRenderer } from "../../MarkdownRenderer";
import { usePersonaLint } from "../../../lib/usePersonaLint";
import type { SectionProps } from "./types";

const ICONS = ["bot", "server", "shield", "bug", "book", "search-check", "compass"];

/** Name, description (what auto-delegation reads), icon, and the prompt with its warnings. */
export const IdentitySection: React.FC<SectionProps> = ({ draft, patch, canEdit }) => {
  const [preview, setPreview] = useState(false);
  const warnings = usePersonaLint(draft.system_prompt);
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <Field label="Name">
          {(control) => (
            <Input {...control} data-testid="agent-name" disabled={!canEdit} value={draft.name} onChange={(e) => patch({ name: e.target.value })} />
          )}
        </Field>
        <Field label="Role title">
          {(control) => (
            <Input {...control} disabled={!canEdit} value={draft.role_title} onChange={(e) => patch({ role_title: e.target.value })} />
          )}
        </Field>
      </div>
      <Field
        label="Description"
        hint={
          draft.kind === "subagent"
            ? "When to delegate to this role, and when not to: the agent reads it to choose."
            : "What this agent is for, shown in the agent picker."
        }
      >
        {(control) => (
          <Input {...control} disabled={!canEdit} value={draft.description} onChange={(e) => patch({ description: e.target.value })} />
        )}
      </Field>
      {draft.kind === "agent" && (
        <Field label="Icon">
          {(control) => (
            <Select {...control} disabled={!canEdit} value={draft.icon} onChange={(e) => patch({ icon: e.target.value })} className="max-w-[10rem]">
              {ICONS.map((icon) => (
                <option key={icon} value={icon}>
                  {icon}
                </option>
              ))}
            </Select>
          )}
        </Field>
      )}
      <div>
        <div className="mb-1 flex items-center justify-between">
          <label htmlFor="agent-prompt" className="text-xs font-medium text-foreground">
            Prompt
          </label>
          <div className="flex items-center gap-1" role="group" aria-label="Prompt view">
            <button
              type="button"
              aria-pressed={!preview}
              onClick={() => setPreview(false)}
              className={`flex items-center gap-1 rounded px-2 py-0.5 text-[10px] ${!preview ? "bg-surface-elevated text-foreground" : "text-neutral-500"}`}
            >
              <Edit3 className="h-3 w-3" aria-hidden="true" /> Edit
            </button>
            <button
              type="button"
              aria-pressed={preview}
              onClick={() => setPreview(true)}
              className={`flex items-center gap-1 rounded px-2 py-0.5 text-[10px] ${preview ? "bg-surface-elevated text-foreground" : "text-neutral-500"}`}
            >
              <Eye className="h-3 w-3" aria-hidden="true" /> Preview
            </button>
          </div>
        </div>
        {preview ? (
          <div data-testid="markdown-preview-box" className="max-h-80 min-h-[160px] overflow-y-auto rounded-lg border border-border p-3">
            <MarkdownRenderer content={draft.system_prompt} />
          </div>
        ) : (
          <textarea
            id="agent-prompt"
            data-testid="agent-prompt"
            rows={10}
            disabled={!canEdit}
            value={draft.system_prompt}
            onChange={(e) => patch({ system_prompt: e.target.value })}
            className="w-full rounded-lg border border-border bg-surface-subnav px-3 py-2 font-mono text-xs leading-relaxed text-foreground focus:border-brand focus:outline-none disabled:opacity-60"
          />
        )}
        {warnings.length > 0 && (
          <ul data-testid="persona-warnings" role="status" className="mt-2 space-y-1">
            {warnings.map((warning) => (
              <li key={warning.message} data-testid={`persona-warning-${warning.kind}`} className="flex items-start gap-1.5 text-xs text-amber-700 dark:text-amber-300">
                <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
                {warning.message}
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
};

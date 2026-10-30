"use client";

import React, { useRef, useState } from "react";
import { tabKeyHandler } from "../../lib/tab-keys";
import { AlertCircle, CheckCircle2, Copy, Download, RotateCcw, Save, Upload } from "lucide-react";
import { useShallow } from "zustand/react/shallow";
import { useMissionStore } from "../../lib/store";
import { fromMarkdown, toMarkdown, type AgentDraft } from "./draft";
import type { AgentRegistries } from "./useAgentRegistries";
import { IdentitySection } from "./sections/IdentitySection";
import { ModelSection } from "./sections/ModelSection";
import { ToolsSection } from "./sections/ToolsSection";
import { SandboxSection } from "./sections/SandboxSection";
import { AccessSection } from "./sections/AccessSection";
import { LimitsSection } from "./sections/LimitsSection";
import { TestRunButton } from "./TestRunButton";

const SECTIONS = [
  { key: "identity", label: "Identity & prompt", Component: IdentitySection },
  { key: "model", label: "Model & credentials", Component: ModelSection },
  { key: "tools", label: "Tools & knowledge", Component: ToolsSection },
  { key: "sandbox", label: "Sandbox", Component: SandboxSection, agentOnly: true },
  { key: "access", label: "Access", Component: AccessSection, orgAgentOnly: true },
  { key: "limits", label: "Limits", Component: LimitsSection },
] as const;

interface AgentEditorProps {
  canEdit: boolean;
  isAdmin: boolean;
  /** The open draft is stored on the server (not a new, unsaved agent). */
  stored: boolean;
  registries: AgentRegistries;
  onSave: (draft: AgentDraft) => Promise<string | null>;
  onDuplicate: (draft: AgentDraft) => void;
  onReset?: (draft: AgentDraft) => Promise<void>;
}

/** One editor for agents and subagent roles: sections on the left, the section on the right. */
export const AgentEditor: React.FC<AgentEditorProps> = ({ canEdit, isAdmin, stored, registries, onSave, onDuplicate, onReset }) => {
  const { draft, saved, patch, open } = useMissionStore(
    useShallow((s) => ({
      draft: s.agentDraft,
      saved: s.agentDraftSaved,
      patch: s.patchAgentDraft,
      open: s.openAgentDraft,
    })),
  );
  const [section, setSection] = useState<string>("identity");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [savedAt, setSavedAt] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);

  if (!draft) return <p className="text-xs text-neutral-500">Choose an agent to edit.</p>;
  const dirty = JSON.stringify(draft) !== JSON.stringify(saved);
  const sections = SECTIONS.filter(
    (s) =>
      !("agentOnly" in s && s.agentOnly && draft.kind !== "agent") &&
      !("orgAgentOnly" in s && s.orgAgentOnly && (draft.kind !== "agent" || draft.scope !== "org")),
  );
  const current = sections.find((s) => s.key === section) ?? sections[0];

  const save = async () => {
    setBusy(true);
    setError(null);
    const problem = await onSave(draft);
    setBusy(false);
    if (problem) {
      setError(problem);
      return;
    }
    setSavedAt(true);
    setTimeout(() => setSavedAt(false), 2500);
  };

  const exportFile = () => {
    const blob = new Blob([toMarkdown(draft)], { type: "text/markdown" });
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = `${draft.id}.md`;
    link.click();
    URL.revokeObjectURL(link.href);
  };

  const importFile = async (file: File | undefined) => {
    if (!file) return;
    open({ ...fromMarkdown(await file.text(), draft) });
  };

  const Component = current.Component;
  return (
    <div className="space-y-4" data-testid="agent-editor">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border/60 pb-3">
        <div>
          <h2 className="text-sm font-bold text-foreground" data-testid="agent-editor-title">
            {draft.name}
          </h2>
          <span className="text-[10px] text-muted-foreground">
            {draft.kind === "subagent" ? "Subagent role" : draft.scope === "user" ? "Personal agent" : "Organization agent"} · {draft.id}
          </span>
        </div>
        <div className="flex flex-wrap items-center gap-1.5">
          <label className="flex items-center gap-1.5 text-xs text-foreground">
            <input type="checkbox" data-testid="agent-enabled" disabled={!canEdit} checked={draft.enabled} onChange={(e) => patch({ enabled: e.target.checked })} />
            Enabled
          </label>
          <TestRunButton draft={draft} ready={stored && !dirty} />
          <button type="button" onClick={() => onDuplicate(draft)} className="inline-flex items-center gap-1 rounded-lg border border-border px-2 py-1.5 text-xs text-foreground hover:bg-surface-elevated">
            <Copy className="h-3.5 w-3.5" aria-hidden="true" /> Duplicate
          </button>
          <button type="button" onClick={exportFile} className="inline-flex items-center gap-1 rounded-lg border border-border px-2 py-1.5 text-xs text-foreground hover:bg-surface-elevated">
            <Download className="h-3.5 w-3.5" aria-hidden="true" /> Export
          </button>
          <button
            type="button"
            disabled={!canEdit}
            onClick={() => fileInput.current?.click()}
            className="inline-flex items-center gap-1 rounded-lg border border-border px-2 py-1.5 text-xs text-foreground hover:bg-surface-elevated disabled:opacity-50"
          >
            <Upload className="h-3.5 w-3.5" aria-hidden="true" /> Import
          </button>
          <input
            ref={fileInput}
            type="file"
            accept=".md,text/markdown"
            className="hidden"
            aria-label="Import an agent file"
            onChange={(e) => void importFile(e.target.files?.[0])}
          />
          {onReset && draft.built_in && (draft.customized || !draft.enabled) && (
            <button type="button" onClick={() => void onReset(draft)} className="inline-flex items-center gap-1 rounded-lg border border-border px-2 py-1.5 text-xs text-foreground hover:bg-surface-elevated">
              <RotateCcw className="h-3.5 w-3.5" aria-hidden="true" /> Reset to default
            </button>
          )}
          <button
            type="button"
            data-testid="agent-save"
            onClick={() => void save()}
            disabled={busy || !canEdit || !dirty}
            className="inline-flex items-center gap-1 rounded-lg bg-brand px-3 py-1.5 text-xs font-semibold text-white hover:bg-brand-hover disabled:opacity-50"
          >
            {savedAt ? <CheckCircle2 className="h-3.5 w-3.5" aria-hidden="true" /> : <Save className="h-3.5 w-3.5" aria-hidden="true" />}
            {savedAt ? "Saved" : busy ? "Saving…" : draft.built_in && !isAdmin ? "Save a copy" : "Save"}
          </button>
        </div>
      </div>
      {error && (
        <p data-testid="persona-save-error" role="alert" className="flex items-center gap-2 text-xs text-rose-500">
          <AlertCircle className="h-4 w-4" aria-hidden="true" /> {error}
        </p>
      )}
      <div className="grid gap-4 md:grid-cols-[11rem_minmax(0,1fr)]">
        <div role="tablist" aria-label="Agent settings" aria-orientation="vertical" className="flex gap-1 overflow-x-auto md:flex-col">
          {sections.map((s) => (
            <button
              key={s.key}
              type="button"
              role="tab"
              id={`agent-tab-${s.key}`}
              aria-selected={current.key === s.key}
              aria-controls={`agent-panel-${s.key}`}
              tabIndex={current.key === s.key ? 0 : -1}
              data-testid={`agent-section-${s.key}`}
              onClick={() => setSection(s.key)}
              onKeyDown={tabKeyHandler(sections.map((x) => x.key), current.key, setSection, (key) => `agent-tab-${key}`, "vertical")}
              className={`whitespace-nowrap rounded-lg px-2.5 py-1.5 text-left text-xs ${current.key === s.key ? "bg-surface-elevated font-semibold text-foreground" : "text-neutral-600 hover:text-foreground dark:text-neutral-400"}`}
            >
              {s.label}
            </button>
          ))}
        </div>
        <div role="tabpanel" id={`agent-panel-${current.key}`} aria-labelledby={`agent-tab-${current.key}`} className="min-w-0">
          <Component draft={draft} patch={patch} canEdit={canEdit} isAdmin={isAdmin} registries={registries} />
        </div>
      </div>
    </div>
  );
};

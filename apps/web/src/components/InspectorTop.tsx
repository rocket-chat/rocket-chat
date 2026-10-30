"use client";

import React, { useState } from "react";
import { useShallow } from "zustand/react/shallow";
import { useMissionStore } from "../lib/store";
import type { InspectorTab } from "../lib/store/types";
import { ChangesPanel } from "./ChangesPanel";
import { GitPanel } from "./GitPanel";
import { PreviewPanel } from "./PreviewPanel";
import { ProcessesPanel } from "./ProcessesPanel";
import { SubagentsPanel } from "./SubagentsPanel";
import { WorkspaceBrowser } from "./WorkspaceBrowser";
import { ArtifactsPanel } from "./ArtifactsDrawer";
import { tabKeyHandler } from "../lib/tab-keys";

const TABS: { id: InspectorTab; label: string }[] = [
  { id: "changes", label: "Changes" },
  { id: "git", label: "Git" },
  { id: "files", label: "Files" },
  { id: "processes", label: "Processes" },
  { id: "subagents", label: "Subagents" },
  { id: "artifacts", label: "Artifacts" },
  { id: "preview", label: "Preview" },
];

/** The inspector's upper half: what the session changed, or the live workspace. */
export const InspectorTop: React.FC = () => {
  const { tab, setTab, changed, running, artifacts } = useMissionStore(
    useShallow((state) => ({
      tab: state.inspectorTab,
      setTab: state.setInspectorTab,
      changed: state.fileDiffs.length,
      running: state.runningProcesses,
      artifacts: state.artifacts.length,
    })),
  );
  const [previewRequest, setPreviewRequest] = useState<{ port: number; nonce: number } | null>(null);
  const openPreview = (port: number) => {
    setPreviewRequest({ port, nonce: Date.now() });
    setTab("preview");
  };
  const count: Partial<Record<InspectorTab, { value: number; label: string }>> = {
    changes: { value: changed, label: "files changed" },
    processes: { value: running, label: "running" },
    artifacts: { value: artifacts, label: "artifacts" },
  };
  const moveWithKeys = tabKeyHandler(TABS.map((t) => t.id), tab, setTab, (id) => `inspector-tab-${id}`);
  const tabClass = (name: InspectorTab) =>
    `relative flex shrink-0 items-center gap-1.5 whitespace-nowrap px-3 py-2 text-xs font-medium cursor-pointer border-b-2 transition-colors ${
      tab === name
        ? "border-brand text-foreground"
        : "border-transparent text-neutral-600 hover:text-foreground dark:text-neutral-300"
    }`;
  return (
    <div className="h-full flex flex-col min-h-0">
      <div role="tablist" aria-label="Inspector" className="flex shrink-0 overflow-x-auto border-b border-border">
        {TABS.map(({ id, label }) => {
          const badge = count[id];
          return (
            <button
              key={id}
              id={`inspector-tab-${id}`}
              role="tab"
              aria-selected={tab === id}
              aria-controls="inspector-tabpanel"
              tabIndex={tab === id ? 0 : -1}
              data-testid={`tab-${id}`}
              type="button"
              className={tabClass(id)}
              onClick={() => setTab(id)}
              onKeyDown={moveWithKeys}
            >
              {label}
              {badge && badge.value > 0 && (
                <span
                  data-testid={`tab-${id}-count`}
                  title={`${badge.value} ${badge.label}`}
                  className="rounded-full bg-brand/15 px-1.5 text-[10px] font-semibold text-brand"
                >
                  {badge.value}
                </span>
              )}
            </button>
          );
        })}
      </div>
      <div
        id="inspector-tabpanel"
        role="tabpanel"
        aria-labelledby={`inspector-tab-${tab}`}
        data-testid="inspector-tabpanel"
        className="flex-1 min-h-0"
      >
        {tab === "changes" && <ChangesPanel />}
        {tab === "git" && <GitPanel />}
        {tab === "files" && <WorkspaceBrowser />}
        {tab === "processes" && <ProcessesPanel onOpenPreview={openPreview} />}
        {tab === "subagents" && <SubagentsPanel />}
        {tab === "artifacts" && <ArtifactsPanel />}
        {tab === "preview" && <PreviewPanel request={previewRequest} />}
      </div>
    </div>
  );
};

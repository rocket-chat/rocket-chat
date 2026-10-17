"use client";

import React, { useState } from "react";
import { MonacoDiffViewer } from "./MonacoDiffViewer";
import { PreviewPanel } from "./PreviewPanel";
import { ProcessesPanel } from "./ProcessesPanel";
import { SubagentsPanel } from "./SubagentsPanel";
import { WorkspaceBrowser } from "./WorkspaceBrowser";

type Tab = "changes" | "files" | "processes" | "subagents" | "preview";

/** The inspector's upper half: the agent's proposed changes, or the live workspace. */
export const InspectorTop: React.FC<{
  onApprove?: (actionId: string) => void;
  onReject?: (actionId: string) => void;
}> = ({ onApprove, onReject }) => {
  const [tab, setTab] = useState<Tab>("changes");
  const [previewRequest, setPreviewRequest] = useState<{ port: number; nonce: number } | null>(null);
  const openPreview = (port: number) => {
    setPreviewRequest({ port, nonce: Date.now() });
    setTab("preview");
  };
  const tabClass = (name: Tab) =>
    `px-3 py-1 text-[11px] font-mono uppercase tracking-wide cursor-pointer ${
      tab === name ? "bg-cyan/15 text-cyan" : "text-muted-foreground hover:text-foreground"
    }`;
  return (
    <div className="h-full flex flex-col min-h-0">
      <div role="tablist" aria-label="Inspector" className="flex border-b border-border shrink-0">
        <button role="tab" aria-selected={tab === "changes"} data-testid="tab-changes" type="button" className={tabClass("changes")} onClick={() => setTab("changes")}>
          Changes
        </button>
        <button role="tab" aria-selected={tab === "files"} data-testid="tab-files" type="button" className={tabClass("files")} onClick={() => setTab("files")}>
          Files
        </button>
        <button role="tab" aria-selected={tab === "processes"} data-testid="tab-processes" type="button" className={tabClass("processes")} onClick={() => setTab("processes")}>
          Processes
        </button>
        <button role="tab" aria-selected={tab === "subagents"} data-testid="tab-subagents" type="button" className={tabClass("subagents")} onClick={() => setTab("subagents")}>
          Subagents
        </button>
        <button role="tab" aria-selected={tab === "preview"} data-testid="tab-preview" type="button" className={tabClass("preview")} onClick={() => setTab("preview")}>
          Preview
        </button>
      </div>
      <div className="flex-1 min-h-0">
        {tab === "changes" && <MonacoDiffViewer onApprove={onApprove} onReject={onReject} />}
        {tab === "files" && <WorkspaceBrowser />}
        {tab === "processes" && <ProcessesPanel onOpenPreview={openPreview} />}
        {tab === "subagents" && <SubagentsPanel />}
        {tab === "preview" && <PreviewPanel request={previewRequest} />}
      </div>
    </div>
  );
};

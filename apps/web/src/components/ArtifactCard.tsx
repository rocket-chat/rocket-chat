"use client";

import React from "react";
import { AlertTriangle, ChevronRight, FileText, Loader2 } from "lucide-react";
import { useMissionStore } from "../lib/store";
import type { ArtifactMention } from "../lib/artifact-calls";

/** A deliverable in the conversation: what it is, and a way to open it in the inspector. */
export const ArtifactCard: React.FC<{ mention: ArtifactMention }> = ({ mention }) => {
  const openArtifact = useMissionStore((s) => s.openArtifact);
  const { id, title, filename, version, change, state } = mention;
  const opens = state === "saved" && id !== null;
  const status =
    state === "saving"
      ? change === "updated" ? "Saving a new version…" : "Saving…"
      : state === "failed"
        ? change === "updated" ? "Could not be updated" : "Could not be saved"
        : `${change === "updated" ? "Updated" : "Created"}${version ? ` · v${version}` : ""}`;

  const body = (
    <>
      <span
        aria-hidden="true"
        className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border border-amber-500/30 bg-amber-500/10 text-amber-500"
      >
        {state === "saving" ? (
          <Loader2 className="h-4 w-4 animate-spin" />
        ) : state === "failed" ? (
          <AlertTriangle className="h-4 w-4 text-diff-del" />
        ) : (
          <FileText className="h-4 w-4" />
        )}
      </span>
      <span className="min-w-0 flex-1">
        <span className="block truncate text-sm font-medium text-foreground">{title}</span>
        <span className="block truncate text-xs text-neutral-500 dark:text-neutral-400">
          {[filename, status].filter(Boolean).join(" · ")}
        </span>
      </span>
      {opens && <ChevronRight aria-hidden="true" className="h-4 w-4 shrink-0 text-neutral-400" />}
    </>
  );

  const frame =
    "my-2 flex w-full max-w-md items-center gap-3 rounded-xl border border-surface-border bg-surface-card px-3 py-2.5 text-left";
  return opens ? (
    <button
      type="button"
      data-testid="artifact-card"
      data-artifact-id={id}
      onClick={() => openArtifact(id)}
      aria-label={`Open ${title}`}
      className={`${frame} cursor-pointer transition-colors hover:bg-surface-elevated focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand/60`}
    >
      {body}
    </button>
  ) : (
    <div data-testid="artifact-card" data-artifact-state={state} className={frame}>
      {body}
    </div>
  );
};

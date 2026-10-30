"use client";

import React, { useCallback, useEffect, useState } from "react";
import { useMissionStore } from "../lib/store";
import { ArtifactBody } from "./ArtifactBody";
import {
  FileText,
  Download,
  Copy,
  Check,
  RefreshCw,
  Code2,
  FileCode,
} from "lucide-react";

export const ArtifactsPanel: React.FC = () => {
  const session = useMissionStore((s) => s.session);
  const sessionId = session && !session.is_draft ? session.session_id : null;
  const storeArtifacts = useMissionStore((s) => s.artifacts);
  const setStoreArtifacts = useMissionStore((s) => s.setArtifacts);

  const selectedId = useMissionStore((s) => s.selectedArtifactId);
  const setSelectedId = useMissionStore((s) => s.selectArtifact);
  // Null while it loads; "" is an artifact that is empty.
  const [selectedContent, setSelectedContent] = useState<string | null>(null);
  const [loadFailed, setLoadFailed] = useState(false);
  const [versions, setVersions] = useState<{ version: number; created_at: number; size: number }[]>([]);
  // The version being read; null is the current one.
  const [shownVersion, setShownVersion] = useState<number | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [copied, setCopied] = useState(false);

  const loadArtifacts = useCallback(async () => {
    if (!sessionId) return;
    setIsLoading(true);
    try {
      const res = await fetch(`/v1/sessions/${sessionId}/artifacts`, {
        signal: AbortSignal.timeout(8000),
      });
      if (res.ok) {
        const data = await res.json();
        setStoreArtifacts(Array.isArray(data) ? data : []);
      }
    } catch {
      // Offline or network error
    } finally {
      setIsLoading(false);
    }
  }, [sessionId, setStoreArtifacts]);

  useEffect(() => {
    if (sessionId) {
      loadArtifacts();
    }
  }, [sessionId, loadArtifacts]);

  // No selection, or one the list does not hold (yet: a card opened before the list loaded), shows
  // the latest; a card's own artifact is kept once the list has it.
  const selectedIsListed = storeArtifacts.some((a) => a.id === selectedId);
  useEffect(() => {
    if (!selectedIsListed && storeArtifacts.length > 0 && (selectedId === null || !isLoading)) {
      setSelectedId(storeArtifacts[0].id);
    }
  }, [storeArtifacts, selectedId, selectedIsListed, isLoading, setSelectedId]);

  const activeArtifact = storeArtifacts.find((a) => a.id === selectedId);
  const currentVersion = activeArtifact?.version;

  const loadDetail = useCallback(
    async (artId: string, version: number | null, signal: AbortSignal) => {
      if (!sessionId) return;
      setSelectedContent(null);
      setLoadFailed(false);
      try {
        const query = version === null ? "" : `?version=${version}`;
        const res = await fetch(`/v1/sessions/${sessionId}/artifacts/${artId}${query}`, {
          signal: AbortSignal.any([signal, AbortSignal.timeout(8000)]),
        });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        setSelectedContent(typeof data.content === "string" ? data.content : "");
      } catch {
        // A newer selection replaced this read: what it shows is not this artifact's to set.
        if (!signal.aborted) setLoadFailed(true);
      }
    },
    [sessionId]
  );

  // Another artifact starts at its latest version.
  useEffect(() => {
    setShownVersion(null);
  }, [selectedId]);

  // Read again when the agent saves a new version of the one being shown.
  useEffect(() => {
    if (!selectedId || !selectedIsListed) {
      setSelectedContent(null);
      return;
    }
    const reading = new AbortController();
    void loadDetail(selectedId, shownVersion, reading.signal);
    return () => reading.abort();
  }, [selectedId, selectedIsListed, shownVersion, currentVersion, loadDetail]);

  useEffect(() => {
    if (!selectedId || !sessionId) {
      setVersions([]);
      return;
    }
    let cancelled = false;
    fetch(`/v1/sessions/${sessionId}/artifacts/${selectedId}/versions`)
      .then((res) => (res.ok ? res.json() : []))
      .then((data) => !cancelled && setVersions(Array.isArray(data) ? data : []))
      .catch(() => !cancelled && setVersions([]));
    return () => {
      cancelled = true;
    };
  }, [selectedId, sessionId, currentVersion]);

  const handleCopy = () => {
    if (selectedContent) {
      navigator.clipboard.writeText(selectedContent);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    }
  };

  const handleDownload = () => {
    if (!activeArtifact || selectedContent === null) return;
    const mime = activeArtifact.artifact_type === "html" ? "text/html" : activeArtifact.artifact_type === "json" ? "application/json" : "text/plain";
    const blob = new Blob([selectedContent], { type: `${mime};charset=utf-8` });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = activeArtifact.filename || "artifact.txt";
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    URL.revokeObjectURL(url);
  };

  return (
    <div className="flex h-full flex-1 overflow-hidden">
      {/* Left List of Artifacts */}
      <div className="w-1/3 overflow-y-auto border-r border-border p-2">
        <div className="mb-2 flex items-center justify-between px-1">
          <span className="text-[11px] font-mono text-muted-foreground uppercase">
            Artifacts ({storeArtifacts.length})
          </span>
          <button
            onClick={loadArtifacts}
            disabled={isLoading}
            className="rounded p-1 text-muted-foreground hover:bg-surface-elevated hover:text-foreground"
            title="Refresh"
          >
            <RefreshCw className={`h-3.5 w-3.5 ${isLoading ? "animate-spin" : ""}`} />
          </button>
        </div>
        {storeArtifacts.length === 0 ? (
          <div className="p-4 text-center text-xs text-muted-foreground">
            No artifacts created yet. Ask the agent to generate reports, specs, or plans!
          </div>
        ) : (
          <div className="space-y-1">
            {storeArtifacts.map((art) => {
              const isSelected = art.id === selectedId;
              return (
                <button
                  key={art.id}
                  onClick={() => setSelectedId(art.id)}
                  className={`w-full rounded-md p-2 text-left transition ${
                    isSelected
                      ? "bg-cyan/15 text-cyan"
                      : "hover:bg-surface-elevated text-foreground/80 hover:text-foreground"
                  }`}
                >
                  <div className="flex items-center gap-1.5">
                    {art.artifact_type === "diff" ? (
                      <FileCode className="h-3.5 w-3.5 text-emerald-500 shrink-0" />
                    ) : art.artifact_type === "json" ? (
                      <Code2 className="h-3.5 w-3.5 text-blue-500 shrink-0" />
                    ) : (
                      <FileText className="h-3.5 w-3.5 text-amber-500 shrink-0" />
                    )}
                    <span className="truncate text-xs font-medium">{art.title}</span>
                  </div>
                  <div className="mt-1 flex items-center justify-between text-[10px] text-muted-foreground">
                    <span className="truncate font-mono">{art.filename}</span>
                    <span className="rounded bg-surface-card px-1 border border-border">
                      v{art.version}
                    </span>
                  </div>
                </button>
              );
            })}
          </div>
        )}
      </div>

      {/* Right Artifact Viewer */}
      <div className="flex flex-1 flex-col overflow-hidden bg-surface-base">
        {activeArtifact ? (
          <>
            {/* Toolbar */}
            <div className="flex items-center justify-between border-b border-border bg-surface-card px-3 py-1.5">
              <div className="min-w-0 flex-1">
                <h3 className="truncate text-xs font-semibold text-foreground">
                  {activeArtifact.title}
                </h3>
                <div className="flex items-center gap-2 text-[10px] text-muted-foreground">
                  <span className="font-mono">{activeArtifact.filename}</span>
                  <span>•</span>
                  <span className="capitalize">{activeArtifact.artifact_type}</span>
                  <span>•</span>
                  {versions.length > 1 ? (
                    <select
                      aria-label="Artifact version"
                      data-testid="artifact-version"
                      value={shownVersion ?? activeArtifact.version}
                      onChange={(e) => {
                        const chosen = Number(e.target.value);
                        setShownVersion(chosen === activeArtifact.version ? null : chosen);
                      }}
                      className="rounded border border-border bg-surface-card px-1 text-[10px]"
                    >
                      {[...versions].reverse().map((v) => (
                        <option key={v.version} value={v.version}>
                          v{v.version}
                          {v.version === activeArtifact.version ? " (current)" : ""}
                        </option>
                      ))}
                    </select>
                  ) : (
                    <span>v{activeArtifact.version}</span>
                  )}
                </div>
              </div>
              <div className="flex items-center gap-1.5 shrink-0">
                <button
                  onClick={handleCopy}
                  className="flex items-center gap-1 rounded border border-border bg-surface-card px-2 py-0.5 text-xs text-foreground/80 hover:bg-surface-elevated"
                  title="Copy content"
                >
                  {copied ? <Check className="h-3 w-3 text-emerald-500" /> : <Copy className="h-3 w-3" />}
                  <span>{copied ? "Copied" : "Copy"}</span>
                </button>
                <button
                  onClick={handleDownload}
                  className="flex items-center gap-1 rounded border border-border bg-surface-card px-2 py-0.5 text-xs text-foreground/80 hover:bg-surface-elevated"
                  title="Download artifact file"
                >
                  <Download className="h-3 w-3" />
                  <span>Download</span>
                </button>
              </div>
            </div>

            {/* Document Render Body */}
            <div className="flex-1 overflow-y-auto p-4">
              {loadFailed ? (
                <div role="alert" className="flex h-full flex-col items-center justify-center gap-2 text-xs text-muted-foreground">
                  <span>This artifact could not be loaded.</span>
                  <button
                    type="button"
                    onClick={() => void loadDetail(activeArtifact.id, shownVersion, new AbortController().signal)}
                    className="rounded border border-border px-2 py-0.5 text-foreground hover:bg-surface-elevated"
                  >
                    Try again
                  </button>
                </div>
              ) : selectedContent === null ? (
                <div className="flex h-full items-center justify-center text-xs text-muted-foreground">
                  Loading content...
                </div>
              ) : selectedContent === "" ? (
                <div className="flex h-full items-center justify-center text-xs text-muted-foreground">
                  This artifact is empty.
                </div>
              ) : (
                <ArtifactBody type={activeArtifact.artifact_type} content={selectedContent} />
              )}
            </div>
          </>
        ) : (
          <div className="flex h-full items-center justify-center text-xs text-muted-foreground">
            Select an artifact from the list to preview
          </div>
        )}
      </div>
    </div>
  );
};

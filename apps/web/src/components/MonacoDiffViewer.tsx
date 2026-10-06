"use client";

import React, { useState, useMemo } from "react";
import dynamic from "next/dynamic";
import { useMissionStore } from "../lib/store";
import { FileCode, Check, X, Split, FileText } from "lucide-react";

// Dynamically import Monaco DiffEditor to avoid SSR issues
const DiffEditor = dynamic(
  () => import("@monaco-editor/react").then((mod) => mod.DiffEditor),
  {
    ssr: false,
    loading: () => (
      <div className="h-full flex items-center justify-center font-mono text-xs text-muted-foreground bg-void">
        Initializing Monaco Diff Engine...
      </div>
    ),
  }
);

interface MonacoDiffViewerProps {
  onApprove?: (actionId: string) => void;
  onReject?: (actionId: string) => void;
}

function parseUnifiedDiff(diffContent: string): { original: string; modified: string } {
  const originalLines: string[] = [];
  const modifiedLines: string[] = [];

  const lines = diffContent.split("\n");
  for (const line of lines) {
    if (line.startsWith("---") || line.startsWith("+++") || line.startsWith("@@")) {
      continue;
    }
    if (line.startsWith("-")) {
      originalLines.push(line.substring(1));
    } else if (line.startsWith("+")) {
      modifiedLines.push(line.substring(1));
    } else {
      const content = line.startsWith(" ") ? line.substring(1) : line;
      originalLines.push(content);
      modifiedLines.push(content);
    }
  }

  return {
    original: originalLines.join("\n"),
    modified: modifiedLines.join("\n"),
  };
}

export const MonacoDiffViewer: React.FC<MonacoDiffViewerProps> = ({
  onApprove,
  onReject,
}) => {
  const { fileDiffs, activeDiffIndex, setActiveDiffIndex } = useMissionStore();
  const [inlineMode, setInlineMode] = useState<boolean>(false);

  const activeDiff = fileDiffs[activeDiffIndex] || null;

  const { original, modified } = useMemo(() => {
    if (!activeDiff) return { original: "", modified: "" };
    return parseUnifiedDiff(activeDiff.diff_content);
  }, [activeDiff]);

  if (!activeDiff) {
    return (
      <div className="h-full flex flex-col items-center justify-center p-6 text-center font-mono bg-void/50 select-none">
        <FileCode className="w-10 h-10 text-muted-foreground/30 mb-3" />
        <span className="text-foreground text-sm font-medium font-sans">
          No Proposed Diffs
        </span>
        <span className="text-muted-foreground text-xs mt-1 max-w-sm">
          When the autonomous agent edits workspace files, syntax-highlighted side-by-side diffs will appear here in real time.
        </span>
      </div>
    );
  }

  return (
    <div className="h-full flex flex-col bg-void border-l border-border select-none">
      {/* Top File Switcher Bar */}
      <div className="h-10 border-b border-border bg-surface px-3 flex items-center justify-between overflow-x-auto">
        <div className="flex items-center gap-1.5 overflow-x-auto">
          {fileDiffs.map((diff, idx) => {
            const isSelected = idx === activeDiffIndex;
            return (
              <button
                key={diff.path}
                onClick={() => setActiveDiffIndex(idx)}
                className={`flex items-center gap-2 px-2.5 py-1 rounded text-xs font-mono transition-all ${
                  isSelected
                    ? "bg-elevated border border-border text-foreground font-semibold"
                    : "text-muted-foreground hover:text-foreground hover:bg-elevated/40"
                }`}
              >
                <FileText className="w-3.5 h-3.5 text-cyan" />
                <span className="truncate max-w-[140px]">{diff.path}</span>
                <span className="text-[10px] text-diff-add font-bold">+{diff.additions}</span>
                <span className="text-[10px] text-diff-del font-bold">-{diff.deletions}</span>
              </button>
            );
          })}
        </div>

        <div className="flex items-center gap-2">
          {/* Side-by-side / inline toggle */}
          <button
            onClick={() => setInlineMode(!inlineMode)}
            title="Toggle side-by-side or inline diff mode"
            className="p-1 rounded hover:bg-elevated text-muted-foreground hover:text-foreground font-mono text-xs flex items-center gap-1 border border-border"
          >
            <Split className="w-3.5 h-3.5 text-cyan" />
            <span className="text-[10px]">{inlineMode ? "INLINE" : "SIDE-BY-SIDE"}</span>
          </button>
        </div>
      </div>

      {/* Monaco DiffEditor container */}
      <div className="flex-1 relative overflow-hidden">
        <DiffEditor
          original={original}
          modified={modified}
          language="python"
          theme="vs-dark"
          options={{
            readOnly: true,
            renderSideBySide: !inlineMode,
            minimap: { enabled: false },
            fontSize: 12,
            fontFamily: "JetBrains Mono, monospace",
            lineNumbers: "on",
            scrollBeyondLastLine: false,
            wordWrap: "on",
            diffWordWrap: "on",
          }}
        />
      </div>

      {/* Decision Footer for Diffs */}
      <div className="h-12 border-t border-border bg-surface px-4 flex items-center justify-between">
        <div className="flex items-center gap-2 text-xs font-mono text-muted-foreground">
          <span>{fileDiffs.length} file(s) modified</span>
        </div>

        <div className="flex items-center gap-2">
          <button
            onClick={() => onReject?.(`reject_${Date.now()}`)}
            className="px-3 py-1.5 rounded border border-border hover:bg-elevated text-diff-del hover:text-white font-mono text-xs flex items-center gap-1.5 transition-all"
          >
            <X className="w-3.5 h-3.5" />
            <span>Request Revision</span>
          </button>

          <button
            onClick={() => onApprove?.(`commit_${Date.now()}`)}
            className="px-3 py-1.5 rounded bg-flame hover:bg-flame-hover text-white font-mono text-xs font-semibold flex items-center gap-1.5 shadow-md shadow-flame/20 transition-all"
          >
            <Check className="w-3.5 h-3.5" />
            <span>Approve & Commit</span>
          </button>
        </div>
      </div>
    </div>
  );
};

"use client";

import React, { useCallback, useEffect, useMemo, useState } from "react";
import dynamic from "next/dynamic";
import "../lib/monaco";
import { useShallow } from "zustand/react/shallow";
import { useMissionStore } from "../lib/store";
import { FileCode, FilePlus2, FileX2, FileEdit, RefreshCw, Split, Undo2 } from "lucide-react";
import type { FileDiffItem } from "../types/mission";

// Dynamically import Monaco DiffEditor to avoid SSR issues
const DiffEditor = dynamic(() => import("@monaco-editor/react").then((mod) => mod.DiffEditor), {
  ssr: false,
  loading: () => (
    <div className="h-full flex items-center justify-center font-mono text-xs text-muted-foreground bg-void">
      Loading the diff viewer…
    </div>
  ),
});

type Scope = "session" | "turn";
type Status = "added" | "modified" | "deleted";

interface ChangedFile {
  path: string;
  status: Status;
  additions: number;
  deletions: number;
  binary: boolean;
}

interface RepositoryChanges {
  path: string;
  name: string;
  base_label: string;
  files: ChangedFile[];
  truncated: boolean;
}

interface ChangesResponse {
  available: boolean;
  scope: Scope;
  repositories: RepositoryChanges[];
}

interface FileVersions {
  original: string | null;
  modified: string | null;
  binary: boolean;
  truncated: boolean;
}

const LANGUAGES: Record<string, string> = {
  py: "python",
  ts: "typescript",
  tsx: "typescript",
  js: "javascript",
  jsx: "javascript",
  json: "json",
  md: "markdown",
  yml: "yaml",
  yaml: "yaml",
  go: "go",
  rs: "rust",
  java: "java",
  sh: "shell",
  css: "css",
  html: "html",
  sql: "sql",
  toml: "ini",
};

function languageOf(path: string): string {
  const extension = path.includes(".") ? path.split(".").pop()!.toLowerCase() : "";
  return LANGUAGES[extension] ?? "plaintext";
}

/** The before and after of a unified diff's hunks: what the agent's own event carries. */
function sidesOfUnifiedDiff(diff: string): { original: string; modified: string } {
  const original: string[] = [];
  const modified: string[] = [];
  for (const line of diff.split("\n")) {
    if (line.startsWith("---") || line.startsWith("+++") || line.startsWith("@@")) continue;
    if (line.startsWith("-")) original.push(line.slice(1));
    else if (line.startsWith("+")) modified.push(line.slice(1));
    else {
      const text = line.startsWith(" ") ? line.slice(1) : line;
      original.push(text);
      modified.push(text);
    }
  }
  return { original: original.join("\n"), modified: modified.join("\n") };
}

const STATUS_ICON: Record<Status, React.ReactNode> = {
  added: <FilePlus2 className="h-3.5 w-3.5 text-diff-add" aria-label="added" />,
  modified: <FileEdit className="h-3.5 w-3.5 text-cyan" aria-label="modified" />,
  deleted: <FileX2 className="h-3.5 w-3.5 text-diff-del" aria-label="deleted" />,
};

interface Selected {
  repository: string;
  path: string;
}

/**
 * What the session changed, file by file, against where the work started.
 *
 * It is read from git in the sandbox, so it is complete after a reload and in a second tab. The
 * agent's own `file_diff` events only tell it when to look again; when the sandbox cannot be read
 * (asleep, or no repository yet) the changes those events reported are shown instead.
 */
export const ChangesPanel: React.FC = () => {
  const { sessionId, changesVersion, selectedChangePath, fileDiffs } = useMissionStore(
    useShallow((state) => ({
      sessionId: state.session?.session_id,
      changesVersion: state.changesVersion,
      selectedChangePath: state.selectedChangePath,
      fileDiffs: state.fileDiffs,
    })),
  );
  const [scope, setScope] = useState<Scope>("session");
  const [inline, setInline] = useState(false);
  const [data, setData] = useState<ChangesResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [selected, setSelected] = useState<Selected | null>(null);
  const [versions, setVersions] = useState<FileVersions | null>(null);
  const [versionsError, setVersionsError] = useState("");
  const [confirming, setConfirming] = useState(false);
  const [revertError, setRevertError] = useState("");

  const isReal = !!sessionId && sessionId !== "draft" && sessionId !== "new";

  const load = useCallback(async () => {
    if (!isReal) return;
    setLoading(true);
    try {
      const res = await fetch(`/v1/sessions/${sessionId}/changes?scope=${scope}`);
      setData(res.ok ? ((await res.json()) as ChangesResponse) : null);
    } catch {
      setData(null);
    } finally {
      setLoading(false);
    }
  }, [sessionId, scope, isReal]);

  // Look again when the agent reports a change, with a short wait so a burst of edits is one read.
  useEffect(() => {
    const timer = setTimeout(load, 400);
    return () => clearTimeout(timer);
  }, [load, changesVersion]);

  const repositories = useMemo(
    () => (data?.available ? data.repositories.filter((repo) => repo.files.length > 0) : []),
    [data],
  );
  const reported = repositories.length === 0 ? fileDiffs : [];
  const total = repositories.reduce((sum, repo) => sum + repo.files.length, 0);
  const additions = repositories.reduce((s, r) => s + r.files.reduce((a, f) => a + f.additions, 0), 0);
  const deletions = repositories.reduce((s, r) => s + r.files.reduce((a, f) => a + f.deletions, 0), 0);

  // Follow "open in inspector" from a step in the conversation.
  useEffect(() => {
    if (!selectedChangePath) return;
    const match = repositories
      .flatMap((repo) => repo.files.map((file) => ({ repository: repo.path, path: file.path })))
      .find((entry) => selectedChangePath.endsWith(entry.path));
    setSelected(match ?? { repository: "", path: selectedChangePath });
  }, [selectedChangePath, repositories]);

  // Keep a valid selection: the first file, until the person picks another.
  useEffect(() => {
    if (selected && (selected.repository === "" || repositories.some((repo) => repo.path === selected.repository && repo.files.some((f) => f.path === selected.path)))) return;
    const first = repositories[0]?.files[0];
    setSelected(first ? { repository: repositories[0].path, path: first.path } : null);
  }, [repositories, selected]);

  useEffect(() => {
    setVersions(null);
    setVersionsError("");
    setConfirming(false);
    setRevertError("");
    if (!selected || !selected.repository || !isReal) return;
    let cancelled = false;
    const query = new URLSearchParams({ repository: selected.repository, path: selected.path, scope });
    fetch(`/v1/sessions/${sessionId}/changes/file?${query}`)
      .then(async (res) => {
        if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || `HTTP ${res.status}`);
        return (await res.json()) as FileVersions;
      })
      .then((body) => !cancelled && setVersions(body))
      .catch((err) => !cancelled && setVersionsError(err instanceof Error ? err.message : "Could not read the file"));
    return () => {
      cancelled = true;
    };
  }, [selected, scope, sessionId, isReal, changesVersion]);

  const revert = async () => {
    if (!selected || !selected.repository) return;
    setRevertError("");
    try {
      const res = await fetch(`/v1/sessions/${sessionId}/changes/revert`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ repository: selected.repository, path: selected.path, scope }),
      });
      if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || `HTTP ${res.status}`);
      setConfirming(false);
      await load();
    } catch (err) {
      setConfirming(false);
      setRevertError(err instanceof Error ? err.message : "Could not undo the change");
    }
  };

  const reportedDiff: FileDiffItem | undefined =
    selected && selected.repository === "" ? fileDiffs.find((d) => d.path === selected.path) : undefined;
  const sides = reportedDiff ? sidesOfUnifiedDiff(reportedDiff.diff_content) : null;
  const original = sides ? sides.original : (versions?.original ?? "");
  const modified = sides ? sides.modified : (versions?.modified ?? "");
  const unavailable = data !== null && !data.available;

  if (total === 0 && reported.length === 0) {
    return (
      <div data-testid="changes-empty" className="h-full flex flex-col items-center justify-center p-6 text-center bg-void/50 select-none">
        <FileCode className="w-10 h-10 text-muted-foreground/30 mb-3" />
        <span className="text-foreground text-sm font-medium">
          {unavailable ? "The sandbox is not running" : loading && !data ? "Reading the workspace…" : "No changes yet"}
        </span>
        <span className="text-muted-foreground text-xs mt-1 max-w-sm">
          {unavailable
            ? "Changes are read from the workspace, so they appear once the sandbox is running again."
            : "Files the agent changes in its repositories are listed here, compared with where the work started."}
        </span>
      </div>
    );
  }

  const files: { key: string; repository: string; repositoryName: string; file: Pick<ChangedFile, "path" | "additions" | "deletions"> & { status: Status } }[] =
    repositories.length > 0
      ? repositories.flatMap((repo) =>
          repo.files.map((file) => ({ key: `${repo.path}:${file.path}`, repository: repo.path, repositoryName: repo.name, file })),
        )
      : reported.map((diff) => ({
          key: `reported:${diff.path}`,
          repository: "",
          repositoryName: "",
          file: { path: diff.path, additions: diff.additions, deletions: diff.deletions, status: diff.is_new_file ? ("added" as const) : ("modified" as const) },
        }));
  const baseLabel = repositories[0]?.base_label ?? "reported by the agent";
  const multiple = repositories.length > 1;

  return (
    <div className="h-full flex flex-col bg-void border-l border-border select-none" data-testid="changes-panel">
      <div className="min-h-10 border-b border-border bg-surface px-3 py-1 flex items-center justify-between gap-2 flex-wrap">
        <div className="flex items-center gap-2 text-xs font-mono">
          <div role="group" aria-label="Compare with" className="flex rounded border border-border overflow-hidden">
            {(["session", "turn"] as const).map((value) => (
              <button
                key={value}
                type="button"
                data-testid={`changes-scope-${value}`}
                aria-pressed={scope === value}
                onClick={() => setScope(value)}
                className={`px-2 py-0.5 text-[11px] ${scope === value ? "bg-cyan/15 text-cyan" : "text-muted-foreground hover:text-foreground"}`}
              >
                {value === "session" ? "Whole session" : "Latest turn"}
              </button>
            ))}
          </div>
          <span className="text-muted-foreground" data-testid="changes-base">{baseLabel}</span>
        </div>
        <div className="flex items-center gap-1.5">
          <button type="button" onClick={load} title="Read the workspace again" aria-label="Refresh changes" className="p-1 rounded border border-border text-muted-foreground hover:text-foreground">
            <RefreshCw className={`w-3.5 h-3.5 ${loading ? "animate-spin" : ""}`} />
          </button>
          <button type="button" onClick={() => setInline(!inline)} title="Toggle side-by-side or inline diff" className="p-1 rounded border border-border text-muted-foreground hover:text-foreground font-mono text-[10px] flex items-center gap-1">
            <Split className="w-3.5 h-3.5 text-cyan" />
            {inline ? "INLINE" : "SIDE-BY-SIDE"}
          </button>
        </div>
      </div>

      <div className="flex flex-1 min-h-0 flex-col md:flex-row">
        <ul data-testid="changes-files" className="md:w-60 md:shrink-0 max-h-40 md:max-h-none overflow-y-auto border-b md:border-b-0 md:border-r border-border bg-surface/40">
          {files.map(({ key, repository, repositoryName, file }) => {
            const isSelected = selected?.repository === repository && selected?.path === file.path;
            return (
              <li key={key}>
                <button
                  type="button"
                  aria-current={isSelected}
                  onClick={() => setSelected({ repository, path: file.path })}
                  className={`w-full flex items-center gap-2 px-2.5 py-1.5 text-left text-xs font-mono ${isSelected ? "bg-elevated text-foreground" : "text-muted-foreground hover:bg-elevated/40 hover:text-foreground"}`}
                >
                  {STATUS_ICON[file.status]}
                  <span className="min-w-0 flex-1 truncate" title={multiple && repositoryName ? `${repositoryName}/${file.path}` : file.path}>
                    {multiple && repositoryName ? `${repositoryName}/` : ""}
                    {file.path}
                  </span>
                  <span className="text-[10px] text-diff-add font-bold">+{file.additions}</span>
                  <span className="text-[10px] text-diff-del font-bold">-{file.deletions}</span>
                </button>
              </li>
            );
          })}
        </ul>

        <div className="flex-1 min-w-0 min-h-0 relative">
          {versionsError ? (
            <p role="alert" className="p-4 text-xs text-diff-del">{versionsError}</p>
          ) : versions?.binary ? (
            <p className="p-4 text-xs text-muted-foreground">This is a binary file; there is no text to compare.</p>
          ) : selected ? (
            <DiffEditor
              original={original}
              modified={modified}
              language={languageOf(selected.path)}
              theme="vs-dark"
              options={{
                readOnly: true,
                renderSideBySide: !inline,
                minimap: { enabled: false },
                fontSize: 12,
                fontFamily: "JetBrains Mono, monospace",
                lineNumbers: "on",
                scrollBeyondLastLine: false,
                wordWrap: "on",
                diffWordWrap: "on",
              }}
            />
          ) : null}
        </div>
      </div>

      <div className="min-h-12 border-t border-border bg-surface px-4 py-1.5 flex items-center justify-between gap-2 flex-wrap">
        <div className="flex items-center gap-2 text-xs font-mono text-muted-foreground">
          <span data-testid="changes-summary">
            {repositories.length > 0 ? `${total} file${total === 1 ? "" : "s"} changed` : `${files.length} file${files.length === 1 ? "" : "s"} reported`}
          </span>
          {repositories.length > 0 && (
            <>
              <span className="text-diff-add font-bold">+{additions}</span>
              <span className="text-diff-del font-bold">-{deletions}</span>
            </>
          )}
          {versions?.truncated && <span>(shown cut at the size limit)</span>}
          {reportedDiff && <span>(only the edit the agent reported)</span>}
          {repositories.some((repo) => repo.truncated) && <span>(more files changed than are listed)</span>}
          {revertError && <span role="alert" className="text-diff-del truncate max-w-[280px]" title={revertError}>{revertError}</span>}
        </div>
        {selected?.repository && (
          <div className="flex items-center gap-2">
            {confirming ? (
              <>
                <span className="text-xs text-muted-foreground">Put {selected.path} back as it was?</span>
                <button type="button" data-testid="changes-revert-confirm" onClick={revert} className="px-3 py-1 rounded border border-diff-del/60 text-diff-del hover:bg-diff-del/10 font-mono text-xs">
                  Undo this file
                </button>
                <button type="button" onClick={() => setConfirming(false)} className="px-3 py-1 rounded border border-border text-muted-foreground hover:text-foreground font-mono text-xs">
                  Keep it
                </button>
              </>
            ) : (
              <button type="button" data-testid="changes-revert" onClick={() => setConfirming(true)} title="Put this file back as it was at the start" className="px-3 py-1 rounded border border-border text-muted-foreground hover:text-foreground font-mono text-xs flex items-center gap-1.5">
                <Undo2 className="w-3.5 h-3.5" />
                Undo this file
              </button>
            )}
          </div>
        )}
      </div>
    </div>
  );
};

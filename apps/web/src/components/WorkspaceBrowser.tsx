"use client";

import React, { useCallback, useEffect, useState } from "react";
import dynamic from "next/dynamic";
import "../lib/monaco";
import { ChevronDown, ChevronRight, File, Folder, Save } from "lucide-react";
import { useMissionStore } from "../lib/store";

const Editor = dynamic(() => import("@monaco-editor/react").then((m) => m.default), {
  ssr: false,
  loading: () => (
    <div className="h-full flex items-center justify-center text-xs text-muted-foreground">
      Loading editor…
    </div>
  ),
});

interface Entry {
  name: string;
  path: string;
  is_dir: boolean;
  size_bytes: number;
}

const LANGUAGES: Record<string, string> = {
  py: "python", ts: "typescript", tsx: "typescript", js: "javascript", jsx: "javascript",
  json: "json", md: "markdown", yml: "yaml", yaml: "yaml", sh: "shell", go: "go",
  rs: "rust", html: "html", css: "css", toml: "ini", sql: "sql",
};

const IMAGE_EXTENSION = /\.(png|jpe?g|gif|webp|svg|ico)$/i;

const languageOf = (path: string) => LANGUAGES[path.split(".").pop()?.toLowerCase() ?? ""] ?? "plaintext";

const TreeLevel: React.FC<{
  sessionId: string;
  directory: string;
  depth: number;
  selected: string | null;
  onSelect: (path: string) => void;
  onError: (message: string) => void;
}> = ({ sessionId, directory, depth, selected, onSelect, onError }) => {
  const [entries, setEntries] = useState<Entry[] | null>(null);
  const [open, setOpen] = useState<Record<string, boolean>>({});

  useEffect(() => {
    let cancelled = false;
    fetch(`/v1/sessions/${sessionId}/workspace/tree?path=${encodeURIComponent(directory)}`)
      .then(async (res) => {
        if (res.ok) return (await res.json()).entries as Entry[];
        const body = await res.json().catch(() => ({}));
        throw new Error(typeof body.detail === "string" ? body.detail : `HTTP ${res.status}`);
      })
      .then((found) => !cancelled && setEntries(found))
      .catch((err: Error) => !cancelled && onError(err.message));
    return () => {
      cancelled = true;
    };
  }, [sessionId, directory, onError]);

  if (entries === null) return null;
  return (
    <ul role={depth === 0 ? "tree" : "group"}>
      {entries.map((entry) => (
        <li key={entry.path} role="treeitem" aria-expanded={entry.is_dir ? !!open[entry.path] : undefined}
          aria-selected={selected === entry.path}
        >
          <button
            type="button"
            data-testid={entry.is_dir ? "ws-folder" : "ws-file"}
            onClick={() =>
              entry.is_dir
                ? setOpen({ ...open, [entry.path]: !open[entry.path] })
                : onSelect(entry.path)
            }
            style={{ paddingLeft: 8 + depth * 12 }}
            className={`w-full flex items-center gap-1.5 py-0.5 text-left text-xs font-mono hover:bg-surface-elevated cursor-pointer ${
              selected === entry.path ? "bg-surface-elevated text-foreground" : "text-neutral-300"
            }`}
          >
            {entry.is_dir ? (
              open[entry.path] ? <ChevronDown className="w-3 h-3" /> : <ChevronRight className="w-3 h-3" />
            ) : (
              <span className="w-3" />
            )}
            {entry.is_dir ? <Folder className="w-3.5 h-3.5 text-brand" /> : <File className="w-3.5 h-3.5 text-neutral-500" />}
            <span className="truncate">{entry.name}</span>
          </button>
          {entry.is_dir && open[entry.path] && (
            <TreeLevel
              sessionId={sessionId}
              directory={entry.path}
              depth={depth + 1}
              selected={selected}
              onSelect={onSelect}
              onError={onError}
            />
          )}
        </li>
      ))}
    </ul>
  );
};

/** Browse the session's workspace and edit files in place (what the agent sees, live). */
export const WorkspaceBrowser: React.FC = () => {
  const session = useMissionStore((s) => s.session);
  const sessionId = session && !session.is_draft ? session.session_id : null;
  const [selected, setSelected] = useState<string | null>(null);
  const [imagePath, setImagePath] = useState<string | null>(null);
  const [content, setContent] = useState("");
  const [saved, setSaved] = useState("");
  const [message, setMessage] = useState<string | null>(null);
  const [treeKey, setTreeKey] = useState(0);
  const onError = useCallback((text: string) => setMessage(text), []);

  const open = useCallback(
    async (path: string) => {
      if (!sessionId) return;
      setMessage(null);
      if (IMAGE_EXTENSION.test(path)) {
        setSelected(path);
        setImagePath(path);
        setContent("");
        setSaved("");
        return;
      }
      setImagePath(null);
      const res = await fetch(
        `/v1/sessions/${sessionId}/workspace/file?path=${encodeURIComponent(path)}`,
      );
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        setMessage(typeof body.detail === "string" ? body.detail : `HTTP ${res.status}`);
        return;
      }
      const file = await res.json();
      setSelected(path);
      setContent(file.content);
      setSaved(file.content);
    },
    [sessionId],
  );

  const save = async () => {
    if (!sessionId || !selected) return;
    const res = await fetch(`/v1/sessions/${sessionId}/workspace/file`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path: selected, content }),
    });
    if (res.ok) {
      setSaved(content);
      setMessage("Saved.");
    } else {
      const body = await res.json().catch(() => ({}));
      setMessage(typeof body.detail === "string" ? body.detail : `Save failed (HTTP ${res.status}).`);
    }
  };

  if (!sessionId) {
    return <p className="p-3 text-xs text-muted-foreground">Start a session to browse its workspace.</p>;
  }

  return (
    <div data-testid="workspace-browser" className="h-full flex min-h-0">
      <div className="w-44 shrink-0 overflow-y-auto border-r border-border py-1">
        <button
          type="button"
          onClick={() => setTreeKey(treeKey + 1)}
          className="w-full px-2 pb-1 text-left text-[10px] font-mono uppercase text-neutral-500 hover:text-foreground cursor-pointer"
        >
          Refresh
        </button>
        <TreeLevel
          key={treeKey}
          sessionId={sessionId}
          directory="/workspace"
          depth={0}
          selected={selected}
          onSelect={(path) => void open(path)}
          onError={onError}
        />
      </div>
      <div className="flex-1 min-w-0 flex flex-col">
        <div className="flex items-center justify-between gap-2 px-2 py-1 border-b border-border text-xs font-mono">
          <span className="truncate text-neutral-300" data-testid="ws-current">
            {selected ?? "Select a file"}
            {selected && content !== saved ? " •" : ""}
          </span>
          <div className="flex items-center gap-2">
            {message && <span role="status" className="text-sky-300">{message}</span>}
            <button
              type="button"
              data-testid="ws-save"
              disabled={!selected || content === saved}
              onClick={() => void save()}
              className="inline-flex items-center gap-1 px-2 py-0.5 rounded border border-border hover:bg-surface-elevated disabled:opacity-40 cursor-pointer"
            >
              <Save className="w-3 h-3" /> Save
            </button>
          </div>
        </div>
        <div className="flex-1 min-h-0">
          {imagePath && sessionId && (
            // eslint-disable-next-line @next/next/no-img-element
            <img
              data-testid="ws-image"
              alt={imagePath}
              src={`/v1/sessions/${sessionId}/workspace/raw?path=${encodeURIComponent(imagePath)}`}
              className="max-w-full max-h-full object-contain m-auto"
            />
          )}
          {selected && !imagePath && (
            <Editor
              path={selected}
              language={languageOf(selected)}
              theme="vs-dark"
              value={content}
              onChange={(value) => setContent(value ?? "")}
              options={{ minimap: { enabled: false }, fontSize: 12, scrollBeyondLastLine: false }}
            />
          )}
        </div>
      </div>
    </div>
  );
};

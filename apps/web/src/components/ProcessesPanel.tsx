"use client";

import React, { useCallback, useEffect, useState } from "react";
import { Square } from "lucide-react";
import { useMissionStore } from "../lib/store";
import { Badge, type BadgeTone } from "./ui/Badge";
import { EmptyState } from "./ui/EmptyState";

interface ProcessView {
  process_id: string;
  name: string;
  command: string;
  state: "running" | "exited" | "stopped" | "lost";
  port: number | null;
  exit_code: number | null;
}

const POLL_MS = 3000;

const TONE: Record<ProcessView["state"], BadgeTone> = {
  running: "success",
  exited: "neutral",
  stopped: "neutral",
  lost: "warning",
};

function describe(process: ProcessView): string {
  if (process.state === "exited") return `exited ${process.exit_code ?? ""}`.trim();
  if (process.state === "lost") return "lost";
  return process.state;
}

/** The session's background processes: what is running, its log tail, and a way to stop it. */
export const ProcessesPanel: React.FC<{ onOpenPreview: (port: number) => void }> = ({
  onOpenPreview,
}) => {
  const session = useMissionStore((s) => s.session);
  const sessionId = session && !session.is_draft ? session.session_id : null;
  const [processes, setProcesses] = useState<ProcessView[] | null>(null);
  const [sandboxRunning, setSandboxRunning] = useState(true);
  const [openLog, setOpenLog] = useState<string | null>(null);
  const [log, setLog] = useState("");
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!sessionId) return;
    try {
      const res = await fetch(`/v1/sessions/${sessionId}/processes`);
      if (!res.ok) return;
      const body = await res.json();
      setProcesses(body.processes);
      setSandboxRunning(body.sandbox_running);
    } catch {
      /* the next poll retries */
    }
  }, [sessionId]);

  useEffect(() => {
    void load();
    const timer = setInterval(load, POLL_MS);
    return () => clearInterval(timer);
  }, [load]);

  useEffect(() => {
    if (!sessionId || !openLog) return;
    let cancelled = false;
    const readLog = async () => {
      const res = await fetch(`/v1/sessions/${sessionId}/processes/${openLog}/output?lines=200`);
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        if (!cancelled) setLog(typeof body.detail === "string" ? body.detail : `HTTP ${res.status}`);
        return;
      }
      const body = await res.json();
      if (!cancelled) setLog(body.output);
    };
    void readLog();
    const timer = setInterval(readLog, POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [sessionId, openLog]);

  const stop = async (processId: string) => {
    setError(null);
    const res = await fetch(`/v1/sessions/${sessionId}/processes/${processId}/stop`, {
      method: "POST",
    });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      setError(typeof body.detail === "string" ? body.detail : `HTTP ${res.status}`);
    }
    await load();
  };

  if (!sessionId) {
    return <p className="p-3 text-xs text-muted-foreground">Start a session to see its processes.</p>;
  }
  if (processes === null) {
    return <p className="p-3 text-xs text-muted-foreground">Loading…</p>;
  }
  if (processes.length === 0) {
    return (
      <div data-testid="processes-empty" className="p-3">
        <EmptyState
          title="No background processes"
          description="Dev servers, watchers and long builds the agent starts show up here, with their output."
        />
      </div>
    );
  }

  return (
    <div data-testid="processes-panel" className="h-full overflow-y-auto p-2 space-y-2">
      {!sandboxRunning && (
        <p className="text-xs text-muted-foreground">
          The sandbox is hibernated; running processes end with it. Send a message to wake it.
        </p>
      )}
      {error && (
        <p role="alert" className="text-xs text-amber-300">
          {error}
        </p>
      )}
      {processes.map((p) => (
        <div
          key={p.process_id}
          data-testid="process-row"
          className="rounded-lg border border-border p-2 text-xs"
        >
          <div className="flex items-center gap-2">
            <span className="font-medium text-foreground truncate">{p.name}</span>
            <Badge tone={TONE[p.state]} data-testid="process-state">
              {describe(p)}
            </Badge>
            <span className="ml-auto flex items-center gap-1">
              {p.port && p.state === "running" && (
                <button
                  type="button"
                  data-testid="process-open-preview"
                  onClick={() => onOpenPreview(p.port as number)}
                  className="px-2 py-0.5 rounded border border-border hover:bg-surface-elevated cursor-pointer"
                >
                  Open :{p.port}
                </button>
              )}
              <button
                type="button"
                data-testid="process-log-toggle"
                onClick={() => setOpenLog(openLog === p.process_id ? null : p.process_id)}
                className="px-2 py-0.5 rounded border border-border hover:bg-surface-elevated cursor-pointer"
              >
                {openLog === p.process_id ? "Hide log" : "Log"}
              </button>
              {p.state === "running" && (
                <button
                  type="button"
                  aria-label={`Stop ${p.name}`}
                  data-testid="process-stop"
                  onClick={() => stop(p.process_id)}
                  className="p-1 rounded border border-border hover:bg-surface-elevated cursor-pointer"
                >
                  <Square className="w-3 h-3" />
                </button>
              )}
            </span>
          </div>
          <code className="block mt-1 truncate text-[11px] text-muted-foreground">{p.command}</code>
          {openLog === p.process_id && (
            <pre
              data-testid="process-log"
              className="mt-2 max-h-48 overflow-auto rounded bg-void p-2 text-[11px] leading-snug whitespace-pre-wrap"
            >
              {log || "(no output yet)"}
            </pre>
          )}
        </div>
      ))}
    </div>
  );
};

"use client";

import React, { useCallback, useEffect, useState } from "react";
import { RotateCcw, Save } from "lucide-react";

interface Checkpoint {
  name: string;
  sha: string;
  created_at: number;
}

/** Workspace snapshots the agent (or you) took, with a one-click rewind. */
export const CheckpointsPanel: React.FC<{ sessionId: string }> = ({ sessionId }) => {
  const [checkpoints, setCheckpoints] = useState<Checkpoint[]>([]);
  const [available, setAvailable] = useState(true);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [confirming, setConfirming] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const res = await fetch(`/v1/sessions/${sessionId}/checkpoints`, {
        signal: AbortSignal.timeout(8000),
      });
      if (!res.ok) return;
      const body = await res.json();
      setAvailable(Boolean(body.available));
      setCheckpoints(Array.isArray(body.checkpoints) ? body.checkpoints : []);
    } catch {
      setAvailable(false);
    }
  }, [sessionId]);

  useEffect(() => {
    void load();
  }, [load]);

  const call = async (url: string, body: unknown, success: string) => {
    setBusy(true);
    setMessage(null);
    try {
      const res = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        setMessage(err.detail || `Failed (HTTP ${res.status}).`);
      } else {
        setMessage(success);
        await load();
      }
    } finally {
      setBusy(false);
      setConfirming(null);
    }
  };

  return (
    <section data-testid="checkpoints-panel" aria-label="Checkpoints" className="space-y-2">
      <div className="flex items-center justify-between">
        <h3 className="text-[11px] font-mono uppercase tracking-wider text-neutral-400">
          Checkpoints
        </h3>
        <button
          type="button"
          data-testid="checkpoint-create"
          disabled={busy || !available}
          onClick={() => void call(`/v1/sessions/${sessionId}/checkpoints`, {}, "Checkpoint created.")}
          className="inline-flex items-center gap-1 px-2 py-1 rounded-md border border-surface-border text-[11px] text-neutral-300 hover:bg-surface-elevated disabled:opacity-40 cursor-pointer"
        >
          <Save className="w-3 h-3" /> Save now
        </button>
      </div>
      {!available && (
        <p className="text-[11px] text-neutral-500">
          Available once the sandbox is running a git workspace.
        </p>
      )}
      {available && checkpoints.length === 0 && (
        <p className="text-[11px] text-neutral-500">
          None yet. One is saved automatically before the agent first changes anything in a turn.
        </p>
      )}
      <ul className="space-y-1">
        {checkpoints.map((c) => (
          <li
            key={c.name}
            data-testid="checkpoint-item"
            className="flex items-center justify-between gap-2 text-[11px] font-mono text-neutral-300"
          >
            <span className="truncate" title={`${c.name} @ ${c.sha}`}>
              {c.name.replace("rocket-checkpoint-", "#")} ·{" "}
              {new Date(c.created_at * 1000).toLocaleTimeString()}
            </span>
            {confirming === c.name ? (
              <span className="flex gap-1">
                <button
                  type="button"
                  data-testid="checkpoint-confirm"
                  disabled={busy}
                  onClick={() =>
                    void call(
                      `/v1/sessions/${sessionId}/checkpoints/rewind`,
                      { reference: c.name, mode: "files" },
                      "Workspace restored.",
                    )
                  }
                  className="px-2 py-0.5 rounded bg-amber-500/20 border border-amber-500/40 text-amber-200 cursor-pointer"
                >
                  Restore files
                </button>
                <button
                  type="button"
                  onClick={() => setConfirming(null)}
                  className="px-2 py-0.5 rounded border border-surface-border cursor-pointer"
                >
                  Cancel
                </button>
              </span>
            ) : (
              <button
                type="button"
                data-testid="checkpoint-rewind"
                aria-label={`Rewind to ${c.name}`}
                disabled={busy}
                onClick={() => setConfirming(c.name)}
                className="inline-flex items-center gap-1 px-2 py-0.5 rounded border border-surface-border hover:bg-surface-elevated cursor-pointer"
              >
                <RotateCcw className="w-3 h-3" /> Rewind
              </button>
            )}
          </li>
        ))}
      </ul>
      {message && (
        <p role="status" className="text-[11px] text-sky-300">
          {message}
        </p>
      )}
    </section>
  );
};

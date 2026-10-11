"use client";

import React, { useCallback, useEffect, useState } from "react";
import { Brain, RefreshCw, Trash2, X } from "lucide-react";
import { useMissionStore } from "../lib/store";

interface Memory {
  memory_id: string;
  scope: string;
  kind: string;
  content: string;
  confidence: number;
  source_session_id: string | null;
  created_at: number;
  last_used_at: number | null;
}

interface MemoryListResponse {
  memories: Memory[];
  total: number;
}

const SCOPE_STYLES: Record<string, string> = {
  org: "border-cyan-500/40 bg-cyan-500/10 text-cyan-300",
  user: "border-brand/40 bg-brand/10 text-brand",
  session: "border-violet-500/40 bg-violet-500/10 text-violet-300",
};

function formatEpoch(epoch: number | null): string {
  if (typeof epoch !== "number" || !Number.isFinite(epoch) || epoch <= 0) return "—";
  return new Date(epoch * 1000).toLocaleDateString();
}

/**
 * Cockpit memory inspector.
 *
 * Lists the durable memories the agent wrote for the current user/organisation and lets
 * the user forget one. It is self-contained (its own trigger + slide-over) so it needs no
 * shared store state; the API scopes every read and delete to the caller's tenant.
 */
export const MemoryInspector: React.FC = () => {
  const isOpen = useMissionStore((state) => state.isMemoryInspectorOpen);
  const setIsOpen = useMissionStore((state) => state.setIsMemoryInspectorOpen);
  const [memories, setMemories] = useState<Memory[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [forgetting, setForgetting] = useState<string | null>(null);

  const loadMemories = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const res = await fetch("/v1/memories?limit=100", {
        signal: AbortSignal.timeout(8000),
      });
      if (!res.ok) {
        setMemories([]);
        setError(
          res.status === 503
            ? "Long-term memory requires the PostgreSQL-backed session store."
            : `Failed to load memories (HTTP ${res.status}).`
        );
        return;
      }
      const data = (await res.json()) as MemoryListResponse;
      setMemories(Array.isArray(data.memories) ? data.memories : []);
    } catch {
      setMemories([]);
      setError("Could not reach the memory API.");
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    if (isOpen) void loadMemories();
  }, [isOpen, loadMemories]);

  const forget = useCallback(async (memoryId: string) => {
    setForgetting(memoryId);
    try {
      const res = await fetch(`/v1/memories/${memoryId}`, { method: "DELETE" });
      if (res.ok) {
        setMemories((current) => current.filter((memory) => memory.memory_id !== memoryId));
      } else {
        setError(`Failed to forget memory (HTTP ${res.status}).`);
      }
    } catch {
      setError("Could not reach the memory API.");
    } finally {
      setForgetting(null);
    }
  }, []);

  return (
    <>
      {isOpen && (
        <aside
          role="dialog"
          aria-label="Memory inspector"
          data-testid="memory-inspector"
          className="fixed inset-y-0 right-0 z-40 w-full sm:w-[26rem] bg-surface-base border-l border-surface-border shadow-2xl flex flex-col animate-in slide-in-from-right duration-200"
        >
          <header className="h-14 shrink-0 px-4 border-b border-surface-border flex items-center justify-between select-none">
            <div className="flex items-center gap-2">
              <Brain className="w-4 h-4 text-brand" />
              <span className="text-xs font-mono uppercase tracking-wider text-foreground">
                Long-Term Memory
              </span>
              {memories.length > 0 && (
                <span className="text-[10px] font-mono text-neutral-500">
                  {memories.length}
                </span>
              )}
            </div>
            <div className="flex items-center gap-1">
              <button
                type="button"
                onClick={() => void loadMemories()}
                title="Refresh memories"
                aria-label="Refresh memories"
                className="p-1.5 rounded-md hover:bg-surface-elevated text-neutral-400 hover:text-foreground cursor-pointer"
              >
                <RefreshCw className={`w-3.5 h-3.5 ${isLoading ? "animate-spin" : ""}`} />
              </button>
              <button
                type="button"
                onClick={() => setIsOpen(false)}
                title="Close memory inspector"
                aria-label="Close memory inspector"
                className="p-1.5 rounded-md hover:bg-surface-elevated text-neutral-400 hover:text-foreground cursor-pointer"
              >
                <X className="w-4 h-4" />
              </button>
            </div>
          </header>

          <div className="flex-1 overflow-y-auto p-4 space-y-3">
            {isLoading && memories.length === 0 && (
              <p className="text-xs text-neutral-500 font-mono">Loading memories…</p>
            )}
            {error && <p className="text-xs text-amber-300 font-sans">{error}</p>}
            {!isLoading && !error && memories.length === 0 && (
              <p className="text-xs text-neutral-500 font-mono">
                No long-term memories stored yet.
              </p>
            )}
            {memories.length > 0 && (
              <ul className="space-y-2.5">
                {memories.map((memory) => (
                  <li
                    key={memory.memory_id}
                    data-testid="memory-item"
                    className="rounded-xl border border-surface-border bg-surface-card/60 p-3 space-y-2"
                  >
                    <div className="flex items-center justify-between gap-2">
                      <div className="flex items-center gap-1.5 min-w-0">
                        <span
                          data-testid="memory-scope"
                          className={`inline-flex items-center px-2 py-0.5 rounded-full border text-[10px] font-mono uppercase tracking-wide ${
                            SCOPE_STYLES[memory.scope] ||
                            "border-surface-border bg-surface-card text-neutral-400"
                          }`}
                        >
                          {memory.scope}
                        </span>
                        <span className="text-[10px] font-mono text-neutral-500">
                          {memory.kind}
                        </span>
                      </div>
                      <button
                        type="button"
                        data-testid="memory-forget"
                        disabled={forgetting === memory.memory_id}
                        onClick={() => void forget(memory.memory_id)}
                        title="Forget this memory"
                        aria-label="Forget this memory"
                        className="p-1 rounded-md text-neutral-500 hover:text-rose-300 hover:bg-rose-500/10 transition-colors cursor-pointer disabled:opacity-50"
                      >
                        <Trash2 className="w-3.5 h-3.5" />
                      </button>
                    </div>
                    <p className="text-[12px] text-neutral-200 font-sans leading-relaxed break-words">
                      {memory.content}
                    </p>
                    <div className="flex items-center gap-3 text-[10px] font-mono text-neutral-500">
                      <span>learned {formatEpoch(memory.created_at)}</span>
                      <span>used {formatEpoch(memory.last_used_at)}</span>
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </aside>
      )}
    </>
  );
};

"use client";

import { CheckpointsPanel } from "./CheckpointsPanel";
import { useConfirm } from "./ui/ConfirmDialog";
import React, { useCallback, useEffect, useState } from "react";
import { useMissionStore } from "../lib/store";
import { useSessionUsage } from "../lib/useSessionUsage";
import type { TurnUsage } from "../lib/session-usage";
import { TurnTrace, TurnTracePage } from "../types/mission";
import {
  AlertTriangle,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  CircleDashed,
  GitFork,
  History,
  Paperclip,
  RefreshCw,
  RotateCcw,
  ShieldCheck,
  Wrench,
  X,
} from "lucide-react";

interface TurnInspectorProps {
  /** Re-dispatches an interrupted turn's prompt as a fresh turn. */
  onRetryTurn: (prompt: string) => void;
}

const STATUS_STYLES: Record<string, string> = {
  completed: "border-emerald-500/40 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300",
  error: "border-rose-500/40 bg-rose-500/10 text-rose-700 dark:text-rose-300",
  interrupted: "border-amber-500/40 bg-amber-500/10 text-amber-700 dark:text-amber-300",
  running: "border-brand/40 bg-brand/10 text-brand",
};

const STATUS_ICONS: Record<string, React.ReactNode> = {
  completed: <CheckCircle2 className="w-3 h-3" />,
  error: <AlertTriangle className="w-3 h-3" />,
  interrupted: <AlertTriangle className="w-3 h-3" />,
  running: <CircleDashed className="w-3 h-3 animate-spin" />,
};

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function formatEpoch(epoch: number | null): string {
  if (typeof epoch !== "number" || !Number.isFinite(epoch) || epoch <= 0) return "—";
  return new Date(epoch * 1000).toLocaleTimeString();
}

function formatDuration(started: number, ended: number | null): string {
  if (typeof ended !== "number" || ended < started) return "in flight";
  return `${Math.max(0, ended - started)}s`;
}

/** One persisted turn row, with its reasoning collapsed by default like the flight stream. */
const TurnRow: React.FC<{
  turn: TurnTrace;
  /** The turn's calls as the usage ledger has them: a turn still running has no tokens in its trace yet. */
  ledger?: TurnUsage;
  sessionId: string;
  retryPrompt: string | null;
  onRetry: (prompt: string) => void;
  /** Called once a rewind is applied, so the list reads the remaining turns again. */
  onRewound?: () => void;
}> = ({
  turn,
  ledger,
  sessionId,
  retryPrompt,
  onRetry,
  onRewound,
}) => {
  const [confirm, confirmDialog] = useConfirm();
  const [isReasoningOpen, setIsReasoningOpen] = useState(false);
  const [isCallsOpen, setIsCallsOpen] = useState(false);
  const [isForking, setIsForking] = useState(false);
  const [isRewinding, setIsRewinding] = useState(false);
  const [rewindError, setRewindError] = useState<string | null>(null);
  const statusClass = STATUS_STYLES[turn.status] || "border-surface-border bg-surface-card text-neutral-400";
  const toolCount = turn.tool_calls.length;
  const approvalCount = turn.approvals.length;
  const canRetry = turn.status === "interrupted" || turn.status === "error";

  const tokenRecord = ((turn.tokens ?? {}) as Record<string, unknown>);
  const usage = ((tokenRecord.usage ?? tokenRecord) as Record<string, unknown>);
  const promptTokens = typeof usage.prompt_tokens === "number" ? usage.prompt_tokens : (typeof tokenRecord.prompt_tokens === "number" ? tokenRecord.prompt_tokens : undefined);
  const completionTokens = typeof usage.completion_tokens === "number" ? usage.completion_tokens : (typeof tokenRecord.completion_tokens === "number" ? tokenRecord.completion_tokens : undefined);
  const cacheReadTokens = typeof usage.cache_read_tokens === "number" ? usage.cache_read_tokens : (typeof tokenRecord.cache_read_tokens === "number" ? tokenRecord.cache_read_tokens : undefined);
  const rawCost = typeof usage.cost_usd === "number" ? usage.cost_usd : (typeof tokenRecord.cost_usd === "number" ? tokenRecord.cost_usd : undefined);
  const costUsd = typeof rawCost === "number" ? (rawCost > 100 ? rawCost / 1_000_000 : rawCost) : null;
  const fromLedger = typeof promptTokens !== "number" && ledger && ledger.calls > 0 ? ledger : null;
  const cacheRatio =
    typeof usage.cache_hit_ratio === "number"
      ? Math.round(usage.cache_hit_ratio * 100)
      : promptTokens && cacheReadTokens
      ? Math.round((cacheReadTokens / promptTokens) * 100)
      : null;
  const calls = Array.isArray(tokenRecord.calls) ? (tokenRecord.calls as Array<Record<string, unknown>>) : [];

  const handleFork = async () => {
    setIsForking(true);
    try {
      const res = await fetch(`/v1/sessions/${sessionId}/fork`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ up_to_turn: turn.turn_index }),
      });
      if (res.ok) {
        const data = await res.json();
        if (data?.session_id) {
          window.location.href = `/chat/${data.session_id}`;
        }
      }
    } catch (err) {
      console.error("Fork failed", err);
    } finally {
      setIsForking(false);
    }
  };

  const handleRewind = async () => {
    if (
      !(await confirm({
        title: `Rewind to turn #${turn.turn_index}?`,
        message: "The session and its workspace go back to that turn. Later turns and commits are truncated.",
        confirmLabel: "Rewind",
        tone: "danger",
      }))
    ) {
      return;
    }
    setIsRewinding(true);
    setRewindError(null);
    try {
      const res = await fetch(`/v1/sessions/${sessionId}/rewind`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ target_turn: turn.turn_index }),
      });
      if (!res.ok) {
        const detail = ((await res.json().catch(() => ({}))) as { detail?: unknown }).detail;
        throw new Error(typeof detail === "string" ? detail : `The server answered ${res.status}.`);
      }
      // The rewound transcript replaces the one on screen, without reloading the page.
      const session = await fetch(`/v1/sessions/${sessionId}`).then((r) => (r.ok ? r.json() : null));
      if (session && session.session_id === sessionId) useMissionStore.getState().setSession(session);
      onRewound?.();
    } catch (err) {
      setRewindError(err instanceof Error ? err.message : "The rewind failed.");
    } finally {
      setIsRewinding(false);
    }
  };

  return (
    <li
      data-testid="turn-inspector-item"
      data-turn-status={turn.status}
      className="rounded-xl border border-surface-border bg-surface-card/60 p-3 space-y-2"
    >
      {confirmDialog}
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2 min-w-0">
          <span className="text-[11px] font-mono text-neutral-500">
            #{String(turn.turn_index).padStart(2, "0")}
          </span>
          <span
            data-testid="turn-status"
            className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full border text-[10px] font-mono uppercase tracking-wide ${statusClass}`}
          >
            {STATUS_ICONS[turn.status]}
            {turn.status}
          </span>
          {(turn.resume_attempt ?? 0) > 0 && (
            <span
              data-testid="turn-resumed"
              title="The server restarted mid-task; the agent continued from the saved conversation."
              className="text-[10px] font-mono uppercase tracking-wide text-sky-300"
            >
              resumed
            </span>
          )}
        </div>
        <span className="text-[10px] font-mono text-neutral-500 shrink-0">
          {formatEpoch(turn.started_at)} · {formatDuration(turn.started_at, turn.ended_at)}
        </span>
      </div>

      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[10px] font-mono text-neutral-500">
        <span className="inline-flex items-center gap-1">
          <Wrench className="w-3 h-3" />
          {toolCount} {toolCount === 1 ? "tool" : "tools"}
        </span>
        <span>{turn.diff_paths.length} files</span>
        {approvalCount > 0 && (
          <span className="inline-flex items-center gap-1 text-amber-700 dark:text-amber-300">
            <ShieldCheck className="w-3 h-3" />
            {approvalCount} {approvalCount === 1 ? "approval" : "approvals"}
          </span>
        )}
        {fromLedger && (
          <span
            data-testid="turn-live-usage"
            className="inline-flex items-center gap-1.5"
            title="Counted call by call while the turn runs"
          >
            <span>{fromLedger.prompt_tokens.toLocaleString()} in</span>
            <span>{fromLedger.completion_tokens.toLocaleString()} out</span>
            <span className="text-emerald-400 font-medium">${fromLedger.cost_usd.toFixed(4)}</span>
          </span>
        )}
        {typeof promptTokens === "number" && (
          <span className="inline-flex items-center gap-1" title="Prompt tokens (cached / total)">
            <span>{promptTokens} in</span>
            {typeof cacheRatio === "number" && cacheRatio > 0 && (
              <span className="px-1 py-0.2 rounded bg-emerald-500/10 text-emerald-400 border border-emerald-500/20 text-[9px]">
                {cacheRatio}% cached
              </span>
            )}
          </span>
        )}
        {typeof completionTokens === "number" && <span>{completionTokens} out</span>}
        {typeof costUsd === "number" && costUsd > 0 && (
          <span className="text-emerald-400 font-medium">${costUsd.toFixed(4)}</span>
        )}
        {typeof turn.tokens?.total_tokens === "number" && typeof promptTokens !== "number" && (
          <span>{turn.tokens.total_tokens as number} tok</span>
        )}
      </div>

      {turn.tool_calls.length > 0 && (
        <ul className="flex flex-wrap gap-1.5">
          {turn.tool_calls.map((tool, index) => (
            <li
              key={`${turn.id}-tool-${index}`}
              className="px-2 py-0.5 rounded-md bg-surface-elevated border border-surface-border text-[10px] font-mono text-neutral-400"
            >
              {typeof tool?.name === "string" ? tool.name : "tool"}
            </li>
          ))}
        </ul>
      )}

      {calls.length > 0 && (
        <div className="pt-0.5">
          <button
            type="button"
            data-testid="turn-calls-toggle"
            aria-expanded={isCallsOpen}
            onClick={() => setIsCallsOpen((open) => !open)}
            className="inline-flex items-center gap-1 text-[10px] font-mono text-neutral-400 hover:text-neutral-200 transition-colors cursor-pointer"
          >
            {isCallsOpen ? <ChevronDown className="w-3 h-3" /> : <ChevronRight className="w-3 h-3" />}
            <span>Model calls ({calls.length})</span>
          </button>
          {isCallsOpen && (
            <div className="mt-1.5 space-y-1.5 pl-3 border-l-2 border-surface-border/80">
              {calls.map((call, idx) => (
                <div
                  key={idx}
                  className="p-2 rounded-lg bg-surface-elevated/70 border border-surface-border text-[10px] font-mono space-y-1"
                >
                  <div className="flex items-center justify-between gap-2">
                    <span className="font-semibold text-neutral-300 truncate">
                      {String(call.model || "default")}
                    </span>
                    <span className="text-neutral-500 uppercase">{String(call.finish_reason || "stop")}</span>
                  </div>
                  <div className="flex flex-wrap gap-x-3 gap-y-0.5 text-neutral-400">
                    <span>Prompt: {Number(call.prompt_tokens ?? 0)}</span>
                    <span>Cached: {Number(call.cache_read_tokens ?? 0)}</span>
                    <span>Output: {Number(call.completion_tokens ?? 0)}</span>
                    {typeof call.cost_usd === "number" && call.cost_usd > 0 && (
                      <span className="text-emerald-400">${call.cost_usd.toFixed(4)}</span>
                    )}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {turn.attachments && turn.attachments.length > 0 && (
        <ul data-testid="turn-attachments" className="flex flex-wrap gap-1.5" aria-label="Attached files">
          {turn.attachments.map((attachment, index) => (
            <li
              key={`${turn.id}-att-${attachment.id ?? index}`}
              className="inline-flex max-w-full items-center gap-1.5 rounded-md border border-surface-border bg-surface-elevated px-2 py-0.5 text-[11px] text-neutral-600 dark:text-neutral-300"
            >
              <Paperclip className="h-3 w-3 shrink-0" aria-hidden="true" />
              <span className="truncate">{attachment.name}</span>
              <span className="shrink-0 text-neutral-500">{formatBytes(attachment.size)}</span>
            </li>
          ))}
        </ul>
      )}

      {turn.diff_paths.length > 0 && (
        <ul className="space-y-0.5">
          {turn.diff_paths.map((path) => (
            <li key={`${turn.id}-${path}`} className="text-[10px] font-mono text-neutral-500 truncate">
              {path}
            </li>
          ))}
        </ul>
      )}

      {turn.error && (
        <p className="text-[11px] text-rose-700 dark:text-rose-300 font-sans">{turn.error}</p>
      )}

      {turn.reasoning_summary && (
        <div>
          <button
            type="button"
            data-testid="turn-reasoning-toggle"
            aria-expanded={isReasoningOpen}
            onClick={() => setIsReasoningOpen((open) => !open)}
            className="inline-flex items-center gap-1.5 text-[11px] text-neutral-400 hover:text-neutral-200 transition-colors cursor-pointer"
          >
            {isReasoningOpen ? <ChevronDown className="w-3 h-3" /> : <ChevronRight className="w-3 h-3" />}
            Reasoning summary
          </button>
          {isReasoningOpen && (
            <p className="mt-1.5 pl-4 border-l-2 border-surface-border/80 text-[11px] text-neutral-400 font-sans leading-relaxed">
              {turn.reasoning_summary}
            </p>
          )}
        </div>
      )}

      <div className="flex items-center gap-2 pt-1.5 border-t border-surface-border/40">
        <button
          type="button"
          data-testid={`turn-fork-${turn.turn_index}`}
          disabled={isForking}
          onClick={handleFork}
          className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded text-[10px] font-medium bg-surface-elevated hover:bg-surface-border/60 border border-surface-border text-neutral-300 transition-colors cursor-pointer"
          title="Create a new session branched from this turn"
        >
          <GitFork className="w-3 h-3 text-brand" />
          <span>{isForking ? "Forking..." : "Fork from here"}</span>
        </button>

        <button
          type="button"
          data-testid={`turn-rewind-${turn.turn_index}`}
          disabled={isRewinding}
          onClick={handleRewind}
          className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded text-[10px] font-medium bg-surface-elevated hover:bg-amber-500/20 hover:border-amber-500/40 border border-surface-border text-neutral-400 hover:text-amber-200 transition-colors cursor-pointer"
          title="Rewind this session and workspace back to this turn"
        >
          <RotateCcw className="w-3 h-3 text-amber-400" />
          <span>{isRewinding ? "Rewinding..." : "Rewind to here"}</span>
        </button>
        {rewindError && (
          <span role="alert" data-testid={`turn-rewind-error-${turn.turn_index}`} className="text-[10px] text-rose-600 dark:text-rose-400">
            Rewind failed: {rewindError}
          </span>
        )}

        {canRetry && (
          <button
            type="button"
            data-testid="turn-retry"
            disabled={!retryPrompt}
            title={retryPrompt ? "Re-run this interrupted turn" : "No prior prompt available to retry"}
            onClick={() => retryPrompt && onRetry(retryPrompt)}
            className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded text-[10px] font-medium transition-colors ${
              retryPrompt
                ? "bg-amber-500/15 hover:bg-amber-500/25 border border-amber-500/40 text-amber-200 cursor-pointer"
                : "bg-surface-elevated border border-surface-border text-neutral-500 cursor-not-allowed"
            }`}
          >
            <RotateCcw className="w-3 h-3" />
            Retry turn
          </button>
        )}
      </div>
    </li>
  );
};

/**
 * Cockpit turn inspector.
 *
 * Lists the durable traces persisted for the active session and surfaces an explicit
 * retry affordance for a turn recovery marked `interrupted` instead of letting
 * the turn vanish with the socket. The API reports the state; the cockpit only offers
 * the action.
 */
export const TurnInspector: React.FC<TurnInspectorProps> = ({ onRetryTurn }) => {
  const isOpen = useMissionStore((s) => s.isTurnInspectorOpen);
  const setIsOpen = useMissionStore((s) => s.setIsTurnInspectorOpen);
  const session = useMissionStore((s) => s.session);
  const sessionId = session && !session.is_draft ? session.session_id : null;
  const isExecuting = useMissionStore((s) => s.isExecuting);
  const usage = useSessionUsage(sessionId ?? undefined, isExecuting);

  const [turns, setTurns] = useState<TurnTrace[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const loadTurns = useCallback(async () => {
    if (!sessionId) {
      setTurns([]);
      return;
    }
    setIsLoading(true);
    setError(null);
    try {
      const res = await fetch(`/v1/sessions/${sessionId}/turns?limit=50`, {
        signal: AbortSignal.timeout(8000),
      });
      if (!res.ok) {
        setTurns([]);
        setError(
          res.status === 503
            ? "Turn traces require the PostgreSQL-backed session store."
            : `Failed to load turns (HTTP ${res.status}).`
        );
        return;
      }
      const page = (await res.json()) as TurnTracePage;
      setTurns(Array.isArray(page.turns) ? page.turns : []);
    } catch {
      setTurns([]);
      setError("Could not reach the turn-trace API.");
    } finally {
      setIsLoading(false);
    }
  }, [sessionId]);

  useEffect(() => {
    if (isOpen) void loadTurns();
  }, [isOpen, loadTurns]);

  if (!isOpen) return null;

  const retryPrompt =
    [...(session?.conversation_history ?? [])]
      .reverse()
      .find((message) => message.role?.toLowerCase() === "user")?.content ?? null;

  return (
    <aside
      role="dialog"
      aria-label="Turn inspector"
      data-testid="turn-inspector"
      className="fixed inset-y-0 right-0 z-40 w-full sm:w-[26rem] bg-surface-base border-l border-surface-border shadow-2xl flex flex-col animate-in slide-in-from-right duration-200"
    >
      <header className="h-14 shrink-0 px-4 border-b border-surface-border flex items-center justify-between select-none">
        <div className="flex items-center gap-2">
          <History className="w-4 h-4 text-brand" />
          <span className="text-xs font-mono uppercase tracking-wider text-foreground">
            Turn Inspector
          </span>
          {sessionId && (
            <span className="text-[10px] font-mono text-neutral-500 truncate max-w-[10rem]">
              {sessionId}
            </span>
          )}
        </div>
        <div className="flex items-center gap-1">
          <button
            type="button"
            onClick={() => void loadTurns()}
            title="Refresh turns"
            aria-label="Refresh turns"
            className="p-1.5 rounded-md hover:bg-surface-elevated text-neutral-400 hover:text-foreground cursor-pointer"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${isLoading ? "animate-spin" : ""}`} />
          </button>
          <button
            type="button"
            onClick={() => setIsOpen(false)}
            title="Close turn inspector"
            aria-label="Close turn inspector"
            className="p-1.5 rounded-md hover:bg-surface-elevated text-neutral-400 hover:text-foreground cursor-pointer"
          >
            <X className="w-4 h-4" />
          </button>
        </div>
      </header>

      <div className="flex-1 overflow-y-auto p-4 space-y-3">
        {sessionId && <CheckpointsPanel sessionId={sessionId} />}
        {isLoading && turns.length === 0 && (
          <p className="text-xs text-neutral-500 font-mono">Loading turns…</p>
        )}
        {error && (
          <p className="text-xs text-amber-300 font-sans">{error}</p>
        )}
        {!isLoading && !error && turns.length === 0 && (
          <p className="text-xs text-neutral-500 font-mono">No turns recorded for this session yet.</p>
        )}
        {turns.length > 0 && (
          <ul className="space-y-3">
            {turns.map((turn) => (
              <TurnRow
                key={turn.id}
                turn={turn}
                ledger={usage?.turns.find((t) => t.turn_index === turn.turn_index)}
                sessionId={sessionId || ""}
                retryPrompt={retryPrompt}
                onRetry={onRetryTurn}
                onRewound={() => void loadTurns()}
              />
            ))}
          </ul>
        )}
      </div>
    </aside>
  );
};

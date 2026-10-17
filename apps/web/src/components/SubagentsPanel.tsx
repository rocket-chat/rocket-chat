"use client";

import React, { useCallback, useEffect, useState } from "react";
import { useMissionStore } from "../lib/store";
import { MarkdownRenderer } from "./MarkdownRenderer";
import {
  AlertTriangle,
  Bot,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Clock,
  Coins,
  FileCode,
  Loader2,
  RefreshCw,
} from "lucide-react";

interface SubagentSummary {
  id: string;
  session_id: string;
  subagent_id: string;
  role: string;
  task: string;
  summary: string;
  status: string;
  turns_taken: number;
  tool_calls_count: number;
  duration_ms: number;
  tokens: number;
  cost_usd: number;
  changed_files: string[];
  artefacts: string[];
  created_at: number;
}

interface SubagentDetail extends SubagentSummary {
  messages: Array<{ role: string; content?: string; reasoning?: string }>;
}

export const SubagentsPanel: React.FC = () => {
  const session = useMissionStore((s) => s.session);
  const sessionId = session && !session.is_draft ? session.session_id : null;

  const [subagents, setSubagents] = useState<SubagentSummary[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [selectedDetail, setSelectedDetail] = useState<SubagentDetail | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [isDetailLoading, setIsDetailLoading] = useState(false);

  const loadSubagents = useCallback(async () => {
    if (!sessionId) {
      setSubagents([]);
      return;
    }
    setIsLoading(true);
    try {
      const res = await fetch(`/v1/sessions/${sessionId}/subagents`, {
        signal: AbortSignal.timeout(8000),
      });
      if (res.ok) {
        const data = await res.json();
        setSubagents(Array.isArray(data) ? data : []);
      }
    } catch {
      // offline or error fallback
    } finally {
      setIsLoading(false);
    }
  }, [sessionId]);

  const flightLog = useMissionStore((s) => s.flightLog);
  const completedSubagentsCount = flightLog.filter(
    (e) => e.subagent && (e.subagent.status === "completed" || e.subagent.status === "error")
  ).length;

  useEffect(() => {
    void loadSubagents();
  }, [loadSubagents, completedSubagentsCount]);

  const loadDetail = async (subagentId: string) => {
    if (!sessionId) return;
    if (selectedId === subagentId) {
      setSelectedId(null);
      setSelectedDetail(null);
      return;
    }
    setSelectedId(subagentId);
    setIsDetailLoading(true);
    try {
      const res = await fetch(`/v1/sessions/${sessionId}/subagents/${subagentId}`, {
        signal: AbortSignal.timeout(8000),
      });
      if (res.ok) {
        const detail = await res.json();
        setSelectedDetail(detail);
      }
    } catch {
      setSelectedDetail(null);
    } finally {
      setIsDetailLoading(false);
    }
  };

  return (
    <div data-testid="subagents-panel" className="h-full flex flex-col p-4 space-y-4 overflow-y-auto">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Bot className="w-4 h-4 text-brand" />
          <h3 className="text-xs font-semibold uppercase tracking-wider text-neutral-300">
            Delegated Subagents ({subagents.length})
          </h3>
        </div>
        <button
          type="button"
          onClick={() => void loadSubagents()}
          disabled={isLoading}
          className="p-1 rounded hover:bg-surface-elevated text-neutral-400 hover:text-neutral-200"
          title="Refresh subagent transcripts"
        >
          <RefreshCw className={`w-3.5 h-3.5 ${isLoading ? "animate-spin" : ""}`} />
        </button>
      </div>

      {subagents.length === 0 && !isLoading && (
        <div className="text-center py-8 text-neutral-500 text-xs">
          No specialized subagents have been delegated in this session yet.
        </div>
      )}

      <div className="space-y-3">
        {subagents.map((sub) => {
          const isSelected = selectedId === sub.subagent_id;
          const isSuccess = sub.status === "completed";

          return (
            <div
              key={sub.id}
              data-testid="subagent-item"
              className="rounded-xl border border-surface-border bg-surface-card/60 p-3.5 space-y-2.5 transition-colors"
            >
              <div className="flex items-start justify-between gap-2">
                <div className="space-y-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="font-semibold text-xs text-foreground uppercase tracking-wide">
                      {sub.role}
                    </span>
                    <span
                      className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full border text-[10px] font-mono uppercase ${
                        isSuccess
                          ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-300"
                          : "border-amber-500/30 bg-amber-500/10 text-amber-300"
                      }`}
                    >
                      {isSuccess ? <CheckCircle2 className="w-2.5 h-2.5" /> : <AlertTriangle className="w-2.5 h-2.5" />}
                      {sub.status}
                    </span>
                  </div>
                  <p className="text-xs text-neutral-400 line-clamp-2">{sub.task}</p>
                </div>

                <button
                  type="button"
                  data-testid="subagent-transcript-toggle"
                  onClick={() => void loadDetail(sub.subagent_id)}
                  className="p-1 text-neutral-400 hover:text-neutral-200 cursor-pointer"
                  title="Toggle transcript"
                >
                  {isSelected ? <ChevronDown className="w-4 h-4" /> : <ChevronRight className="w-4 h-4" />}
                </button>
              </div>

              {sub.summary && (
                <div className="rounded-lg border border-surface-border bg-surface-base/80 p-2.5 text-xs text-neutral-300">
                  <MarkdownRenderer content={sub.summary} />
                </div>
              )}

              {sub.changed_files.length > 0 && (
                <div className="flex flex-wrap gap-1 font-mono text-[10px]">
                  {sub.changed_files.map((file, idx) => (
                    <span key={idx} className="inline-flex items-center gap-1 px-2 py-0.5 rounded bg-surface-elevated border border-surface-border text-neutral-400">
                      <FileCode className="w-2.5 h-2.5" />
                      {file}
                    </span>
                  ))}
                </div>
              )}

              <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[10px] font-mono text-neutral-500 pt-1 border-t border-surface-border/50">
                <span className="inline-flex items-center gap-1">
                  <Clock className="w-3 h-3" />
                  {sub.duration_ms}ms
                </span>
                <span>{sub.turns_taken} turns</span>
                <span>{sub.tool_calls_count} tools</span>
                {sub.tokens > 0 && <span>{sub.tokens} tokens</span>}
                {sub.cost_usd > 0 && (
                  <span className="inline-flex items-center gap-0.5 text-emerald-400">
                    <Coins className="w-3 h-3" />
                    ${sub.cost_usd.toFixed(4)}
                  </span>
                )}
              </div>

              {isSelected && (
                <div className="pt-2 border-t border-surface-border space-y-2">
                  <h4 className="text-[11px] font-mono uppercase tracking-wider text-neutral-400">
                    Execution Transcript
                  </h4>
                  {isDetailLoading ? (
                    <div className="flex items-center gap-2 py-3 text-xs text-neutral-500">
                      <Loader2 className="w-3.5 h-3.5 animate-spin text-brand" />
                      Loading subagent transcript...
                    </div>
                  ) : (
                    <div className="space-y-2 max-h-60 overflow-y-auto">
                      {(selectedDetail?.messages || []).map((msg, i) => (
                        <div
                          key={i}
                          className={`p-2 rounded-lg text-xs ${
                            msg.role === "user"
                              ? "bg-surface-elevated/70 border border-surface-border text-neutral-300"
                              : "bg-surface-card/80 text-foreground"
                          }`}
                        >
                          <div className="font-mono text-[10px] text-neutral-500 uppercase mb-1">
                            {msg.role}
                          </div>
                          <div className="leading-relaxed">
                            <MarkdownRenderer content={msg.content || ""} />
                          </div>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
};

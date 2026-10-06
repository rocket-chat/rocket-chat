"use client";

import React, { useState } from "react";
import { SubagentExecutionData } from "../types/mission";
import { MarkdownRenderer } from "./MarkdownRenderer";
import {
  Shield,
  CheckCircle2,
  Search,
  Bot,
  AlertCircle,
  Loader2,
  ChevronDown,
  ChevronRight,
  Clock,
  Sparkles,
} from "lucide-react";

interface SubagentExecutionCardProps {
  subagent: SubagentExecutionData;
}

export const SubagentExecutionCard: React.FC<SubagentExecutionCardProps> = ({ subagent }) => {
  const [isExpanded, setIsExpanded] = useState<boolean>(false);

  const getSubagentIcon = () => {
    const roleLower = (subagent.role || "").toLowerCase();
    if (roleLower.includes("security") || roleLower.includes("audit")) {
      return <Shield className="w-3.5 h-3.5 text-amber-400" />;
    }
    if (roleLower.includes("qa") || roleLower.includes("test") || roleLower.includes("verif")) {
      return <CheckCircle2 className="w-3.5 h-3.5 text-emerald-400" />;
    }
    if (roleLower.includes("research") || roleLower.includes("search") || roleLower.includes("doc")) {
      return <Search className="w-3.5 h-3.5 text-sky-400" />;
    }
    return <Bot className="w-3.5 h-3.5 text-brand" />;
  };

  const roleFormatted = subagent.role
    .split("_")
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(" ");

  return (
    <div
      className={`my-2 rounded-lg border transition-all text-xs font-mono select-text overflow-hidden ${
        subagent.status === "error"
          ? "border-diff-del/50 bg-diff-del/5"
          : subagent.status === "running"
          ? "border-brand/40 bg-brand/5 shadow-sm shadow-brand/10"
          : "border-surface-border bg-surface-card/60 hover:border-brand/40"
      }`}
    >
      {/* Header Bar */}
      <div
        onClick={() => setIsExpanded(!isExpanded)}
        className="flex items-center justify-between px-3 py-2 cursor-pointer bg-surface-elevated/40 hover:bg-surface-elevated/70 transition-colors select-none"
      >
        <div className="flex items-center gap-2 min-w-0">
          <div className="shrink-0 p-1 rounded bg-surface-base border border-surface-border">
            {getSubagentIcon()}
          </div>
          <div className="flex items-center gap-2 min-w-0">
            <span className="font-bold text-foreground tracking-wide shrink-0">
              {roleFormatted}
            </span>
            <span className="text-neutral-400 text-[11px] truncate hidden sm:inline max-w-[280px]">
              — {subagent.task}
            </span>
          </div>

          {/* Status Badge */}
          {subagent.status === "running" && (
            <span className="flex items-center gap-1 px-1.5 py-0.5 rounded bg-brand/15 border border-brand/30 text-brand text-[10px] shrink-0 font-sans">
              <Loader2 className="w-2.5 h-2.5 animate-spin" />
              DELEGATING
            </span>
          )}
          {subagent.status === "completed" && (
            <span className="flex items-center gap-1 px-1.5 py-0.5 rounded bg-emerald-500/15 border border-emerald-500/30 text-emerald-400 text-[10px] shrink-0 font-sans">
              <CheckCircle2 className="w-2.5 h-2.5" />
              DONE
            </span>
          )}
          {subagent.status === "error" && (
            <span className="flex items-center gap-1 px-1.5 py-0.5 rounded bg-diff-del/15 border border-diff-del/30 text-diff-del text-[10px] shrink-0 font-sans">
              <AlertCircle className="w-2.5 h-2.5" />
              FAILED
            </span>
          )}
        </div>

        <div className="flex items-center gap-2 text-neutral-500 shrink-0">
          {subagent.duration_ms && (
            <span className="hidden sm:flex items-center gap-1 text-[10px] font-mono">
              <Clock className="w-2.5 h-2.5" />
              {subagent.duration_ms}ms
            </span>
          )}
          {isExpanded ? (
            <ChevronDown className="w-3.5 h-3.5 text-neutral-400" />
          ) : (
            <ChevronRight className="w-3.5 h-3.5 text-neutral-400" />
          )}
        </div>
      </div>

      {/* Expanded Subagent Traces and Summary */}
      {isExpanded && (
        <div className="p-3 border-t border-surface-border space-y-3 bg-surface-base/80 text-[11.5px] font-sans">
          {/* Subagent Task Description */}
          <div className="space-y-1">
            <span className="text-[10px] font-mono font-semibold text-neutral-500 uppercase tracking-wider">
              Delegated Goal
            </span>
            <p className="text-neutral-300 font-mono text-[11px] bg-surface-card px-2.5 py-1.5 rounded border border-surface-border">
              {subagent.task}
            </p>
          </div>

          {/* Intermediate Reasoning / Turns if running */}
          {subagent.thought && (
            <div className="space-y-1">
              <span className="text-[10px] font-mono font-semibold text-neutral-500 uppercase tracking-wider flex items-center gap-1">
                <Sparkles className="w-3 h-3 text-brand" />
                Specialist Reasoning Trace
              </span>
              <div className="text-neutral-400 font-sans text-xs pl-2 border-l-2 border-brand/40">
                <MarkdownRenderer content={subagent.thought} />
              </div>
            </div>
          )}

          {/* Synthesized Output Result */}
          {subagent.summary ? (
            <div className="space-y-1">
              <span className="text-[10px] font-mono font-semibold text-emerald-400 uppercase tracking-wider">
                Synthesized Specialist Findings
              </span>
              <div className="text-neutral-200 font-sans text-xs bg-surface-card p-3 rounded-md border border-surface-border leading-relaxed">
                <MarkdownRenderer content={subagent.summary} />
              </div>
            </div>
          ) : subagent.status === "running" ? (
            <div className="flex items-center gap-2 text-neutral-400 text-xs italic">
              <Loader2 className="w-3 h-3 animate-spin text-brand" />
              Executing autonomous turns in isolated context...
            </div>
          ) : null}
        </div>
      )}
    </div>
  );
};

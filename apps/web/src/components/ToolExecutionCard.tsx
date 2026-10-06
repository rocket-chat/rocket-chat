"use client";

import React, { useState } from "react";
import { ToolCallData } from "../types/mission";
import {
  Terminal,
  FileCode,
  Wrench,
  Search,
  CheckCircle2,
  AlertCircle,
  Loader2,
  ChevronDown,
  ChevronRight,
  Copy,
  Check,
  Clock,
  Code2,
  FileText,
} from "lucide-react";

interface ToolExecutionCardProps {
  toolCall: ToolCallData;
}

export const ToolExecutionCard: React.FC<ToolExecutionCardProps> = ({ toolCall }) => {
  const [isExpanded, setIsExpanded] = useState<boolean>(false);
  const [activeTab, setActiveTab] = useState<"params" | "output">("output");
  const [copied, setCopied] = useState<boolean>(false);

  const isBash =
    toolCall.name === "bash_exec" ||
    toolCall.name === "bash" ||
    toolCall.name === "exec_command";

  const getToolIcon = () => {
    if (isBash) return <Terminal className="w-3.5 h-3.5 text-amber" />;
    if (toolCall.name.includes("file") || toolCall.name.includes("read") || toolCall.name.includes("write")) {
      return <FileCode className="w-3.5 h-3.5 text-cyan" />;
    }
    if (toolCall.name.includes("search") || toolCall.name.includes("find")) {
      return <Search className="w-3.5 h-3.5 text-violet" />;
    }
    return <Wrench className="w-3.5 h-3.5 text-foreground/80" />;
  };

  const copyToClipboard = (text: string) => {
    navigator.clipboard.writeText(text);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const argsFormatted = toolCall.arguments
    ? JSON.stringify(toolCall.arguments, null, 2)
    : "{}";

  const bashCommand =
    isBash && toolCall.arguments
      ? (toolCall.arguments.command as string) || (toolCall.arguments.cmd as string) || ""
      : null;

  const outputFormatted = toolCall.result || toolCall.stdout || "(Awaiting execution output...)";

  return (
    <div
      className={`my-2.5 rounded-lg border transition-all text-xs font-mono select-text overflow-hidden ${
        toolCall.status === "error"
          ? "border-diff-del/50 bg-diff-del/5"
          : toolCall.status === "pending"
          ? "border-amber/40 bg-amber/5 animate-pulse"
          : "border-border/80 bg-surface/70 hover:border-cyan/40"
      }`}
    >
      {/* Header Bar */}
      <div
        onClick={() => setIsExpanded(!isExpanded)}
        className="flex items-center justify-between px-3 py-2 cursor-pointer bg-elevated/60 hover:bg-elevated/90 transition-colors select-none"
      >
        <div className="flex items-center gap-2">
          {getToolIcon()}
          <span className="font-bold text-foreground tracking-wide">
            {toolCall.name}
          </span>

          {/* Status Badge */}
          {toolCall.status === "pending" && (
            <span className="flex items-center gap-1 px-1.5 py-0.5 rounded bg-amber/15 border border-amber/30 text-amber text-[10px]">
              <Loader2 className="w-2.5 h-2.5 animate-spin" />
              RUNNING
            </span>
          )}
          {toolCall.status === "success" && (
            <span className="flex items-center gap-1 px-1.5 py-0.5 rounded bg-diff-add/15 border border-diff-add/30 text-diff-add text-[10px]">
              <CheckCircle2 className="w-2.5 h-2.5" />
              SUCCESS
            </span>
          )}
          {toolCall.status === "error" && (
            <span className="flex items-center gap-1 px-1.5 py-0.5 rounded bg-diff-del/15 border border-diff-del/30 text-diff-del text-[10px]">
              <AlertCircle className="w-2.5 h-2.5" />
              FAILED
            </span>
          )}

          {/* Duration Badge */}
          {toolCall.duration_ms !== undefined && (
            <span className="flex items-center gap-1 text-[10px] text-muted-foreground">
              <Clock className="w-2.5 h-2.5" />
              {toolCall.duration_ms}ms
            </span>
          )}
        </div>

        <div className="flex items-center gap-2">
          {isExpanded ? (
            <ChevronDown className="w-4 h-4 text-muted-foreground" />
          ) : (
            <ChevronRight className="w-4 h-4 text-muted-foreground" />
          )}
        </div>
      </div>

      {/* Expanded Details */}
      {isExpanded && (
        <div className="p-3 border-t border-border/60 bg-void/50 space-y-2">
          {/* If Bash: Render clear inline shell prompt */}
          {bashCommand && (
            <div className="rounded border border-border/80 bg-void p-2.5 flex items-center justify-between text-[11px]">
              <div className="flex items-center gap-2 overflow-x-auto">
                <span className="text-amber font-bold">$</span>
                <span className="text-foreground/90 font-medium select-all">{bashCommand}</span>
              </div>
              <button
                onClick={(e) => {
                  e.stopPropagation();
                  copyToClipboard(bashCommand);
                }}
                title="Copy Command"
                className="text-muted-foreground hover:text-cyan transition-colors p-1"
              >
                {copied ? <Check className="w-3.5 h-3.5 text-diff-add" /> : <Copy className="w-3.5 h-3.5" />}
              </button>
            </div>
          )}

          {/* Tab Bar: Parameters vs Output */}
          <div className="flex items-center justify-between border-b border-border/50 pb-1 text-[11px]">
            <div className="flex items-center gap-2">
              <button
                onClick={() => setActiveTab("output")}
                className={`flex items-center gap-1 pb-1 transition-colors ${
                  activeTab === "output"
                    ? "border-b-2 border-cyan text-cyan font-bold"
                    : "text-muted-foreground hover:text-foreground"
                }`}
              >
                <FileText className="w-3 h-3" />
                Response / Output
              </button>
              <button
                onClick={() => setActiveTab("params")}
                className={`flex items-center gap-1 pb-1 transition-colors ${
                  activeTab === "params"
                    ? "border-b-2 border-cyan text-cyan font-bold"
                    : "text-muted-foreground hover:text-foreground"
                }`}
              >
                <Code2 className="w-3 h-3" />
                Request Parameters
              </button>
            </div>

            <button
              onClick={() => copyToClipboard(activeTab === "params" ? argsFormatted : outputFormatted)}
              className="flex items-center gap-1 text-[10px] text-muted-foreground hover:text-cyan transition-colors"
            >
              {copied ? <Check className="w-3 h-3 text-diff-add" /> : <Copy className="w-3 h-3" />}
              <span>{copied ? "Copied" : "Copy"}</span>
            </button>
          </div>

          {/* Tab Content */}
          {activeTab === "params" ? (
            <pre className="p-2.5 rounded bg-void border border-border/60 text-cyan/90 text-[11px] overflow-x-auto max-h-48 whitespace-pre-wrap leading-relaxed">
              {argsFormatted}
            </pre>
          ) : (
            <div className="rounded bg-void border border-border/60 p-2.5 max-h-60 overflow-y-auto">
              <pre
                className={`text-[11px] whitespace-pre-wrap leading-relaxed ${
                  toolCall.status === "error" ? "text-diff-del/90" : "text-foreground/85"
                }`}
              >
                {outputFormatted}
              </pre>
            </div>
          )}
        </div>
      )}
    </div>
  );
};

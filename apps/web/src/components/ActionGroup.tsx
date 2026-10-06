"use client";

import React, { useState } from "react";
import { FlightLogEntry } from "../types/mission";
import { ToolExecutionCard } from "./ToolExecutionCard";
import { SubagentExecutionCard } from "./SubagentExecutionCard";
import { MarkdownRenderer } from "./MarkdownRenderer";
import { ChevronRight, ChevronDown } from "lucide-react";

interface ActionGroupProps {
  id: string;
  entries: FlightLogEntry[];
  isActive?: boolean;
}

export const ActionGroup: React.FC<ActionGroupProps> = ({ entries, isActive = false }) => {
  // Always collapsed by default for discreet appearance
  const [isOpen, setIsOpen] = useState<boolean>(false);

  const toolCount = entries.filter((e) => !!e.toolCall).length;
  const subagentCount = entries.filter((e) => !!e.subagent).length;

  return (
    <div className="my-1.5 font-sans select-none text-xs">
      {/* Super Discreet Collapsed Pill (Expandable when clicked) */}
      <button
        type="button"
        data-testid="action-group-toggle"
        onClick={() => setIsOpen(!isOpen)}
        className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-md hover:bg-surface-elevated/70 text-neutral-400 hover:text-neutral-200 transition-colors cursor-pointer group"
        aria-expanded={isOpen}
      >
        <span className="flex items-center justify-center w-3.5 h-3.5 text-neutral-500 group-hover:text-neutral-300">
          {isOpen ? (
            <ChevronDown className="w-3.5 h-3.5" />
          ) : (
            <ChevronRight className="w-3.5 h-3.5" />
          )}
        </span>
        <span className="text-[12px] font-sans text-neutral-400 group-hover:text-neutral-300">
          {isActive ? (
            <span className="flex items-center gap-1.5 text-brand">
              <span className="w-1.5 h-1.5 rounded-full bg-brand animate-pulse" />
              Reasoning in progress...
            </span>
          ) : (
            <>Thought process ({entries.length} {entries.length === 1 ? "step" : "steps"}{toolCount > 0 ? ` · ${toolCount} ${toolCount === 1 ? "tool" : "tools"}` : ""}{subagentCount > 0 ? ` · ${subagentCount} ${subagentCount === 1 ? "subagent" : "subagents"}` : ""})</>
          )}
        </span>
      </button>

      {/* Expanded Discreet Accordion View with full contents */}
      {isOpen && (
        <div className="mt-2 mb-3 pl-3.5 border-l-2 border-surface-border/80 space-y-3 font-mono text-[11.5px] leading-relaxed animate-in fade-in-50 duration-150">
          {entries.map((entry, idx) => (
            <div key={entry.id} className="space-y-1.5">
              <div className="flex items-center gap-2 text-neutral-400">
                <span className="text-brand font-semibold text-[10px]">
                  {String(idx + 1).padStart(2, "0")}
                </span>
                <span className="text-neutral-300 font-medium text-[11px]">
                  {entry.subagent
                    ? `Subagent Delegation: ${entry.subagent.role}`
                    : entry.toolCall
                    ? `Tool: ${entry.toolCall.name}`
                    : "Reasoning Trace"}
                </span>
              </div>

              {entry.reasoning && (
                <div className="pl-3 text-neutral-400 font-sans text-xs">
                  <MarkdownRenderer content={entry.reasoning} />
                </div>
              )}

              {entry.toolCall && (
                <div className="pl-3">
                  <ToolExecutionCard toolCall={entry.toolCall} />
                </div>
              )}

              {entry.subagent && (
                <div className="pl-3">
                  <SubagentExecutionCard subagent={entry.subagent} />
                </div>
              )}
            </div>
          ))}

          <div className="pt-1.5 text-[10px] font-mono text-neutral-500 flex items-center justify-between">
            <span>{entries.length} reasoning {entries.length === 1 ? "step" : "steps"} executed</span>
            <span className="text-neutral-400">Chain-of-Thought</span>
          </div>
        </div>
      )}
    </div>
  );
};

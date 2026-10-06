"use client";

import React, { useRef, useEffect } from "react";
import { useMissionStore } from "../lib/store";
import { DecisionCard } from "./DecisionCard";
import { ActionGroup } from "./ActionGroup";
import { MarkdownRenderer } from "./MarkdownRenderer";
import { TaskChecklist } from "./TaskChecklist";
import { FlightLogEntry } from "../types/mission";
import { RocketChatIcon } from "./RocketChatLogo";
import { Info } from "lucide-react";

interface FlightLogStreamProps {
  onAnswerQuestion: (questionId: string, selectedOptions: string[], customText?: string) => void;
}

interface ActionGroupData {
  id: string;
  entries: FlightLogEntry[];
  isActive?: boolean;
}

export const FlightLogStream: React.FC<FlightLogStreamProps> = ({
  onAnswerQuestion,
}) => {
  const {
    flightLog,
    activeDecision,
    isExecuting,
    statusMessage,
    activeAgent,
  } = useMissionStore();

  const bottomRef = useRef<HTMLDivElement>(null);

  // Auto-scroll on new entries
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [flightLog, activeDecision, isExecuting, statusMessage]);

  // Aggregate consecutive actions into distinct ActionGroups
  const rawStreamItems: Array<
    | { type: "user"; entry: FlightLogEntry }
    | { type: "system"; entry: FlightLogEntry }
    | { type: "action_group"; data: ActionGroupData }
    | { type: "answer"; entry: FlightLogEntry }
  > = [];

  let currentGroupEntries: FlightLogEntry[] = [];

  const flushGroup = (isActiveGroup = false) => {
    if (currentGroupEntries.length > 0) {
      rawStreamItems.push({
        type: "action_group",
        data: {
          id: `group-${currentGroupEntries[0].id}`,
          entries: [...currentGroupEntries],
          isActive: isActiveGroup,
        },
      });
      currentGroupEntries = [];
    }
  };

  for (const entry of flightLog) {
    if (entry.role === "USER") {
      flushGroup();
      rawStreamItems.push({ type: "user", entry });
    } else if (entry.role === "SYSTEM") {
      flushGroup();
      rawStreamItems.push({ type: "system", entry });
    } else {
      // AGENT role
      const isSubagentInvocation = !!entry.subagent;
      const isToolInvocation =
        !!entry.toolCall ||
        (!!entry.content &&
          (entry.content.startsWith("Invoking tool:") ||
            entry.content.startsWith("Executing tool:") ||
            entry.content.startsWith("Delegating to subagent")));
      const isPureReasoning = !!entry.reasoning && !entry.content;

      if (isSubagentInvocation || isToolInvocation || isPureReasoning) {
        currentGroupEntries.push(entry);
      } else {
        if (entry.reasoning) {
          currentGroupEntries.push({
            ...entry,
            content: "",
          });
        }
        flushGroup();
        rawStreamItems.push({ type: "answer", entry });
      }
    }
  }

  if (currentGroupEntries.length > 0) {
    flushGroup(isExecuting);
  }

  return (
    <div
      data-testid="flight-log-stream"
      className="continuous-flight-stream flex-1 overflow-y-auto px-4 sm:px-6 py-6 space-y-6 font-sans select-text max-w-3xl mx-auto w-full"
    >
      <TaskChecklist />

      {rawStreamItems.map((item) => {
        // USER REQUEST: Simple bubble on the right
        if (item.type === "user") {
          return (
            <div key={item.entry.id} className="flex justify-end my-3">
              <div className="max-w-[85%] bg-surface-card border border-surface-border rounded-2xl rounded-tr-sm px-4 py-3 text-neutral-100 shadow-sm">
                <div className="text-[14.5px] leading-relaxed">
                  <MarkdownRenderer content={item.entry.content} />
                </div>
                <div
                  suppressHydrationWarning
                  className="mt-1.5 text-[10px] font-mono text-neutral-500 text-right"
                >
                  {item.entry.timestamp || "14:02 UTC"}
                </div>
              </div>
            </div>
          );
        }

        // SYSTEM MESSAGE: Clean pill
        if (item.type === "system") {
          return (
            <div key={item.entry.id} className="flex justify-center my-2">
              <div className="inline-flex items-center gap-2 px-3 py-1 rounded-full border border-surface-border bg-surface-card/40 text-neutral-400 text-[11px] font-mono">
                <Info className="w-3.5 h-3.5 text-neutral-400 shrink-0" />
                <span>{item.entry.content}</span>
              </div>
            </div>
          );
        }

        // DISCREET REASONING / TOOL CALLS ACCORDION
        if (item.type === "action_group") {
          return (
            <div key={item.data.id} className="pl-11">
              <ActionGroup
                id={item.data.id}
                entries={item.data.entries}
                isActive={item.data.isActive}
              />
            </div>
          );
        }

        // ASSISTANT ANSWER: Editorial Claude / Stitch style without surrounding borders
        return (
          <div key={item.entry.id} className="flex gap-3.5 items-start my-4">
            {/* Rocket Brand Pill */}
            <div className="w-7 h-7 rounded-lg bg-brand/10 border border-brand/25 flex-shrink-0 flex items-center justify-center text-brand mt-0.5">
              <RocketChatIcon className="w-4 h-4" />
            </div>

            <div className="flex-1 space-y-2.5 min-w-0">
              {/* Header Info */}
              <div className="flex items-center justify-between text-xs text-neutral-400">
                <span className="font-medium text-neutral-200">
                  {activeAgent?.name || "Rocket Chat"}
                </span>
                {item.entry.timestamp && (
                  <span
                    suppressHydrationWarning
                    className="font-mono text-[10px] text-neutral-500"
                  >
                    {item.entry.timestamp}
                  </span>
                )}
              </div>

              {/* Message Content: Clean text without any block/border wrapper */}
              <div className="text-[14.5px] leading-relaxed text-neutral-200 font-sans">
                <MarkdownRenderer content={item.entry.content} />
              </div>
            </div>
          </div>
        );
      })}

      {/* Real-time Processing Indicator */}
      {isExecuting && !activeDecision && (
        <div className="flex gap-3.5 items-start pl-1">
          <div className="w-7 h-7 rounded-lg bg-brand/10 border border-brand/25 flex-shrink-0 flex items-center justify-center text-brand mt-0.5">
            <RocketChatIcon className="w-4 h-4 animate-pulse" />
          </div>
          <div className="flex items-center gap-2 text-xs text-neutral-400 pt-1 font-sans">
            <span className="w-1.5 h-1.5 rounded-full bg-brand animate-ping" />
            <span>{statusMessage || "Synthesizing answer..."}</span>
          </div>
        </div>
      )}

      {/* Decision Card */}
      {activeDecision && (
        <DecisionCard
          decision={activeDecision}
          onSubmit={(selected, text) =>
            onAnswerQuestion(activeDecision.question_id, selected, text)
          }
        />
      )}

      <div ref={bottomRef} />
    </div>
  );
};

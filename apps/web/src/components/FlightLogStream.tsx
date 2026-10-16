"use client";

import React, { useRef, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { useMissionStore } from "../lib/store";
import { useSession } from "./AuthProvider";
import { DecisionCard } from "./DecisionCard";
import { ApprovalCard } from "./ApprovalCard";
import { ActionGroup } from "./ActionGroup";
import { MarkdownRenderer } from "./MarkdownRenderer";
import { TaskChecklist } from "./TaskChecklist";
import { FlightLogEntry, TurnBinding } from "../types/mission";
import { RocketChatIcon } from "./RocketChatLogo";
import { CockpitEmptyState } from "./CockpitEmptyState";
import { ArrowDown, Check, Copy, Info, Zap, FileText, GitFork } from "lucide-react";

interface FlightLogStreamProps {
  onAnswerQuestion: (questionId: string, selectedOptions: string[], customText?: string) => void;
  onApprove?: (actionId: string) => void;
  onReject?: (actionId: string) => void;
}

/** Closer than this to the bottom counts as "at the bottom": the stream keeps following. */
const FOLLOW_THRESHOLD_PX = 80;

interface ActionGroupData {
  id: string;
  entries: FlightLogEntry[];
  isActive?: boolean;
}

export const FlightLogStream: React.FC<FlightLogStreamProps> = ({
  onAnswerQuestion,
  onApprove,
  onReject,
}) => {
  const {
    flightLog,
    activeDecision,
    activeApproval,
    isExecuting,
    statusMessage,
    turnBinding,
    isStreamContinuous,
    session,
    fetchSessions,
  } = useMissionStore();

  const router = useRouter();
  const { data: authSession } = useSession();
  const currentUserId =
    (authSession?.user as unknown as { user_id?: string } | undefined)?.user_id ?? null;
  // Name the author of each message only when more than one person wrote in this session.
  const showAuthors =
    new Set(flightLog.filter((e) => e.role === "USER" && e.authorUserId).map((e) => e.authorUserId))
      .size > 1;
  const [forkingEntryId, setForkingEntryId] = useState<string | null>(null);
  const [copiedEntryId, setCopiedEntryId] = useState<string | null>(null);
  const streamRef = useRef<HTMLDivElement>(null);
  // Follow new content only while the reader is at the bottom; once they scroll up to review
  // something, the stream must stay where they are.
  const followingRef = useRef(true);
  const lastEntryCountRef = useRef(0);
  const [showJumpToLatest, setShowJumpToLatest] = useState(false);

  const handleScroll = () => {
    const stream = streamRef.current;
    if (!stream) return;
    const distance = stream.scrollHeight - stream.scrollTop - stream.clientHeight;
    const atBottom = distance <= FOLLOW_THRESHOLD_PX;
    followingRef.current = atBottom;
    setShowJumpToLatest(!atBottom);
  };

  const jumpToLatest = () => {
    const stream = streamRef.current;
    if (!stream) return;
    followingRef.current = true;
    setShowJumpToLatest(false);
    stream.scrollTo({ top: stream.scrollHeight, behavior: "smooth" });
  };

  const handleCopy = (entryId: string, text: string) => {
    void navigator.clipboard.writeText(text);
    setCopiedEntryId(entryId);
    setTimeout(() => setCopiedEntryId((current) => (current === entryId ? null : current)), 1500);
  };

  // Map each entry to how many user turns had started by the time it was rendered, so
  // "Fork from here" on an assistant answer branches *through* the turn it belongs to.
  const userTurnsByEntry = new Map<string, number>();
  let userTurnsSoFar = 0;
  for (const entry of flightLog) {
    if (entry.role === "USER") userTurnsSoFar += 1;
    userTurnsByEntry.set(entry.id, userTurnsSoFar);
  }

  const handleForkFrom = async (entryId: string) => {
    if (!session || session.is_draft || session.session_id === "draft") return;
    const upToTurn = userTurnsByEntry.get(entryId) ?? 0;
    setForkingEntryId(entryId);
    try {
      const res = await fetch(`/v1/sessions/${session.session_id}/fork`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(upToTurn > 0 ? { up_to_turn: upToTurn } : {}),
        signal: AbortSignal.timeout(10000),
      });
      if (!res.ok) return;
      const forked = (await res.json()) as { session_id: string };
      await fetchSessions();
      router.push(`/chat/${forked.session_id}`);
    } catch {
      // A failed fork leaves the user on the current session so they can retry.
    } finally {
      setForkingEntryId(null);
    }
  };

  // Follow new entries while at the bottom.
  useEffect(() => {
    // An empty conversation shows its starting points from the top; only follow real content.
    if (flightLog.length === 0 && !isExecuting) return;
    // Sending a message always brings you back to the conversation's end.
    const grew = flightLog.length > lastEntryCountRef.current;
    lastEntryCountRef.current = flightLog.length;
    if (grew && flightLog[flightLog.length - 1]?.role === "USER") followingRef.current = true;
    if (!followingRef.current) return;
    // Scroll the stream itself. scrollIntoView would also scroll every ancestor, including the
    // overflow-hidden page root, which pushes the top bar off screen on a phone. The jump is
    // instant: an animated one would fire scroll events that look like the reader leaving.
    const stream = streamRef.current;
    stream?.scrollTo({ top: stream.scrollHeight, behavior: "auto" });
  }, [flightLog, activeDecision, activeApproval, isExecuting, statusMessage]);

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
    <div className="relative flex min-h-0 flex-1 flex-col">
    <div
      ref={streamRef}
      onScroll={handleScroll}
      data-testid="flight-log-stream"
      data-stream-continuous={isStreamContinuous}
      className="continuous-flight-stream flex min-h-0 flex-1 flex-col overflow-y-auto overflow-x-hidden [&>*]:shrink-0 px-4 sm:px-6 py-6 space-y-6 font-sans select-text max-w-3xl mx-auto w-full"
    >
      <TaskChecklist />

      {flightLog.length === 0 && !isExecuting && <CockpitEmptyState />}

      {rawStreamItems.map((item) => {
        // USER REQUEST: Simple bubble on the right
        if (item.type === "user") {
          return (
            <div key={item.entry.id} className="flex justify-end my-3">
              <div
                data-testid="user-message"
                className="min-w-0 max-w-[85%] bg-surface-card border border-surface-border rounded-2xl rounded-tr-sm px-4 py-3 text-foreground shadow-sm [overflow-wrap:anywhere]"
              >
                {showAuthors && item.entry.authorUserId && (
                  <div
                    data-testid="user-message-author"
                    className="mb-1 text-[10px] font-mono uppercase tracking-wide text-neutral-500"
                  >
                    {item.entry.authorUserId === currentUserId
                      ? "You"
                      : item.entry.authorUserId}
                  </div>
                )}
                <div className="text-[14.5px] leading-relaxed">
                  <MarkdownRenderer content={item.entry.content} />
                </div>
                {item.entry.attachments && item.entry.attachments.length > 0 && (
                  <div data-testid="user-attachments" className="mt-2 flex flex-wrap gap-1.5">
                    {item.entry.attachments.map((att) => (
                      <span
                        key={att.attachmentId}
                        className="inline-flex items-center gap-1.5 px-2 py-1 rounded-lg border border-surface-border bg-surface/60 text-[11px] font-mono text-neutral-300"
                      >
                        {att.preview ? (
                          // eslint-disable-next-line @next/next/no-img-element
                          <img
                            src={att.preview}
                            alt={att.name}
                            className="w-5 h-5 rounded object-cover border border-surface-border"
                          />
                        ) : (
                          <FileText className="w-3.5 h-3.5 text-neutral-400" />
                        )}
                        <span className="max-w-[150px] truncate">{att.name}</span>
                      </span>
                    ))}
                  </div>
                )}
                {item.entry.timestamp && (
                  <div
                    suppressHydrationWarning
                    className="mt-1 text-right text-[11px] text-neutral-500"
                  >
                    {item.entry.timestamp}
                  </div>
                )}
              </div>
            </div>
          );
        }

        // SYSTEM MESSAGE: Clean pill
        if (item.type === "system") {
          const isTurnLimitWarning =
            item.entry.content.includes("⚡") ||
            item.entry.content.includes("Turn Budget Exceeded") ||
            item.entry.content.includes("Synthesizing final answer");

          if (isTurnLimitWarning) {
            return (
              <div key={item.entry.id} className="flex justify-center my-3">
                <div className="inline-flex items-center gap-2 px-3.5 py-1.5 rounded-full border border-amber-500/40 bg-amber-500/10 text-amber-300 text-[11px] font-mono shadow-sm">
                  <Zap className="w-3.5 h-3.5 text-amber-400 shrink-0" />
                  <span>{item.entry.content.replace(/^⚡\s*/, "")}</span>
                </div>
              </div>
            );
          }

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
            <div key={item.data.id}>
              <ActionGroup
                id={item.data.id}
                entries={item.data.entries}
                isActive={item.data.isActive}
              />
            </div>
          );
        }

        // ASSISTANT ANSWER: plain text on the page; actions appear on hover or focus
        return (
          <div key={item.entry.id} className="group my-4 min-w-0">
            <div className="min-w-0 space-y-1.5">
              <div className="flex items-center justify-between gap-2 text-xs">
                <span className="flex items-center gap-2 font-medium text-neutral-700 dark:text-neutral-200">
                  <span className="flex h-5 w-5 flex-shrink-0 items-center justify-center rounded-md border border-brand/25 bg-brand/10 text-brand">
                    <RocketChatIcon className="h-3 w-3" />
                  </span>
                  <AnsweredBy
                    binding={item.entry.answeredBy ?? (item.entry.isPersisted ? null : turnBinding)}
                  />
                </span>
                <div className="flex items-center gap-1 opacity-0 transition-opacity group-hover:opacity-100 group-focus-within:opacity-100 [@media(hover:none)]:opacity-100">
                  {item.entry.timestamp && (
                    <span suppressHydrationWarning className="mr-1 text-[11px] text-neutral-500">
                      {item.entry.timestamp}
                    </span>
                  )}
                  <button
                    type="button"
                    aria-label="Copy answer"
                    title="Copy answer"
                    onClick={() => handleCopy(item.entry.id, item.entry.content)}
                    className="cursor-pointer rounded-md p-1.5 text-neutral-500 hover:bg-surface-elevated hover:text-foreground"
                  >
                    {copiedEntryId === item.entry.id ? (
                      <Check className="h-3.5 w-3.5 text-diff-add" />
                    ) : (
                      <Copy className="h-3.5 w-3.5" />
                    )}
                  </button>
                  <button
                    type="button"
                    data-testid="fork-from-here"
                    disabled={forkingEntryId === item.entry.id}
                    aria-label="Fork the conversation from this answer"
                    title="Fork the conversation from this answer"
                    onClick={() => void handleForkFrom(item.entry.id)}
                    className="inline-flex cursor-pointer items-center gap-1 rounded-md p-1.5 text-neutral-500 hover:bg-surface-elevated hover:text-brand disabled:opacity-50"
                  >
                    <GitFork className="h-3.5 w-3.5" />
                    <span className="sr-only">Fork from here</span>
                  </button>
                </div>
              </div>

              <div className="font-sans text-[14.5px] leading-relaxed text-foreground">
                <MarkdownRenderer content={item.entry.content} />
              </div>
            </div>
          </div>
        );
      })}

      {/* Real-time Processing Indicator */}
      {isExecuting && !activeDecision && (
        <div className="flex items-center gap-2">
          <div className="flex h-5 w-5 flex-shrink-0 items-center justify-center rounded-md border border-brand/25 bg-brand/10 text-brand">
            <RocketChatIcon className="h-3 w-3 animate-pulse" />
          </div>
          <div className="flex items-center gap-2 text-xs text-neutral-500 dark:text-neutral-400 font-sans">
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

      {/* Approval Gate */}
      {activeApproval && (
        <ApprovalCard
          approval={activeApproval}
          onApprove={(actionId) => onApprove?.(actionId)}
          onReject={(actionId) => onReject?.(actionId)}
        />
      )}

    </div>
    {showJumpToLatest && (
      <button
        type="button"
        data-testid="scroll-to-latest"
        aria-label="Scroll to the latest message"
        onClick={jumpToLatest}
        className="absolute bottom-3 left-1/2 z-10 flex h-9 w-9 -translate-x-1/2 items-center justify-center rounded-full border border-surface-border bg-surface-card text-foreground shadow-md transition-colors hover:bg-surface-elevated"
      >
        <ArrowDown className="h-4 w-4" aria-hidden="true" />
      </button>
    )}
    </div>
  );
};

/** Who answered a turn: its own agent and model, never the composer's current selection. */
function AnsweredBy({ binding }: { binding: TurnBinding | null }) {
  if (!binding) return <>Rocket Chat</>;
  return (
    <>
      <span data-testid="answered-by">{binding.agentName || "Raw model"}</span>
      <span className="font-mono text-[11px] font-normal text-neutral-500">
        {binding.model.split("/").pop()}
      </span>
    </>
  );
}

"use client";

import { useShallow } from "zustand/react/shallow";
import React, { useRef, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { useMissionStore } from "../lib/store";
import { useSession } from "./AuthProvider";
import { DecisionCard } from "./DecisionCard";
import { ApprovalCard } from "./ApprovalCard";
import { ActionGroup } from "./ActionGroup";
import { useVirtualizer } from "@tanstack/react-virtual";
import { MarkdownRenderer } from "./MarkdownRenderer";
import { TaskChecklist } from "./TaskChecklist";
import { TurnSummaryCard } from "./TurnSummaryCard";
import { FlightLogEntry, TurnBinding } from "../types/mission";
import { RocketChatIcon } from "./RocketChatLogo";
import { CockpitEmptyState } from "./CockpitEmptyState";
import {
  AlertTriangle,
  Archive,
  ArrowDown,
  Check,
  Clock,
  Copy,
  FileText,
  GitFork,
  GitPullRequest,
  Info,
  RefreshCw,
  Search,
  X,
  Zap,
} from "lucide-react";

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

const hasSubsequentToolsInTurn = (startIndex: number, logs: FlightLogEntry[]): boolean => {
  for (let j = startIndex + 1; j < logs.length; j++) {
    const next = logs[j];
    if (next.role === "USER") break;
    if (
      next.toolCall ||
      next.subagent ||
      (next.content &&
        (next.content.startsWith("Invoking tool:") ||
          next.content.startsWith("Executing tool:") ||
          next.content.startsWith("Delegating to subagent")))
    ) {
      return true;
    }
  }
  return false;
};

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
  } = useMissionStore(
    useShallow((state) => ({ flightLog: state.flightLog, activeDecision: state.activeDecision, activeApproval: state.activeApproval, isExecuting: state.isExecuting, statusMessage: state.statusMessage, turnBinding: state.turnBinding, isStreamContinuous: state.isStreamContinuous, session: state.session, fetchSessions: state.fetchSessions }))
  );

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
  const [searchQuery, setSearchQuery] = useState("");
  const [isSearchOpen, setIsSearchOpen] = useState(false);
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

  for (let i = 0; i < flightLog.length; i++) {
    const entry = flightLog[i];
    if (entry.role === "USER") {
      flushGroup();
      rawStreamItems.push({ type: "user", entry });
    } else if (entry.role === "SYSTEM") {
      const lower = (entry.content || "").toLowerCase();
      const isWait =
        lower.includes("waiting for") ||
        lower.includes("wait started") ||
        lower.includes("durable wait");
      // If a wait entry precedes an adjacent wait_for tool call, group the tool call first
      if (
        isWait &&
        i + 1 < flightLog.length &&
        flightLog[i + 1].toolCall?.name === "wait_for"
      ) {
        currentGroupEntries.push(flightLog[i + 1]);
        flushGroup();
        rawStreamItems.push({ type: "system", entry });
        i++;
        continue;
      }
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
      const isStepPreface =
        !isSubagentInvocation &&
        !isToolInvocation &&
        !isPureReasoning &&
        hasSubsequentToolsInTurn(i, flightLog);

      if (isSubagentInvocation || isToolInvocation || isPureReasoning || isStepPreface) {
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

  const filteredItems = searchQuery.trim()
    ? rawStreamItems.filter((item) => {
        const text =
          item.type === "action_group"
            ? item.data.entries.map((e) => e.content || e.reasoning || "").join(" ")
            : item.entry.content || item.entry.reasoning || "";
        return text.toLowerCase().includes(searchQuery.toLowerCase());
      })
    : rawStreamItems;

  const isVirtualized = filteredItems.length > 100;
  const rowVirtualizer = useVirtualizer({
    count: filteredItems.length,
    getScrollElement: () => streamRef.current,
    estimateSize: () => 140,
    overscan: 8,
  });

  const renderItem = (item: (typeof rawStreamItems)[number]) => {
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
                    <span className="max-w-[150px] truncate" title={att.name}>{att.name}</span>
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

    // SYSTEM MESSAGE: Clean pill or special card
    if (item.type === "system") {
      const content = item.entry.content;
      const lower = content.toLowerCase();

      // Compaction Card
      if (lower.includes("compacted") || lower.includes("compaction")) {
        return (
          <div key={item.entry.id} data-testid="compaction-card" className="flex justify-center my-3">
            <div className="w-full max-w-xl rounded-xl border border-emerald-500/40 bg-emerald-500/10 p-3.5 space-y-2 text-foreground">
              <div className="flex items-center justify-between gap-2">
                <div className="flex items-center gap-2">
                  <Archive className="w-4 h-4 text-emerald-400" />
                  <span className="font-semibold text-xs text-emerald-300 uppercase tracking-wide">
                    Context Compacted
                  </span>
                </div>
                <span className="text-[10px] font-mono text-emerald-400/80">Checkpoint Saved</span>
              </div>
              <div className="text-xs text-neutral-300 leading-relaxed font-sans">
                <MarkdownRenderer content={content} />
              </div>
            </div>
          </div>
        );
      }

      // Goal Continuation Card
      if (lower.includes("continuation")) {
        return (
          <div key={item.entry.id} data-testid="continuation-card" className="flex justify-center my-3">
            <div className="w-full max-w-xl rounded-xl border border-sky-500/40 bg-sky-500/10 p-3.5 space-y-2 text-foreground">
              <div className="flex items-center justify-between gap-2">
                <div className="flex items-center gap-2">
                  <RefreshCw className="w-4 h-4 text-sky-400" />
                  <span className="font-semibold text-xs text-sky-300 uppercase tracking-wide">
                    Goal Continuation
                  </span>
                </div>
                <span className="text-[10px] font-mono text-sky-400/80">Multi-Turn Resumed</span>
              </div>
              <div className="text-xs text-neutral-300 leading-relaxed font-sans">
                <MarkdownRenderer content={content} />
              </div>
            </div>
          </div>
        );
      }

      // Durable Wait Card
      if (lower.includes("waiting for") || lower.includes("wait started") || lower.includes("durable wait")) {
        const isCi = lower.includes("ci_status") || lower.includes("check") || lower.includes("ci ");
        const isReview = lower.includes("review");
        return (
          <div key={item.entry.id} data-testid="waiting-card" className="flex justify-center my-3">
            <div className="w-full max-w-xl rounded-xl border border-amber-500/40 bg-amber-500/10 p-3.5 space-y-2 text-foreground shadow-sm">
              <div className="flex items-center justify-between gap-2">
                <div className="flex items-center gap-2">
                  {isCi ? (
                    <GitPullRequest className="w-4 h-4 text-amber-400 shrink-0" />
                  ) : (
                    <Clock className="w-4 h-4 text-amber-400 shrink-0" />
                  )}
                  <span className="font-semibold text-xs text-amber-300 uppercase tracking-wide">
                    {isCi ? "CI Status Gate" : isReview ? "PR Review Gate" : "Durable Wait"}
                  </span>
                </div>
                <div className="flex items-center gap-2">
                  <span className="inline-flex items-center gap-1.5 rounded-full bg-amber-500/20 px-2 py-0.5 text-[10px] font-mono font-medium text-amber-300 border border-amber-500/30">
                    <span className="h-1.5 w-1.5 rounded-full bg-amber-400 animate-pulse" />
                    Waiting for external event
                  </span>
                  {item.entry.timestamp && (
                    <span suppressHydrationWarning className="text-[10px] font-mono text-neutral-500/70">
                      {item.entry.timestamp}
                    </span>
                  )}
                </div>
              </div>
              <div className="text-xs text-neutral-300 leading-relaxed font-sans">
                <MarkdownRenderer content={content} />
              </div>
            </div>
          </div>
        );
      }

      // Telemetry Alert Card
      if (lower.includes("cache collapse") || lower.includes("alert:")) {
        return (
          <div key={item.entry.id} data-testid="alert-card" className="flex justify-center my-3">
            <div className="w-full max-w-xl rounded-xl border border-rose-500/40 bg-rose-500/10 p-3.5 space-y-2 text-foreground">
              <div className="flex items-center justify-between gap-2">
                <div className="flex items-center gap-2">
                  <AlertTriangle className="w-4 h-4 text-rose-400" />
                  <span className="font-semibold text-xs text-rose-300 uppercase tracking-wide">
                    Telemetry Alert
                  </span>
                </div>
              </div>
              <div className="text-xs text-rose-200 leading-relaxed font-sans">
                <MarkdownRenderer content={content} />
              </div>
            </div>
          </div>
        );
      }

      const isTurnLimitWarning =
        content.includes("⚡") ||
        content.includes("Turn Budget Exceeded") ||
        content.includes("Synthesizing final answer");

      if (isTurnLimitWarning) {
        return (
          <div key={item.entry.id} className="flex justify-center my-3">
            <div className="inline-flex items-center gap-2 px-3.5 py-1.5 rounded-full border border-amber-500/40 bg-amber-500/10 text-amber-300 text-[11px] font-mono shadow-sm">
              <Zap className="w-3.5 h-3.5 text-amber-400 shrink-0" />
              <span>{content.replace(/^⚡\s*/, "")}</span>
            </div>
          </div>
        );
      }

      return (
        <div key={item.entry.id} className="flex justify-center my-2">
          <div className="inline-flex items-center gap-2 px-3 py-1 rounded-full border border-surface-border bg-surface-card/40 text-neutral-400 text-[11px] font-mono">
            <Info className="w-3.5 h-3.5 text-neutral-400 shrink-0" />
            <span>{content}</span>
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

    // ASSISTANT ANSWER
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
            <div className="flex items-center gap-2">
              {item.entry.timestamp && (
                <span suppressHydrationWarning className="font-mono text-[10px] text-neutral-500/70">
                  {item.entry.timestamp}
                </span>
              )}
              <div className="flex items-center gap-1 opacity-0 transition-opacity group-hover:opacity-100 group-focus-within:opacity-100 [@media(hover:none)]:opacity-100">
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
        </div>

          <div className="font-sans text-[14.5px] leading-relaxed text-foreground">
            <MarkdownRenderer content={item.entry.content} />
          </div>
        </div>
      </div>
    );
  };

  return (
    <div className="relative flex min-h-0 flex-1 flex-col">
      {/* Stream Search Header */}
      <div className="absolute top-2 right-4 z-20">
        {isSearchOpen ? (
          <div className="flex items-center gap-1.5 rounded-lg border border-surface-border bg-surface-elevated/95 px-2 py-1 text-xs shadow-md backdrop-blur-md">
            <Search className="h-3.5 w-3.5 text-neutral-400" />
            <input
              type="text"
              data-testid="stream-search-input"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder="Search flight log..."
              className="bg-transparent text-xs text-foreground placeholder-neutral-500 focus:outline-none w-36 sm:w-52"
              autoFocus
            />
            {searchQuery && (
              <button
                type="button"
                onClick={() => setSearchQuery("")}
                className="text-neutral-400 hover:text-foreground"
              >
                <X className="h-3 w-3" />
              </button>
            )}
            <button
              type="button"
              onClick={() => {
                setIsSearchOpen(false);
                setSearchQuery("");
              }}
              className="text-neutral-500 hover:text-foreground ml-1"
            >
              <X className="h-3.5 w-3.5" />
            </button>
          </div>
        ) : (
          <button
            type="button"
            data-testid="stream-search-toggle"
            aria-label="Search flight log"
            onClick={() => setIsSearchOpen(true)}
            className="rounded-lg border border-surface-border bg-surface-card/80 p-1.5 text-neutral-400 hover:bg-surface-elevated hover:text-foreground transition-colors shadow-sm"
          >
            <Search className="h-3.5 w-3.5" />
          </button>
        )}
      </div>

      {/* Progress stays in view while the conversation scrolls */}
      <div className="shrink-0 px-4 pt-3 sm:px-6 empty:hidden">
        <TaskChecklist />
      </div>

      <div
        ref={streamRef}
        onScroll={handleScroll}
        data-testid="flight-log-stream"
        data-stream-continuous={isStreamContinuous}
        className="continuous-flight-stream flex min-h-0 flex-1 flex-col overflow-y-auto overflow-x-hidden [&>*]:shrink-0 px-4 sm:px-6 py-6 space-y-6 font-sans select-text max-w-3xl mx-auto w-full"
      >
        {flightLog.length === 0 && !isExecuting && <CockpitEmptyState />}

        {isVirtualized ? (
          <div
            style={{
              height: `${rowVirtualizer.getTotalSize()}px`,
              width: "100%",
              position: "relative",
            }}
          >
            {rowVirtualizer.getVirtualItems().map((virtualRow) => {
              const item = filteredItems[virtualRow.index];
              return (
                <div
                  key={virtualRow.key}
                  data-index={virtualRow.index}
                  ref={rowVirtualizer.measureElement}
                  style={{
                    position: "absolute",
                    top: 0,
                    left: 0,
                    width: "100%",
                    transform: `translateY(${virtualRow.start}px)`,
                  }}
                >
                  {renderItem(item)}
                </div>
              );
            })}
          </div>
        ) : (
          filteredItems.map((item) => renderItem(item))
        )}

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

      <TurnSummaryCard />

    </div>
    {/* Questions and approvals wait here, above the composer, whatever the stream is scrolled to */}
    {(activeDecision || activeApproval) && (
      <div data-testid="gate-dock" className="max-h-[50vh] shrink-0 space-y-3 overflow-y-auto border-t border-amber-500/40 bg-surface-base px-4 py-3 sm:px-6">
        <div className="mx-auto w-full max-w-3xl space-y-3">
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
      </div>
    )}
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

"use client";

import { threadBindingOf } from "../lib/channels";
import { splitSpeaker, useParticipants } from "../lib/participants";
import { ChannelIcon } from "./ChannelIcon";
import { useShallow } from "zustand/react/shallow";
import React, { useCallback, useRef, useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { useMissionStore } from "../lib/store";
import { useAuth } from "./AuthProvider";
import { DecisionCard } from "./DecisionCard";
import { ApprovalCard } from "./ApprovalCard";
import { TranscriptSearchResults } from "./TranscriptSearchResults";
import { AnswerActions, UserMessageActions } from "./MessageActions";
import { useCockpitRequest } from "../lib/cockpit-events";
import { ActionGroup } from "./ActionGroup";
import { useVirtualizer } from "@tanstack/react-virtual";
import { SystemNoticeCard } from "./SystemNoticeCard";
import { MarkdownRenderer } from "./MarkdownRenderer";
import { TaskChecklist } from "./TaskChecklist";
import { TurnSummaryCard } from "./TurnSummaryCard";
import { WaitCard, waitOf } from "./WaitCard";
import { ApprovalRememberScope, ArtifactItem, FlightLogEntry, TurnBinding } from "../types/mission";
import { RocketChatIcon } from "./RocketChatLogo";
import { RocketLoader } from "./RocketLoader";
import { useLoadingQuip } from "../lib/loadingQuips";
import { QUICK_TURN_MS, TurnProgressLine, useNow } from "./TurnProgressLine";
import { TurnErrorCard } from "./TurnErrorCard";
import { useKeepAtBottom } from "../lib/useKeepAtBottom";
import { describeArtifactCall, isArtifactCall, mentionOfArtifact, type ArtifactMention } from "../lib/artifact-calls";
import { ArtifactCard } from "./ArtifactCard";
import { CockpitEmptyState } from "./CockpitEmptyState";
import { SessionLoadingScreen } from "./SessionLoadingScreen";
import {
  ArrowDown,
  Check,
  Copy,
  FileText,
  GitFork,
  Loader2,
  Search,
  X,
} from "lucide-react";

interface FlightLogStreamProps {
  onAnswerQuestion: (questionId: string, selectedOptions: string[], customText?: string) => void;
  onApprove?: (actionId: string, remember?: ApprovalRememberScope) => void;
  onReject?: (actionId: string) => void;
  /** Sends a failed turn's instruction again, on another model when one is given. */
  onRetryTurn?: (prompt: string, model?: string) => void;
  opening?: boolean;
}

interface ActionGroupData {
  id: string;
  entries: FlightLogEntry[];
  isActive?: boolean;
}

/** A conversation longer than this renders only the rows near the viewport. */
const VIRTUALIZE_AFTER_ITEMS = 40;

type StreamItem =
  | { type: "user"; entry: FlightLogEntry }
  | { type: "system"; entry: FlightLogEntry }
  | { type: "action_group"; data: ActionGroupData }
  | { type: "answer"; entry: FlightLogEntry }
  | { type: "artifact"; entry: FlightLogEntry; mention: ArtifactMention };

const isToolEntry = (entry: FlightLogEntry): boolean =>
  Boolean(
    entry.toolCall ||
      entry.subagent ||
      (entry.content &&
        (entry.content.startsWith("Invoking tool:") ||
          entry.content.startsWith("Executing tool:") ||
          entry.content.startsWith("Delegating to subagent")))
  );

/** For each entry, whether a tool call follows it before the next user message: one backward pass. */
const toolsFollowInTurn = (logs: FlightLogEntry[]): boolean[] => {
  const follows = new Array<boolean>(logs.length).fill(false);
  let seen = false;
  for (let j = logs.length - 1; j >= 0; j--) {
    follows[j] = seen;
    if (logs[j].role === "USER") seen = false;
    else if (isToolEntry(logs[j])) seen = true;
  }
  return follows;
};

/**
 * The conversation as the stream shows it: consecutive steps folded into action groups between
 * the messages and answers. Pure, so the component recomputes it only when the log changes.
 */
function groupFlightLog(flightLog: FlightLogEntry[], isExecuting: boolean, artifacts: ArtifactItem[]): StreamItem[] {
  const rawStreamItems: StreamItem[] = [];
  const toolFollows = toolsFollowInTurn(flightLog);

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
      if (entry.artifact) {
        rawStreamItems.push({ type: "artifact", entry, mention: mentionOfArtifact(entry.artifact, "created") });
      } else {
        rawStreamItems.push({ type: "system", entry });
      }
    } else if (isArtifactCall(entry.toolCall)) {
      // A deliverable is a result the person came for: it stands out of the collapsed steps.
      flushGroup();
      rawStreamItems.push({ type: "artifact", entry, mention: describeArtifactCall(entry.toolCall, artifacts) });
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
        (toolFollows[i] ||
          (isExecuting && i === flightLog.length - 1 && (entry.isReasoningActive || !entry.content.trim())));

      if (isSubagentInvocation || isToolInvocation || isPureReasoning || isStepPreface) {
        currentGroupEntries.push(entry);
      } else {
        if (entry.reasoning) {
          currentGroupEntries.push({
            id: `thought-${entry.id}`,
            role: "AGENT",
            content: "",
            reasoning: entry.reasoning,
            timestamp: entry.timestamp,
            isReasoningActive: false,
          });
        }
        flushGroup();
        rawStreamItems.push({
          type: "answer",
          entry: {
            ...entry,
            reasoning: undefined,
          },
        });
      }
    }
  }

  if (currentGroupEntries.length > 0) {
    flushGroup(isExecuting);
  }
  return rawStreamItems;
}

export const FlightLogStream: React.FC<FlightLogStreamProps> = ({
  onAnswerQuestion,
  onApprove,
  onReject,
  onRetryTurn,
  opening,
}) => {
  const {
    flightLog,
    artifacts,
    activeDecision,
    activeApproval,
    isExecuting,
    statusMessage,
    turnBinding,
    isStreamContinuous,
    session,
    fetchSessions,
    hasOlderHistory,
    isLoadingOlderHistory,
    loadOlderHistory,
  } = useMissionStore(
    useShallow((state) => ({
      flightLog: state.flightLog,
      artifacts: state.artifacts,
      activeDecision: state.activeDecision,
      activeApproval: state.activeApproval,
      isExecuting: state.isExecuting,
      statusMessage: state.statusMessage,
      turnBinding: state.turnBinding,
      isStreamContinuous: state.isStreamContinuous,
      session: state.session,
      fetchSessions: state.fetchSessions,
      hasOlderHistory: state.hasOlderHistory,
      isLoadingOlderHistory: state.isLoadingOlderHistory,
      loadOlderHistory: state.loadOlderHistory,
    }))
  );

  const quip = useLoadingQuip(isExecuting && !activeDecision);
  const turnStartedAt = useMissionStore((state) => state.turnProgress.startedAt);
  const historyUserTurnsBefore = useMissionStore((state) => state.historyUserTurnsBefore);
  const now = useNow(isExecuting);
  // The playful line suits a quick turn; on a long one the real progress is what matters.
  const showQuip = turnStartedAt === null || now - turnStartedAt < QUICK_TURN_MS;

  const router = useRouter();
  const currentUserId = useAuth().user?.user_id ?? null;
  // Name the author of each message only when more than one person wrote in this session. A chat
  // thread's guests share no account, so the name the platform gave each message tells them apart.
  const userEntries = flightLog.filter((e) => e.role === "USER");
  // A message stored without its author (older history, the prompt that created the session)
  // is the owner's, unless a chat platform named its speaker.
  const ownerId = session && !session.is_draft ? session.tenant_user_id : null;
  const authorOf = (entry: { authorUserId?: string | null; content: string }) =>
    entry.authorUserId ?? (splitSpeaker(entry.content).speaker ? null : ownerId);
  const authorKey = (entry: { authorUserId?: string | null; content: string }) =>
    `${authorOf(entry) ?? ""}|${splitSpeaker(entry.content).speaker ?? ""}`;
  const showAuthors = new Set(userEntries.map(authorKey)).size > 1;
  const participants = useParticipants(
    showAuthors && session && !session.is_draft ? session.session_id : null,
    userEntries.map((e) => authorOf(e) ?? "").filter(Boolean)
  );
  const threadPlatform = threadBindingOf(session)?.platform ?? null;
  const [forkingEntryId, setForkingEntryId] = useState<string | null>(null);
  const [copiedEntryId, setCopiedEntryId] = useState<string | null>(null);
  const [searchQuery, setSearchQuery] = useState("");
  const [isSearchOpen, setIsSearchOpen] = useState(false);
  const openSearch = useCallback(() => setIsSearchOpen(true), []);
  useCockpitRequest("open-transcript-search", openSearch);
  const streamRef = useRef<HTMLDivElement>(null);
  // Follow new content only while the reader is at the bottom; once they scroll up to review
  // something, the stream must stay where they are. An empty conversation shows its starting
  // points from the top; only real content is followed.
  const { following: followingRef, setFollowing, readScroll } = useKeepAtBottom(
    streamRef,
    !opening && (flightLog.length > 0 || isExecuting)
  );
  const lastEntryCountRef = useRef(0);
  const [showJumpToLatest, setShowJumpToLatest] = useState(false);

  const handleScroll = () => setShowJumpToLatest(!readScroll());

  const jumpToLatest = () => {
    const stream = streamRef.current;
    if (!stream) return;
    setFollowing(true);
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
  const promptOfTurn = new Map<number, string>();
  // Counted from the start of the conversation, not from the first message loaded.
  let userTurnsSoFar = historyUserTurnsBefore;
  for (const entry of flightLog) {
    if (entry.role === "USER") {
      userTurnsSoFar += 1;
      promptOfTurn.set(userTurnsSoFar, entry.content);
    }
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

  // Every conversation opens at its end: having scrolled up in the previous one says nothing here.
  const openSessionId = session?.session_id;
  useEffect(() => {
    setFollowing(true);
    lastEntryCountRef.current = 0;
    setShowJumpToLatest(false);
  }, [openSessionId, opening, setFollowing]);

  // Follow new entries while at the bottom.
  useEffect(() => {
    // An empty conversation shows its starting points from the top; only follow real content.
    if (flightLog.length === 0 && !isExecuting) return;
    // Sending a message always brings you back to the conversation's end.
    const grew = flightLog.length > lastEntryCountRef.current;
    lastEntryCountRef.current = flightLog.length;
    if (grew && flightLog[flightLog.length - 1]?.role === "USER") setFollowing(true);
    if (!followingRef.current) return;
    // Scroll the stream itself. scrollIntoView would also scroll every ancestor, including the
    // overflow-hidden page root, which pushes the top bar off screen on a phone. The jump is
    // instant: an animated one would fire scroll events that look like the reader leaving.
    const stream = streamRef.current;
    stream?.scrollTo({ top: stream.scrollHeight, behavior: "auto" });
  }, [flightLog, activeDecision, activeApproval, isExecuting, statusMessage, followingRef, setFollowing]);

  // Aggregate consecutive actions into distinct ActionGroups (once per change of the log).
  const rawStreamItems = useMemo(() => groupFlightLog(flightLog, isExecuting, artifacts), [flightLog, isExecuting, artifacts]);

  const filteredItems = searchQuery.trim()
    ? rawStreamItems.filter((item) => {
        const text =
          item.type === "action_group"
            ? item.data.entries.map((e) => e.content || e.reasoning || "").join(" ")
            : item.entry.content || item.entry.reasoning || "";
        return text.toLowerCase().includes(searchQuery.toLowerCase());
      })
    : rawStreamItems;

  // Rows are measured as they render (measureElement); below this count there is little to save.
  const isVirtualized = filteredItems.length > VIRTUALIZE_AFTER_ITEMS;
  const rowVirtualizer = useVirtualizer({
    count: filteredItems.length,
    getScrollElement: () => streamRef.current,
    estimateSize: () => 140,
    overscan: 8,
  });
  // Measuring a row above the viewport normally shifts the scroll to keep what is shown in place;
  // that shift fires a scroll event far from the end, which reads as the reader leaving it. While
  // following, the end is held by useKeepAtBottom instead.
  rowVirtualizer.shouldAdjustScrollPositionOnItemSizeChange = () => !followingRef.current;

  const renderItem = (item: StreamItem) => {
    if (item.type === "artifact") {
      return <ArtifactCard key={item.entry.id} mention={item.mention} />;
    }
    // USER REQUEST: Simple bubble on the right
    if (item.type === "user") {
      const { speaker, text } = splitSpeaker(item.entry.content);
      const author = authorOf(item.entry);
      const mine = author !== null && author === currentUserId && !speaker;
      const participant = author ? participants.get(author) : undefined;
      // A guest is named by the platform (its account is a placeholder); a member by their name.
      const name = mine ? "You" : participant && !participant.guest_of ? participant.name : speaker ?? participant?.name ?? "Someone";
      const via = speaker ? participant?.guest_of ?? threadPlatform : null;
      const left = showAuthors && !mine;
      return (
        <div key={item.entry.id} className={`flex my-3 ${left ? "justify-start" : "justify-end"}`}>
          <div
            data-testid="user-message"
            className={`min-w-0 max-w-[85%] bg-surface-card border border-surface-border rounded-2xl ${left ? "rounded-tl-sm" : "rounded-tr-sm"} px-4 py-3 text-foreground shadow-sm [overflow-wrap:anywhere]`}
          >
            {showAuthors && (
              <div data-testid="user-message-author" className="mb-1 flex items-center gap-1.5 text-xs font-semibold text-foreground">
                {via && via !== "github" && <ChannelIcon platform={via} className="h-3 w-3" />}
                <span>{name}</span>
              </div>
            )}
            <div className="text-[14.5px] leading-relaxed">
              <MarkdownRenderer content={text} />
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
            <div className="mt-1 flex items-center justify-end gap-1">
              <UserMessageActions
                sessionId={session && !session.is_draft ? session.session_id : null}
                turn={userTurnsByEntry.get(item.entry.id) ?? 0}
                content={item.entry.content}
                busy={isExecuting}
              />
              {item.entry.timestamp && (
                <span suppressHydrationWarning className="text-right text-[11px] text-neutral-500">
                  {item.entry.timestamp}
                </span>
              )}
            </div>
          </div>
        </div>
      );
    }

    // SYSTEM MESSAGE: Clean pill or special card
    if (item.type === "system") {
      if (waitOf(item.entry)) return <WaitCard key={item.entry.id} entry={item.entry} />;
      if (item.entry.turnError) {
        return <TurnErrorCard key={item.entry.id} error={item.entry.turnError} onRetry={onRetryTurn} />;
      }
      return <SystemNoticeCard key={item.entry.id} entry={item.entry} />;
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
              <AnswerActions
                sessionId={session && !session.is_draft ? session.session_id : null}
                turn={userTurnsByEntry.get(item.entry.id) ?? 0}
                prompt={promptOfTurn.get(userTurnsByEntry.get(item.entry.id) ?? 0) ?? null}
                busy={isExecuting}
              />
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

  if (opening) {
    return (
      <div
        data-testid="flight-log-stream"
        className="flex flex-1 h-full w-full items-center justify-center bg-surface-base select-none"
      >
        <SessionLoadingScreen />
      </div>
    );
  }

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
              placeholder="Search this conversation…"
              aria-label="Search this conversation"
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
        ) : null}
        {isSearchOpen && (
          <TranscriptSearchResults sessionId={session && !session.is_draft ? session.session_id : null} query={searchQuery} />
        )}
        {isSearchOpen ? null : (
          <button
            type="button"
            data-testid="stream-search-toggle"
            aria-label="Search this conversation"
            onClick={openSearch}
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
        role="log"
        aria-live="polite"
        aria-label="Conversation"
        data-stream-continuous={isStreamContinuous}
        className="continuous-flight-stream flex min-h-0 flex-1 flex-col overflow-y-auto overflow-x-hidden [&>*]:shrink-0 px-4 sm:px-6 py-6 space-y-6 font-sans select-text max-w-3xl mx-auto w-full"
      >
        {hasOlderHistory && (
          <div className="flex justify-center pb-2">
            <button
              type="button"
              data-testid="load-older-history"
              disabled={isLoadingOlderHistory}
              onClick={() => void loadOlderHistory()}
              className="inline-flex items-center gap-1.5 rounded-full border border-surface-border bg-surface-elevated/70 px-3 py-1 text-xs text-neutral-500 hover:text-foreground transition-colors disabled:opacity-50"
            >
              {isLoadingOlderHistory ? (
                <>
                  <Loader2 className="h-3 w-3 animate-spin text-brand" />
                  <span>Loading earlier messages…</span>
                </>
              ) : (
                <span>Load earlier messages</span>
              )}
            </button>
          </div>
        )}
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
        <div data-testid="processing-indicator" className="flex items-start gap-2.5">
          <RocketLoader />
          <div className="min-w-0 font-sans text-xs">
            {showQuip && (
              <div
                aria-hidden="true"
                data-testid="loading-quip"
                className="truncate italic text-orange-700 dark:text-brand"
              >
                {quip}
              </div>
            )}
            <div className="flex items-center gap-2 text-neutral-500 dark:text-neutral-400">
              <span className="h-1.5 w-1.5 flex-none shrink-0 rounded-full bg-brand animate-ping" />
              <span className="truncate" title={statusMessage || undefined}>
                {statusMessage || "Working…"}
              </span>
            </div>
            <TurnProgressLine now={now} />
          </div>
        </div>
      )}

      <TurnSummaryCard />

    </div>
    {/* Questions and approvals wait here, above the composer, whatever the stream is scrolled to */}
    {(activeDecision || activeApproval) && (
      <div data-testid="gate-dock" role="alert" aria-live="assertive" className="max-h-[50vh] shrink-0 space-y-3 overflow-y-auto border-t border-amber-500/40 bg-surface-base px-4 py-3 sm:px-6">
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
          onApprove={(actionId, remember) => onApprove?.(actionId, remember)}
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

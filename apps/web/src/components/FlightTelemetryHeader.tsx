"use client";

import { SessionUsageChip } from "./SessionUsageChip";
import { SpendLimitChip } from "./SpendLimitChip";
import { useShallow } from "zustand/react/shallow";
import React, { useState } from "react";
import { useParams } from "next/navigation";
import { useMissionStore } from "../lib/store";
import { ViewMenu } from "./ViewMenu";
import { UserHeaderCapsule } from "./UserHeaderCapsule";
import { IconButton } from "./ui/IconButton";
import { ShareDialog } from "./ShareDialog";
import {
  Zap,
  PanelRightClose,
  PanelRightOpen,
  PanelLeftOpen,
  Share2,
} from "lucide-react";
import { threadBindingOf } from "../lib/channels";
import { ChannelIcon } from "./ChannelIcon";
import { SessionTitle } from "./SessionTitle";
import { statusOf, STATUS_LABEL, type SessionStatus } from "../lib/session-groups";

/** The one status vocabulary, shared with the sidebar: working, needs you, waiting, failed, ready. */
const StatusChip: React.FC<{ status: SessionStatus; tokenRate: number }> = ({ status, tokenRate }) => {
  const tone: Record<SessionStatus, string> = {
    working: "border-brand/30 text-brand",
    needs_you: "border-amber-500/50 text-amber-700 dark:text-amber-300",
    waiting: "border-sky-500/40 text-sky-700 dark:text-sky-300",
    failed: "border-rose-500/50 text-rose-700 dark:text-rose-300",
    idle: "border-surface-border text-neutral-700 dark:text-neutral-300",
  };
  return (
    <div data-testid="status-chip" data-status={status} className={`flex items-center gap-1.5 rounded-md border bg-surface-card px-2.5 py-1 text-[12px] ${tone[status]}`}>
      {status === "working" ? (
        <Zap className="h-3.5 w-3.5 animate-pulse" aria-hidden="true" />
      ) : (
        <span className={`h-1.5 w-1.5 rounded-full ${status === "idle" ? "bg-emerald-500" : status === "needs_you" ? "bg-amber-500" : status === "failed" ? "bg-rose-500" : "bg-sky-500"}`} aria-hidden="true" />
      )}
      <span>{status === "idle" ? "Ready" : status === "working" ? "Working" : STATUS_LABEL[status]}</span>
      {status === "working" && tokenRate > 0 && <span className="hidden text-neutral-500 sm:inline">· {tokenRate} tok/s</span>}
    </div>
  );
};

function formatTokens(tokens: number): string {
  return tokens >= 1000 ? `${(tokens / 1000).toFixed(1)}k tokens` : `${tokens} tokens`;
}

export const FlightTelemetryHeader: React.FC = () => {
  const {
    session,
    activeAgent,
    selectedModel,
    inferenceMode,
    tokenRate,
    isExecuting,
    isRightPanelOpen,
    setIsRightPanelOpen,
    isTurnInspectorOpen,
    setIsTurnInspectorOpen,
    isMemoryInspectorOpen,
    setIsMemoryInspectorOpen,
    openInspectorTab,
    isLeftSidebarOpen,
    setIsLeftSidebarOpen,
    goal,
  } = useMissionStore(
    useShallow((state) => ({ session: state.session, activeAgent: state.activeAgent, selectedModel: state.selectedModel, inferenceMode: state.inferenceMode, tokenRate: state.tokenRate, isExecuting: state.isExecuting, isRightPanelOpen: state.isRightPanelOpen, setIsRightPanelOpen: state.setIsRightPanelOpen, isTurnInspectorOpen: state.isTurnInspectorOpen, setIsTurnInspectorOpen: state.setIsTurnInspectorOpen, isMemoryInspectorOpen: state.isMemoryInspectorOpen, setIsMemoryInspectorOpen: state.setIsMemoryInspectorOpen, openInspectorTab: state.openInspectorTab, isLeftSidebarOpen: state.isLeftSidebarOpen, setIsLeftSidebarOpen: state.setIsLeftSidebarOpen, goal: state.goal }))
  );
  const params = useParams();
  const routeSessionId = typeof params?.sessionId === "string" ? params.sessionId : null;
  const sessions = useMissionStore((state) => state.sessions);
  const displayTitle =
    (session?.session_id === routeSessionId ? session?.title : null) ||
    (routeSessionId ? sessions.find((s) => s.session_id === routeSessionId)?.title : null) ||
    session?.title ||
    "New Session";
  const binding = threadBindingOf(session);
  const artifactCount = useMissionStore((state) => state.artifacts.length);

  const [isShareOpen, setIsShareOpen] = useState(false);

  const turnBinding = useMissionStore((state) => state.turnBinding);
  // While a turn runs, the header names what is running it, not what the composer would pick next.
  const running = isExecuting && turnBinding !== null;
  const activeModelDisplayName = running
    ? turnBinding.agentName || turnBinding.model.split("/").pop() || turnBinding.model
    : inferenceMode === "agent"
      ? activeAgent?.name || "Agent"
      : selectedModel.split("/").pop() || selectedModel;

  return (
    <header className="sticky top-0 z-30 h-14 shrink-0 pl-3 pr-1.5 sm:pl-4 sm:pr-2 bg-surface-sidebar/90 backdrop-blur-md border-b border-surface-border flex items-center justify-between gap-2 select-none transition-colors">
      {/* Left: Sidebar toggle (mobile only — the collapsed rail has its own at md+; the sidebar
          itself, open or as that rail, is where the brand mark lives here), Thread Title & Model Tag */}
      <div className="flex min-w-0 flex-1 items-center gap-3">
        {!isLeftSidebarOpen && (
          <IconButton
            label="Expand Sessions Sidebar"
            onClick={() => setIsLeftSidebarOpen(true)}
            className="shrink-0 md:hidden"
          >
            <PanelLeftOpen className="h-4 w-4" />
          </IconButton>
        )}

        {/* Thread Title (click to rename) */}
        <div className="min-w-0 flex-1">
          <SessionTitle
            title={displayTitle}
            sessionId={session && !session.is_draft && session.session_id === (routeSessionId ?? session.session_id) ? session.session_id : null}
          />
        </div>
        {binding?.url && (
          <a
            href={binding.url}
            target="_blank"
            rel="noopener noreferrer"
            data-testid={binding.platform === "slack" ? "open-in-slack" : "open-thread-header"}
            className="hidden lg:inline-flex items-center gap-1 shrink-0 whitespace-nowrap text-xs text-neutral-500 hover:text-brand dark:text-neutral-400"
          >
            <ChannelIcon platform={binding.platform} />
            Open in {binding.label}
          </a>
        )}

        <div className="h-3.5 w-px bg-surface-border hidden lg:block shrink-0" />

        {/* Real Model / Persona Tag */}
        <div className="hidden min-w-0 shrink items-center gap-1.5 text-xs text-neutral-500 dark:text-neutral-400 bg-surface-card hover:bg-surface-elevated px-2 py-0.5 rounded border border-surface-border transition-colors lg:flex">
          <span className="w-1.5 h-1.5 rounded-full bg-brand shrink-0" />
          <span className="max-w-[10rem] truncate font-mono text-[12px] text-neutral-700 dark:text-neutral-300">
            {activeModelDisplayName}
          </span>
        </div>
      </div>

      {/* Right: run state, then view controls, then account */}
      <div className="flex shrink-0 items-center gap-2 text-xs">
        <StatusChip
          status={session ? (isExecuting && statusOf(session) !== "needs_you" ? "working" : statusOf(session)) : isExecuting ? "working" : "idle"}
          tokenRate={tokenRate}
        />

        <div className="hidden sm:block">
          <SessionUsageChip
            sessionId={session && !session.is_draft ? session.session_id : undefined}
            running={isExecuting}
          />
        </div>

        <div className="hidden sm:block">
          <SpendLimitChip
            sessionId={session && !session.is_draft ? session.session_id : undefined}
            running={isExecuting}
          />
        </div>

        {goal && goal.status === "active" && (
          <div
            data-testid="goal-chip"
            title="Budget used by this long task across its continuation turns"
            className="hidden items-center gap-1.5 rounded-md border border-surface-border bg-surface-card px-2.5 py-1 text-[12px] text-neutral-600 dark:text-neutral-400 md:flex"
          >
            <span>Long task</span>
            <span className="font-mono">{formatTokens(goal.consumedTokens)}</span>
            <span className="font-mono">${goal.consumedCostUsd.toFixed(2)}</span>
            {goal.maxContinuations > 0 && (
              <span className="font-mono">
                {goal.continuationCount}/{goal.maxContinuations}
              </span>
            )}
          </div>
        )}

        <div className="mx-0.5 hidden h-4 w-px bg-surface-border sm:block" />

        {session?.session_id && (
          <button
            type="button"
            data-testid="share-trigger"
            onClick={() => setIsShareOpen(true)}
            title="Share this session"
            className="inline-flex h-8 items-center gap-1.5 rounded-lg border border-surface-border bg-surface-card px-2.5 text-xs text-foreground transition-colors hover:bg-surface-elevated"
          >
            <Share2 className="h-4 w-4" aria-hidden="true" />
            <span className="hidden sm:inline">Share</span>
          </button>
        )}

        <ViewMenu
          artifactCount={artifactCount}
          onMemory={() => setIsMemoryInspectorOpen(!isMemoryInspectorOpen)}
          onTurns={() => setIsTurnInspectorOpen(!isTurnInspectorOpen)}
          onArtifacts={() => openInspectorTab("artifacts")}
        />

        <IconButton
          data-testid="right-panel-toggle"
          label={isRightPanelOpen ? "Collapse Right Panel" : "Expand Right Panel (Inspector / Terminal)"}
          pressed={isRightPanelOpen}
          onClick={() => setIsRightPanelOpen(!isRightPanelOpen)}
        >
          {isRightPanelOpen ? <PanelRightClose className="h-4 w-4" /> : <PanelRightOpen className="h-4 w-4" />}
        </IconButton>

        <div className="mx-0.5 hidden h-4 w-px bg-surface-border sm:block" />

        <UserHeaderCapsule />
      </div>
      {isShareOpen && session?.session_id && (
        <ShareDialog sessionId={session.session_id} onClose={() => setIsShareOpen(false)} />
      )}
    </header>
  );
};

"use client";

import React from "react";
import Link from "next/link";
import { useMissionStore } from "../lib/store";
import { ThemeToggle } from "./Theme/ThemeToggle";
import { UserHeaderCapsule } from "./UserHeaderCapsule";
import { IconButton, iconButtonClass } from "./ui/IconButton";
import {
  Zap,
  Settings2,
  PanelRightClose,
  PanelRightOpen,
  PanelLeftOpen,
  History,
  Brain,
} from "lucide-react";

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
    isLeftSidebarOpen,
    setIsLeftSidebarOpen,
  } = useMissionStore();

  const activeModelDisplayName =
    inferenceMode === "agent"
      ? activeAgent?.name || "Agent"
      : selectedModel.split("/").pop() || selectedModel;

  return (
    <header className="sticky top-0 z-30 h-14 shrink-0 px-4 sm:px-6 bg-surface-base/90 backdrop-blur-md border-b border-surface-border flex items-center justify-between select-none transition-colors">
      {/* Left: Sidebar toggle, Thread Title & Model Tag */}
      <div className="flex items-center gap-3">
        {!isLeftSidebarOpen && (
          <div className="flex items-center gap-2">
            <IconButton label="Expand Sessions Sidebar" onClick={() => setIsLeftSidebarOpen(true)}>
              <PanelLeftOpen className="h-4 w-4" />
            </IconButton>
            <Link href="/" className="flex items-center gap-2">
              <span className="hidden font-display text-sm font-semibold tracking-tight text-foreground sm:inline">
                Rocket Chat
              </span>
            </Link>
          </div>
        )}

        {/* Thread Title */}
        <h1 className="text-sm font-semibold text-foreground flex items-center gap-2 min-w-0 truncate max-w-[34vw] sm:max-w-md">
          <span className="truncate">{session?.title || "New Session"}</span>
        </h1>

        <div className="h-3.5 w-px bg-surface-border hidden sm:block" />

        {/* Real Model / Persona Tag */}
        <div className="hidden sm:flex items-center gap-1.5 text-xs text-neutral-500 dark:text-neutral-400 bg-surface-card hover:bg-surface-elevated px-2 py-0.5 rounded border border-surface-border transition-colors">
          <span className="w-1.5 h-1.5 rounded-full bg-brand" />
          <span className="font-mono text-[11px] text-neutral-700 dark:text-neutral-300">
            {activeModelDisplayName}
          </span>
        </div>
      </div>

      {/* Right: run state, then view controls, then account */}
      <div className="flex items-center gap-2 text-xs">
        {isExecuting ? (
          <div className="flex items-center gap-1.5 rounded-md border border-brand/30 bg-surface-card px-2.5 py-1 text-[11px] text-brand">
            <Zap className="h-3.5 w-3.5 animate-pulse" aria-hidden="true" />
            <span>Working</span>
            {tokenRate > 0 && <span className="hidden text-neutral-500 sm:inline">· {tokenRate} tok/s</span>}
          </div>
        ) : (
          <div className="flex items-center gap-1.5 rounded-md border border-surface-border bg-surface-card px-2.5 py-1 text-[11px] text-neutral-600 dark:text-neutral-400">
            <span className="h-1.5 w-1.5 rounded-full bg-emerald-500" aria-hidden="true" />
            <span>Ready</span>
          </div>
        )}

        <div className="mx-0.5 hidden h-4 w-px bg-surface-border sm:block" />

        <div className="hidden items-center gap-1.5 sm:flex">
          <ThemeToggle />
          <IconButton
            data-testid="memory-inspector-trigger"
            label="Open memory inspector"
            pressed={isMemoryInspectorOpen}
            aria-expanded={isMemoryInspectorOpen}
            onClick={() => setIsMemoryInspectorOpen(!isMemoryInspectorOpen)}
          >
            <Brain className="h-4 w-4" />
          </IconButton>
          <IconButton
            data-testid="turn-inspector-toggle"
            label="Turn Inspector"
            pressed={isTurnInspectorOpen}
            onClick={() => setIsTurnInspectorOpen(!isTurnInspectorOpen)}
          >
            <History className="h-4 w-4" />
          </IconButton>
        </div>

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

        <Link href="/settings/github" title="Settings Console" aria-label="Settings Console" className={iconButtonClass(false, "hidden sm:inline-flex")}>
          <Settings2 className="h-4 w-4" />
        </Link>
      </div>
    </header>
  );
};

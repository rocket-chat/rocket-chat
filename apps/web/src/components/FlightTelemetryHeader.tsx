"use client";

import React from "react";
import Link from "next/link";
import { useMissionStore } from "../lib/store";
import { ThemeToggle } from "./Theme/ThemeToggle";
import { UserHeaderCapsule } from "./UserHeaderCapsule";
import {
  Zap,
  Settings2,
  PanelRightClose,
  PanelRightOpen,
  PanelLeftOpen,
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
    isLeftSidebarOpen,
    setIsLeftSidebarOpen,
  } = useMissionStore();

  const activeModelDisplayName =
    inferenceMode === "agent"
      ? activeAgent?.name || "Agent"
      : selectedModel.split("/").pop() || selectedModel;

  return (
    <header className="sticky top-0 z-30 h-13 px-4 sm:px-6 bg-surface-base/90 backdrop-blur-md border-b border-surface-border flex items-center justify-between select-none transition-colors">
      {/* Left: Sidebar toggle, Thread Title & Model Tag */}
      <div className="flex items-center gap-3">
        {!isLeftSidebarOpen && (
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={() => setIsLeftSidebarOpen(true)}
              className="p-1.5 rounded-lg border border-surface-border hover:border-brand/40 bg-surface-card hover:bg-surface-elevated text-neutral-400 hover:text-white transition-colors cursor-pointer"
              title="Expand Sessions Sidebar"
            >
              <PanelLeftOpen className="w-4 h-4" />
            </button>
            <Link href="/" className="flex items-center gap-2">
              <span className="font-display font-semibold text-sm tracking-tight text-foreground">
                Rocket Chat
              </span>
            </Link>
          </div>
        )}

        {/* Thread Title */}
        <h1 className="text-sm font-semibold text-foreground flex items-center gap-2 truncate max-w-xs sm:max-w-md">
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

      {/* Right: Real Execution State & Token Telemetry (only if streaming tokens), Theme Toggle, Right Panel toggle & Settings */}
      <div className="flex items-center gap-2 text-xs">
        {/* Dynamic Execution / Token Rate Tag */}
        {isExecuting ? (
          <div className="flex items-center gap-1.5 px-2.5 py-1 rounded-md bg-surface-card border border-brand/30 text-[11px] font-mono text-brand animate-pulse">
            <Zap className="w-3.5 h-3.5 text-brand" />
            <span>Executing</span>
            {tokenRate > 0 && (
              <>
                <span className="text-neutral-400 dark:text-neutral-600">·</span>
                <span className="text-neutral-700 dark:text-neutral-300">{tokenRate} tok/s</span>
              </>
            )}
          </div>
        ) : (
          <div className="flex items-center gap-1.5 px-2.5 py-1 rounded-md bg-surface-card border border-surface-border text-[11px] font-mono text-neutral-500 dark:text-neutral-400">
            <span className="w-1.5 h-1.5 rounded-full bg-emerald-500" />
            <span>Ready</span>
          </div>
        )}

        {/* Native Light/Dark Mode Toggle */}
        <ThemeToggle />

        {/* Toggle Right Panel (Inspector/Terminal) */}
        <button
          type="button"
          onClick={() => setIsRightPanelOpen(!isRightPanelOpen)}
          className={`p-1.5 rounded-lg border transition-colors cursor-pointer ${
            isRightPanelOpen
              ? "bg-surface-elevated border-surface-border text-foreground"
              : "bg-surface-card hover:bg-surface-elevated border-surface-border text-neutral-500 hover:text-foreground dark:text-neutral-400 dark:hover:text-white"
          }`}
          title={isRightPanelOpen ? "Collapse Right Panel" : "Expand Right Panel (Inspector / Terminal)"}
        >
          {isRightPanelOpen ? (
            <PanelRightClose className="w-4 h-4" />
          ) : (
            <PanelRightOpen className="w-4 h-4" />
          )}
        </button>

        {/* User Capsule */}
        <UserHeaderCapsule />

        {/* Settings Suite Entrypoint */}
        <Link
          href="/settings/github"
          className="p-1.5 rounded-lg bg-surface-card hover:bg-surface-elevated border border-surface-border text-neutral-500 hover:text-foreground dark:text-neutral-400 dark:hover:text-white transition-colors cursor-pointer"
          title="Settings Console"
        >
          <Settings2 className="w-4 h-4" />
        </Link>
      </div>
    </header>
  );
};

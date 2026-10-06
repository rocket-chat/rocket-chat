"use client";

import React, { useState, useRef, useEffect } from "react";
import { useMissionStore } from "../lib/store";
import { Server, GitBranch, Loader2, Rocket, ChevronDown, Check, Bot, Zap, Shield, Bug, Book, Search } from "lucide-react";
import { AgentPersonaConfig, ModelOption } from "../types/mission";

interface IgnitionConsoleProps {
  onIgnite: (prompt: string, model?: string) => void;
  initialPrompt?: string;
}

const getAgentIcon = (iconName?: string) => {
  switch (iconName) {
    case "shield":
      return <Shield className="w-3.5 h-3.5 text-emerald-500" />;
    case "bug":
      return <Bug className="w-3.5 h-3.5 text-amber-500" />;
    case "book":
      return <Book className="w-3.5 h-3.5 text-purple-500" />;
    case "bot":
    default:
      return <Bot className="w-3.5 h-3.5 text-primary" />;
  }
};

export const IgnitionConsole: React.FC<IgnitionConsoleProps> = ({
  onIgnite,
  initialPrompt = "",
}) => {
  const [prompt, setPrompt] = useState<string>(initialPrompt);

  useEffect(() => {
    if (initialPrompt) {
      setPrompt(initialPrompt);
      textareaRef.current?.focus();
    }
  }, [initialPrompt]);
  const [isModeDropdownOpen, setIsModeDropdownOpen] = useState<boolean>(false);
  const [modelSearchQuery, setModelSearchQuery] = useState<string>("");
  const [isLaunchingRocket, setIsLaunchingRocket] = useState<boolean>(false);
  const [isThrustPulsing, setIsThrustPulsing] = useState<boolean>(false);
  const dropdownRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const {
    flightLog,
    isExecuting,
    appendFlightLog,
    session,
    clearReasoning,
    setIsExecuting,
    setStatusMessage,
    selectedModel,
    availableModels,
    setAvailableModels,
    activeAgent,
    availableAgents,
    inferenceMode,
    selectAgent,
    selectDirectModel,
    suggestedFollowup,
    setSuggestedFollowup,
  } = useMissionStore();


  // Discover models from backend API on mount
  useEffect(() => {
    const fetchModels = async () => {
      try {
        const res = await fetch("/v1/models");
        if (res.ok) {
          const data = await res.json();
          if (Array.isArray(data.models) && data.models.length > 0) {
            setAvailableModels(data.models);
          }
        }
      } catch {
        // Retain pre-configured store defaults
      }
    };
    fetchModels();
  }, [setAvailableModels]);

  // Close dropdown on outside click
  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if (dropdownRef.current && !dropdownRef.current.contains(e.target as Node)) {
        setIsModeDropdownOpen(false);
      }
    };
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

  const handleIgnite = () => {
    const trimmed = prompt.trim();
    if (!trimmed || isExecuting) return;

    // Trigger cool rocket lift-off on the first user message of a session
    const hasUserMessages = flightLog.some((e) => e.role === "USER");
    const isFirstUserMessage = !hasUserMessages;
    if (isFirstUserMessage) {
      setIsLaunchingRocket(true);
      setTimeout(() => setIsLaunchingRocket(false), 1400);
    }

    // Trigger thrust pulse on send button
    setIsThrustPulsing(true);
    setTimeout(() => setIsThrustPulsing(false), 300);

    clearReasoning();
    setIsExecuting(true);
    setStatusMessage("Transmitting instructions to agent sandbox...");

    appendFlightLog({
      id: `user-${Date.now()}`,
      role: "USER",
      content: trimmed,
      timestamp: new Date().toLocaleTimeString(),
    });

    const targetModel = inferenceMode === "agent" ? activeAgent?.model || selectedModel : selectedModel;
    onIgnite(trimmed, targetModel);
    setPrompt("");
    setSuggestedFollowup(null);
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    // Tab key: Autocomplete LLM-suggested follow-up question into composer
    if (e.key === "Tab" && !prompt && suggestedFollowup) {
      e.preventDefault();
      setPrompt(suggestedFollowup);
      setSuggestedFollowup(null);
      return;
    }

    // Enter or ⌘⏎ / Ctrl⏎ to run
    if (e.key === "Enter") {
      if (!e.shiftKey) {
        e.preventDefault();
        handleIgnite();
      }
    }
  };

  const formatContextK = (val: unknown) => {
    if (typeof val === "number" && !isNaN(val)) return Math.round(val / 1024);
    const parsed = parseInt(String(val).replace(/[^0-9]/g, ""));
    if (isNaN(parsed)) return 128;
    return parsed > 1000 ? Math.round(parsed / 1024) : parsed;
  };

  const currentModelObj =
    availableModels.find((m) => m.id === selectedModel) ||
    availableModels[0] || {
      id: selectedModel,
      name: selectedModel,
      provider: "LLM",
      context_window: 128000,
    };

  const filteredModels = availableModels.filter(
    (m) =>
      m.name.toLowerCase().includes(modelSearchQuery.toLowerCase()) ||
      m.id.toLowerCase().includes(modelSearchQuery.toLowerCase())
  );

  return (
    <div className="relative border-t border-border bg-surface/90 backdrop-blur-md p-3.5 select-none transition-colors">
      {/* First Message Rocket Launch Lift-off Animation */}
      {isLaunchingRocket && (
        <div className="fixed inset-0 pointer-events-none z-50 flex items-center justify-center">
          <div className="flex flex-col items-center animate-rocket-liftoff">
            <div className="relative">
              <div className="w-16 h-16 rounded-2xl bg-primary/20 backdrop-blur-md border border-primary/50 flex items-center justify-center text-primary shadow-2xl shadow-primary/60">
                <Rocket className="w-9 h-9 -rotate-45 text-primary drop-shadow-[0_0_12px_rgba(249,115,22,0.8)]" />
              </div>
              {/* Rocket Exhaust Plume */}
              <div className="absolute top-full left-1/2 -translate-x-1/2 w-3 h-14 bg-gradient-to-b from-amber-400 via-primary to-transparent rounded-full blur-[2px] animate-pulse" />
              <div className="absolute top-full left-1/2 -translate-x-1/2 w-7 h-10 bg-gradient-to-b from-orange-500/80 via-transparent to-transparent rounded-full blur-sm" />
            </div>
            <div className="mt-6 px-3.5 py-1 rounded-full bg-surface/90 border border-primary/30 text-primary text-xs font-mono tracking-wider font-semibold shadow-lg">
              IGNITION & LIFTOFF 🚀
            </div>
          </div>
        </div>
      )}

      <div className="bg-surface-card border border-surface-border focus-within:border-brand/60 focus-within:ring-1 focus-within:ring-brand/40 rounded-2xl shadow-xl transition-all p-3">
        {/* Text Input Area & Ghost Autocomplete Overlay */}
        <div className="relative">
          <textarea
            ref={textareaRef}
            data-testid="instruction-input"
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder={
              suggestedFollowup
                ? ""
                : "Message Rocket Chat or instruct orbital maneuver... (Enter to run)"
            }
            rows={2}
            disabled={isExecuting}
            className="w-full resize-none bg-transparent text-sm font-sans text-neutral-100 placeholder:text-neutral-500 focus:outline-none disabled:opacity-50 leading-relaxed p-1 relative z-10"
          />

          {/* Ghost text displayed when composer is empty and LLM follow-up exists */}
          {!prompt && suggestedFollowup && !isExecuting && (
            <div className="absolute top-1 left-1 pointer-events-none flex items-center gap-2 select-none z-0">
              <span className="text-sm font-sans text-neutral-400/80 italic">
                {suggestedFollowup}
              </span>
              <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-mono bg-brand/15 text-brand border border-brand/30">
                Tab ⇥
              </span>
            </div>
          )}
        </div>

        {/* Console Parameter Deck & Trigger Button */}
        <div className="mt-2.5 pt-2 border-t border-border/50 flex flex-wrap items-center justify-between gap-2 text-xs">
          {/* Controls: Mode Selector (Agent OR Direct Model) */}
          <div className="flex items-center gap-2">
            <div className="relative" ref={dropdownRef}>
              <button
                type="button"
                onClick={() => setIsModeDropdownOpen(!isModeDropdownOpen)}
                disabled={isExecuting}
                className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg bg-surface hover:bg-elevated border border-border text-foreground hover:border-primary/40 transition-colors cursor-pointer select-none"
                title="Select Active Language Model or Agent"
              >
                {inferenceMode === "agent" ? (
                  <>
                    {getAgentIcon(activeAgent?.icon)}
                    <span className="font-medium text-xs truncate max-w-[140px] sm:max-w-[200px]">
                      {activeAgent?.name || "Agent"}
                    </span>
                    <span className="text-[10px] text-muted-foreground font-mono hidden md:inline">
                      ({activeAgent?.model?.split("/").pop() || "default"})
                    </span>
                  </>
                ) : (
                  <>
                    <Zap className="w-3.5 h-3.5 text-amber" />
                    <span className="font-medium text-xs truncate max-w-[140px] sm:max-w-[200px]">
                      {currentModelObj.name}
                    </span>
                    <span className="text-[10px] text-muted-foreground font-mono hidden md:inline">
                      (Direct)
                    </span>
                  </>
                )}
                <ChevronDown className="w-3 h-3 text-muted-foreground ml-0.5" />
              </button>

              {/* Mode Dropdown: Agent OR Direct Model */}
              {isModeDropdownOpen && (
                <div className="absolute bottom-full mb-2 left-0 w-80 rounded-2xl border border-border bg-surface shadow-xl p-2 z-50 animate-in fade-in-50 zoom-in-95">
                  <div className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground px-2 py-1 border-b border-border/40 mb-1 flex items-center justify-between">
                    <span>EXECUTION PERSONA OR MODEL</span>
                    <span className="text-primary font-semibold">[MUTUALLY EXCLUSIVE]</span>
                  </div>

                  <div className="max-h-72 overflow-y-auto space-y-3 p-1">
                    {/* Section 1: Specialized Agents */}
                    <div>
                      <div className="text-[10px] font-mono text-muted-foreground uppercase px-2 mb-1">
                        Specialized Agents
                      </div>
                      <div className="space-y-1">
                        {availableAgents.map((agent: AgentPersonaConfig) => {
                          const isSelected = inferenceMode === "agent" && activeAgent?.id === agent.id;
                          return (
                            <button
                              key={agent.id}
                              type="button"
                              onClick={() => {
                                selectAgent(agent);
                                setIsModeDropdownOpen(false);
                              }}
                              className={`w-full text-left px-2.5 py-1.5 rounded-lg flex items-start justify-between text-xs transition-colors cursor-pointer ${
                                isSelected
                                  ? "bg-primary/10 text-primary border border-primary/20"
                                  : "hover:bg-elevated text-foreground"
                              }`}
                            >
                              <div className="flex items-start gap-2 min-w-0">
                                <div className="mt-0.5 shrink-0">{getAgentIcon(agent.icon)}</div>
                                <div className="truncate">
                                  <div className="font-semibold text-xs truncate">{agent.name}</div>
                                  <div className="text-[10px] text-muted-foreground font-mono truncate">
                                    Model: {agent.model?.split("/").pop()} • {agent.role_title}
                                  </div>
                                </div>
                              </div>
                              {isSelected && <Check className="w-3.5 h-3.5 text-primary shrink-0 ml-1 mt-0.5" />}
                            </button>
                          );
                        })}
                      </div>
                    </div>

                    {/* Section 2: Direct Models (Raw LLM Mode) */}
                    <div>
                      <div className="text-[10px] font-mono text-muted-foreground uppercase px-2 mb-1 flex items-center justify-between">
                        <span>Direct Models ({availableModels.length})</span>
                      </div>

                      {/* Live Model Search Filter */}
                      <div className="relative px-1 mb-1.5">
                        <Search className="w-3 h-3 text-muted-foreground absolute left-3 top-2 pointer-events-none" />
                        <input
                          type="text"
                          placeholder="Filter models (e.g. claude, gpt, deepseek)..."
                          value={modelSearchQuery}
                          onChange={(e) => setModelSearchQuery(e.target.value)}
                          className="w-full pl-7 pr-2 py-1 text-[11px] rounded-lg border border-border bg-surface text-foreground placeholder:text-muted-foreground/60 focus:outline-none focus:border-primary/50"
                        />
                      </div>

                      <div className="space-y-1 max-h-48 overflow-y-auto">
                        {filteredModels.length === 0 ? (
                          <div className="text-[11px] text-muted-foreground px-2 py-2 text-center">
                            No models match &quot;{modelSearchQuery}&quot;
                          </div>
                        ) : (
                          filteredModels.map((m: ModelOption) => {
                            const isSelected = inferenceMode === "direct" && selectedModel === m.id;
                            return (
                              <button
                                key={m.id}
                                type="button"
                                onClick={() => {
                                  selectDirectModel(m.id);
                                  setIsModeDropdownOpen(false);
                                }}
                                className={`w-full text-left px-2.5 py-1.5 rounded-lg flex items-center justify-between text-xs transition-colors cursor-pointer ${
                                  isSelected
                                    ? "bg-amber/10 text-amber border border-amber/20"
                                    : "hover:bg-elevated text-foreground"
                                }`}
                              >
                                <div className="flex items-center gap-2 min-w-0">
                                  <Zap className="w-3.5 h-3.5 text-amber shrink-0" />
                                  <div className="truncate">
                                    <span className="font-medium truncate">{m.name}</span>
                                    <span className="text-[10px] text-muted-foreground font-mono ml-2 shrink-0">
                                      {formatContextK(m.context_window)}k ctx
                                    </span>
                                  </div>
                                </div>
                                {isSelected && <Check className="w-3.5 h-3.5 text-amber shrink-0 ml-1" />}
                              </button>
                            );
                          })
                        )}
                      </div>
                    </div>
                  </div>
                </div>
              )}
            </div>

            {/* Sandbox Runtime Indicator */}
            <span className="hidden sm:flex items-center gap-1.5 px-2.5 py-1 rounded-md bg-surface border border-border text-muted-foreground text-[11px] font-mono">
              <Server className="w-3 h-3 text-status-live" />
              <span>Sandbox Ready</span>
            </span>

            {/* Git Branch Badge (Displayed ONLY when a Git repository/branch is bound to the session) */}
            {session?.git_branch && (
              <span className="hidden md:flex items-center gap-1 px-2 py-1 rounded-md bg-surface border border-border text-muted-foreground text-[11px] font-mono">
                <GitBranch className="w-3 h-3 text-muted-foreground" />
                <span>{session.git_branch}</span>
              </span>
            )}
          </div>

          {/* Execution Trigger */}
          <button
            data-testid="ignite-button"
            onClick={handleIgnite}
            disabled={!prompt.trim() || isExecuting}
            className={`relative px-4 py-1.5 rounded-xl font-medium text-xs flex items-center gap-1.5 transition-all shadow-sm active:scale-95 overflow-hidden ${
              isThrustPulsing ? "animate-thrust-pulse" : ""
            } ${
              !prompt.trim() || isExecuting
                ? "bg-overlay text-muted-foreground cursor-not-allowed opacity-60"
                : "bg-primary hover:bg-primary-hover text-white cursor-pointer rocket-thrust-glow"
            }`}
          >
            {isExecuting ? (
              <>
                <Loader2 className="w-3.5 h-3.5 animate-spin" />
                <span>Running...</span>
              </>
            ) : (
              <>
                <Rocket
                  className={`w-3.5 h-3.5 -rotate-45 transition-transform ${
                    isThrustPulsing ? "translate-x-0.5 -translate-y-0.5 scale-110" : ""
                  }`}
                />
                <span>Run</span>
                <span className="text-[10px] opacity-70 font-mono">⌘⏎</span>
                {isThrustPulsing && (
                  <span className="absolute -bottom-1 left-2 w-4 h-4 bg-amber rounded-full blur-sm animate-ping" />
                )}
              </>
            )}
          </button>
        </div>
      </div>
    </div>
  );
};

"use client";

import React, { useState, useRef, useEffect } from "react";
import { useMissionStore } from "../lib/store";
import { newMessageId } from "../lib/message-id";
import { loadDraft, saveDraft } from "../lib/draft";
import { useAuth } from "./AuthProvider";
import { Server, GitBranch, Rocket, ChevronDown, Check, Bot, Zap, Shield, Bug, Book, Search, Square, ListPlus, X, Paperclip, FileText, AlertTriangle } from "lucide-react";
import { AgentPersonaConfig, ModelOption } from "../types/mission";
import { PermissionModePicker } from "./PermissionModePicker";
import { SandboxPicker } from "./SandboxPicker";
import { SlashCommandMenu } from "./SlashCommandMenu";

interface IgnitionConsoleProps {
  onIgnite: (
    prompt: string,
    model?: string,
    messageId?: string,
    attachmentIds?: string[]
  ) => void;
  onCancel?: () => void;
  initialPrompt?: string;
  /** The conversation is still opening: typing waits until its draft is known. */
  loading?: boolean;
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
  onCancel,
  initialPrompt = "",
  loading = false,
}) => {
  const [prompt, setPrompt] = useState<string>(initialPrompt);

  useEffect(() => {
    if (initialPrompt) {
      setPrompt(initialPrompt);
      textareaRef.current?.focus();
    }
  }, [initialPrompt]);
  const composerSeed = useMissionStore((state) => state.composerSeed);
  const clearComposerSeed = useMissionStore((state) => state.clearComposerSeed);
  const draftSessionId = useMissionStore((state) => state.session?.session_id);
  // The composer outlives navigation, so each conversation's unsent text is tied to it. Moving
  // from one conversation to another swaps the field for the other's draft (empty when it has
  // none) instead of carrying the first one's text over. Going from "no conversation yet" to the
  // one just created keeps what is typed.
  const previousSessionRef = useRef<string | undefined>(draftSessionId);
  useEffect(() => {
    const previous = previousSessionRef.current;
    previousSessionRef.current = draftSessionId;
    const saved = loadDraft(draftSessionId);
    if (previous && previous !== draftSessionId) setPrompt(saved);
    else if (saved) setPrompt((current) => current || saved);
  }, [draftSessionId]);
  useEffect(() => {
    const timer = setTimeout(() => saveDraft(draftSessionId, prompt), 300);
    return () => clearTimeout(timer);
  }, [draftSessionId, prompt]);
  // A starter or template drops its text in once; clearing it keeps a later mount (opening
  // another conversation) from filling the field with it again.
  useEffect(() => {
    if (composerSeed) {
      setPrompt(composerSeed.text);
      textareaRef.current?.focus();
      clearComposerSeed();
    }
  }, [composerSeed, clearComposerSeed]);
  const [isModeDropdownOpen, setIsModeDropdownOpen] = useState<boolean>(false);
  const [modelSearchQuery, setModelSearchQuery] = useState<string>("");
  const [isLaunchingRocket, setIsLaunchingRocket] = useState<boolean>(false);
  const [isThrustPulsing, setIsThrustPulsing] = useState<boolean>(false);
  const [isDragging, setIsDragging] = useState<boolean>(false);
  const dropdownRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const {
    flightLog,
    isExecuting,
    appendOwnUserMessage,
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
    queuedPrompt,
    setQueuedPrompt,
    pendingAttachments,
    attachmentError,
    uploadAttachment,
    removeAttachment,
    clearAttachments,
  } = useMissionStore();
  const { user } = useAuth();


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

  const stageFiles = (files: FileList | File[] | null | undefined) => {
    if (!files) return;
    Array.from(files).forEach((file) => {
      void uploadAttachment(file);
    });
  };

  const handlePaste = (e: React.ClipboardEvent<HTMLTextAreaElement>) => {
    const pasted = e.clipboardData?.files;
    if (pasted && pasted.length > 0) {
      e.preventDefault();
      stageFiles(pasted);
    }
  };

  const handleDrop = (e: React.DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    setIsDragging(false);
    stageFiles(e.dataTransfer?.files);
  };

  const handleIgnite = () => {
    const trimmed = prompt.trim();
    if (!trimmed && pendingAttachments.length === 0) return;

    if (isExecuting) {
      setQueuedPrompt(trimmed);
      setPrompt("");
      return;
    }

    // Never dispatch while a file is still uploading: the turn would start without it.
    if (pendingAttachments.some((a) => a.status === "uploading")) {
      setStatusMessage("Waiting for attachments to finish uploading...");
      return;
    }

    const readyAttachments = pendingAttachments.filter((a) => a.status === "ready");
    const attachmentIds = readyAttachments.map((a) => a.attachmentId);

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

    const messageId = newMessageId();
    appendOwnUserMessage({
      messageId,
      content: trimmed,
      authorUserId: user?.user_id ?? null,
      attachments: readyAttachments,
    });

    const targetModel = inferenceMode === "agent" ? activeAgent?.model || selectedModel : selectedModel;
    onIgnite(trimmed, targetModel, messageId, attachmentIds.length ? attachmentIds : undefined);
    setPrompt("");
    saveDraft(draftSessionId, "");
    setSuggestedFollowup(null);
    clearAttachments();
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    // Tab key: Autocomplete LLM-suggested follow-up question into composer
    if (e.key === "Tab" && !prompt && suggestedFollowup && !isExecuting) {
      e.preventDefault();
      setPrompt(suggestedFollowup);
      setSuggestedFollowup(null);
      return;
    }

    // Enter or ⌘⏎ / Ctrl⏎ to run or queue
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
    <div className="relative shrink-0 border-t border-border bg-surface/90 backdrop-blur-md p-3.5 select-none transition-colors">
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

      <div
        data-testid="composer"
        className={`bg-surface-card border rounded-2xl shadow-xl transition-all p-3 ${
          isDragging
            ? "border-brand ring-2 ring-brand/40"
            : "border-surface-border focus-within:border-brand/60 focus-within:ring-1 focus-within:ring-brand/40"
        }`}
        onDragOver={(e) => {
          e.preventDefault();
          setIsDragging(true);
        }}
        onDragLeave={() => setIsDragging(false)}
        onDrop={handleDrop}
      >
        {/* Attachment staging row */}
        {(pendingAttachments.length > 0 || attachmentError) && (
          <div data-testid="attachment-tray" className="mb-2 flex flex-wrap gap-2">
            {pendingAttachments.map((att) => (
              <div
                key={att.attachmentId}
                data-testid="attachment-chip"
                data-status={att.status}
                className={`group flex items-center gap-2 pl-1.5 pr-1 py-1 rounded-xl border text-xs ${
                  att.status === "error"
                    ? "border-rose-500/40 bg-rose-500/10 text-rose-300"
                    : att.status === "uploading"
                    ? "border-border bg-surface text-muted-foreground"
                    : "border-border bg-surface text-foreground"
                }`}
              >
                {att.preview ? (
                  // eslint-disable-next-line @next/next/no-img-element
                  <img
                    src={att.preview}
                    alt={att.name}
                    className="w-7 h-7 rounded-md object-cover border border-border"
                  />
                ) : att.status === "error" ? (
                  <AlertTriangle className="w-4 h-4 shrink-0" />
                ) : (
                  <FileText className="w-4 h-4 shrink-0 text-muted-foreground" />
                )}
                <span className="max-w-[160px] truncate font-mono">{att.name}</span>
                <span className="text-[10px] text-muted-foreground font-mono">
                  {att.status === "uploading"
                    ? "uploading…"
                    : att.status === "error"
                    ? "failed"
                    : `${Math.max(1, Math.round(att.size / 1024))} KB`}
                </span>
                <button
                  type="button"
                  data-testid="attachment-remove"
                  onClick={() => removeAttachment(att.attachmentId)}
                  className="p-0.5 rounded hover:bg-elevated text-muted-foreground hover:text-foreground cursor-pointer"
                  title={`Remove ${att.name}`}
                  aria-label={`Remove ${att.name}`}
                >
                  <X className="w-3.5 h-3.5" />
                </button>
              </div>
            ))}
            {attachmentError && pendingAttachments.every((a) => a.status !== "error") && (
              <div className="flex items-center gap-1.5 px-2 py-1 rounded-xl border border-rose-500/40 bg-rose-500/10 text-rose-300 text-[11px] font-mono">
                <AlertTriangle className="w-3.5 h-3.5" />
                <span>{attachmentError}</span>
              </div>
            )}
          </div>
        )}

        {/* Queued Prompt Banner */}
        {queuedPrompt && (
          <div className="flex items-center justify-between gap-2 px-3 py-1.5 mb-2 rounded-xl bg-primary/10 border border-primary/25 text-xs text-primary font-mono">
            <div className="flex items-center gap-2 truncate">
              <span className="font-semibold uppercase tracking-wider text-[10px] bg-primary/20 text-primary px-1.5 py-0.5 rounded">
                Queued Next
              </span>
              <span className="truncate text-foreground/90 font-sans">{queuedPrompt}</span>
            </div>
            <button
              type="button"
              onClick={() => setQueuedPrompt(null)}
              className="text-muted-foreground hover:text-foreground p-0.5 rounded transition-colors"
              title="Cancel queued prompt"
            >
              <X className="w-3.5 h-3.5" />
            </button>
          </div>
        )}

        {/* Text Input Area & Ghost Autocomplete Overlay */}
        <div className="relative">
          <SlashCommandMenu
            input={prompt}
            onPick={(completion) => {
              setPrompt(completion);
              textareaRef.current?.focus();
            }}
          />
          <textarea
            ref={textareaRef}
            data-testid="instruction-input"
            readOnly={loading}
            aria-busy={loading}
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            onKeyDown={handleKeyDown}
            onPaste={handlePaste}
            placeholder={
              loading
                ? "Opening the conversation…"
                : isExecuting
                ? "Agent is running... Type your next instruction to queue it (Enter to queue)"
                : suggestedFollowup
                ? ""
                : "Describe what you want done… (Enter to run, Shift+Enter for a new line)"
            }
            rows={2}
            className="w-full resize-none bg-transparent text-sm font-sans text-foreground placeholder:text-neutral-500 focus:outline-none leading-relaxed p-1 relative z-10"
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
          <div className="flex min-w-0 flex-wrap items-center gap-2">
            <input
              ref={fileInputRef}
              type="file"
              multiple
              data-testid="attachment-input"
              className="hidden"
              onChange={(e) => {
                stageFiles(e.target.files);
                e.target.value = "";
              }}
            />
            <button
              type="button"
              data-testid="attach-button"
              onClick={() => fileInputRef.current?.click()}
              className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg bg-surface hover:bg-elevated border border-border text-muted-foreground hover:text-foreground hover:border-primary/40 transition-colors cursor-pointer"
              title="Attach a screenshot or log file (or drag / paste)"
            >
              <Paperclip className="w-3.5 h-3.5" />
              <span className="hidden sm:inline text-xs">Attach</span>
            </button>
            <PermissionModePicker disabled={isExecuting} />
            {!session || session.is_draft ? <SandboxPicker disabled={isExecuting} /> : null}
            <div className="relative" ref={dropdownRef}>
              <button
                type="button"
                data-testid="agent-model-picker"
                aria-label="Agent or model for your next message"
                aria-expanded={isModeDropdownOpen}
                onClick={() => setIsModeDropdownOpen(!isModeDropdownOpen)}
                className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg bg-surface hover:bg-elevated border border-border text-foreground hover:border-primary/40 transition-colors cursor-pointer select-none"
                title="Agent or model for your next message"
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
                      (no agent)
                    </span>
                  </>
                )}
                <ChevronDown className="w-3 h-3 text-muted-foreground ml-0.5" />
              </button>

              {/* Mode Dropdown: Agent OR Direct Model */}
              {isModeDropdownOpen && (
                <div className="absolute bottom-full mb-2 left-0 w-80 rounded-2xl border border-border bg-surface shadow-xl p-2 z-50 animate-in fade-in-50 zoom-in-95">
                  <div className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground px-2 py-1 border-b border-border/40 mb-1 flex items-center justify-between">
                    <span>Agent or model</span>
                    <span className="text-muted-foreground">
                      {isExecuting ? "Applies to your next message" : "Choose one"}
                    </span>
                  </div>

                  <div className="max-h-72 overflow-y-auto space-y-3 p-1">
                    {/* Section 1: Specialized Agents */}
                    <div>
                      <div className="text-[10px] font-mono text-muted-foreground uppercase px-2 mb-1">
                        Specialized Agents
                      </div>
                      <div className="space-y-1">
                        {availableAgents
                          .filter((agent: AgentPersonaConfig) => agent.enabled !== false)
                          .map((agent: AgentPersonaConfig) => {
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
                        <span>Models, no agent ({availableModels.length})</span>
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

          {/* Execution Controls: Stop, Queue, Run */}
          <div className="flex items-center gap-1.5">
            {isExecuting && (
              <button
                type="button"
                onClick={onCancel}
                data-testid="stop-button"
                className="relative px-3 py-1.5 rounded-xl font-medium text-xs flex items-center gap-1.5 transition-all shadow-sm active:scale-95 bg-rose-600/90 hover:bg-rose-600 text-white cursor-pointer"
                title="Interrupt ongoing execution"
              >
                <Square className="w-3 h-3 fill-current" />
                <span>Stop</span>
              </button>
            )}

            {isExecuting ? (
              <button
                type="button"
                onClick={handleIgnite}
                disabled={!prompt.trim()}
                data-testid="queue-button"
                className={`relative px-3.5 py-1.5 rounded-xl font-medium text-xs flex items-center gap-1.5 transition-all shadow-sm active:scale-95 ${
                  !prompt.trim()
                    ? "bg-overlay text-muted-foreground cursor-not-allowed opacity-50"
                    : "bg-primary hover:bg-primary-hover text-white cursor-pointer"
                }`}
                title="Queue this prompt to run immediately after current turn"
              >
                <ListPlus className="w-3.5 h-3.5" />
                <span>Queue</span>
              </button>
            ) : (
              <button
                data-testid="ignite-button"
                onClick={handleIgnite}
                disabled={!prompt.trim() && pendingAttachments.length === 0}
                className={`relative px-4 py-1.5 rounded-xl font-medium text-xs flex items-center gap-1.5 transition-all shadow-sm active:scale-95 overflow-hidden ${
                  isThrustPulsing ? "animate-thrust-pulse" : ""
                } ${
                  !prompt.trim() && pendingAttachments.length === 0
                    ? "bg-overlay text-muted-foreground cursor-not-allowed opacity-60"
                    : "bg-primary hover:bg-primary-hover text-white cursor-pointer rocket-thrust-glow"
                }`}
              >
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
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
};

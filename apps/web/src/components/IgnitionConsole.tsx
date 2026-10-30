"use client";

import { launchRocket } from "./LaunchRocket";
import { useShallow } from "zustand/react/shallow";
import React, { useState, useRef, useEffect } from "react";
import { useMissionStore } from "../lib/store";
import { newMessageId } from "../lib/message-id";
import { loadDraft, saveDraft } from "../lib/draft";
import { useAuth } from "./AuthProvider";
import { AlertTriangle, Book, Bot, Brain, Bug, Check, ChevronDown, Compass, FileText, GitBranch, ListPlus, MessageSquarePlus, Paperclip, Rocket, Search, SearchCheck, Shield, Square, X, Zap } from "lucide-react";
import { AgentPersonaConfig, ModelOption } from "../types/mission";
import { PermissionModePicker } from "./PermissionModePicker";
import { SandboxPicker } from "./SandboxPicker";
import { SlashCommandMenu, useSlashMatches, type SlashCommand } from "./SlashCommandMenu";
import { builtinCommand, runBuiltin } from "../lib/composer-commands";
import { useRouter } from "next/navigation";
import { MentionMenu } from "./composer/MentionMenu";
import { ComposerHints } from "./composer/ComposerHints";
import { mentionAt, useMentions, type MentionItem } from "./composer/useMentions";

interface IgnitionConsoleProps {
  onIgnite: (
    prompt: string,
    model?: string,
    messageId?: string,
    attachmentIds?: string[],
    delivery?: "steer" | "queue",
    files?: File[],
    reasoningEffort?: string
  ) => void;
  onCancel?: () => void;
  /** Withdraw a message sent during the turn that no turn has taken yet. */
  onCancelPending?: (messageId: string) => void;
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
    case "search-check":
      return <SearchCheck className="w-3.5 h-3.5 text-sky-600 dark:text-sky-400" />;
    case "compass":
      return <Compass className="w-3.5 h-3.5 text-indigo-600 dark:text-indigo-400" />;
    case "bot":
    default:
      return <Bot className="w-3.5 h-3.5 text-primary" />;
  }
};

/**
 * What the next message will do to the workspace, shown only when it is not the usual case: a
 * running or not-yet-created sandbox is the norm and needs no label.
 */
const SandboxState: React.FC<{ status?: string; isDraft: boolean }> = ({ status, isDraft }) => {
  if (isDraft || !status || status === "non_existent" || status === "running") return null;
  const asleep = status === "hibernated" || status === "stopped" || status === "paused";
  const label = asleep ? "Sandbox asleep, wakes on send" : `Sandbox ${status.replace(/_/g, " ")}`;
  return (
    <span
      data-testid="sandbox-state"
      title={label}
      className="flex items-center gap-1.5 px-2 py-1 text-[11px] text-muted-foreground sm:px-2.5 sm:py-1.5"
    >
      <span className={`h-1.5 w-1.5 rounded-full ${asleep ? "bg-amber-500" : "bg-neutral-400"}`} aria-hidden="true" />
      <span className="sr-only sm:not-sr-only">{label}</span>
    </span>
  );
};

const REASONING_CHOICES: { value: string; label: string }[] = [
  { value: "", label: "Auto" },
  { value: "none", label: "Off" },
  { value: "minimal", label: "Minimal" },
  { value: "low", label: "Low" },
  { value: "medium", label: "Medium" },
  { value: "high", label: "High" },
];

export const IgnitionConsole: React.FC<IgnitionConsoleProps> = ({
  onIgnite,
  onCancel,
  onCancelPending,
  initialPrompt = "",
  loading = false,
}) => {
  const [prompt, setPrompt] = useState<string>(initialPrompt);
  const [reasoningEffort, setReasoningEffort] = useState<string | undefined>(undefined);
  const [slashIndex, setSlashIndex] = useState(0);
  const [commandNotice, setCommandNotice] = useState("");
  const router = useRouter();
  const slashMatches = useSlashMatches(prompt);

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
  const [isThrustPulsing, setIsThrustPulsing] = useState<boolean>(false);
  const [isDragging, setIsDragging] = useState<boolean>(false);
  const [caret, setCaret] = useState<number>(0);
  const [mentionIndex, setMentionIndex] = useState<number>(0);
  const dropdownRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  // The field grows with what is typed (up to 40% of the viewport, then it scrolls).
  useEffect(() => {
    const field = textareaRef.current;
    if (!field) return;
    field.style.height = "auto";
    field.style.height = `${field.scrollHeight}px`;
  }, [prompt]);

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
    activeAgent,
    availableAgents,
    inferenceMode,
    selectAgent,
    selectDirectModel,
    suggestedFollowup,
    setSuggestedFollowup,
    pendingAttachments,
    attachmentError,
    uploadAttachment,
    removeAttachment,
    clearAttachments,
    pendingMessages,
    addPendingMessage,
  } = useMissionStore(
    useShallow((state) => ({ flightLog: state.flightLog, isExecuting: state.isExecuting, appendOwnUserMessage: state.appendOwnUserMessage, session: state.session, clearReasoning: state.clearReasoning, setIsExecuting: state.setIsExecuting, setStatusMessage: state.setStatusMessage, selectedModel: state.selectedModel, availableModels: state.availableModels, activeAgent: state.activeAgent, availableAgents: state.availableAgents, inferenceMode: state.inferenceMode, selectAgent: state.selectAgent, selectDirectModel: state.selectDirectModel, suggestedFollowup: state.suggestedFollowup, setSuggestedFollowup: state.setSuggestedFollowup, pendingAttachments: state.pendingAttachments, attachmentError: state.attachmentError, uploadAttachment: state.uploadAttachment, removeAttachment: state.removeAttachment, clearAttachments: state.clearAttachments, pendingMessages: state.pendingMessages, addPendingMessage: state.addPendingMessage }))
  );
  const { user } = useAuth();
  const realSessionId = session && !session.is_draft && session.session_id !== "draft" ? session.session_id : null;
  const mentionToken = mentionAt(prompt, caret);
  const mentionItems = useMentions(realSessionId, mentionToken);
  const mentionOpen = mentionToken !== null && mentionItems.length > 0;

  const pickMention = (item: MentionItem) => {
    if (!mentionToken) return;
    const insert = `@${item.path}${item.isDir ? "/" : " "}`;
    const next = prompt.slice(0, mentionToken.start) + insert + prompt.slice(caret);
    setPrompt(next);
    const position = mentionToken.start + insert.length;
    setCaret(position);
    setMentionIndex(0);
    requestAnimationFrame(() => {
      textareaRef.current?.focus();
      textareaRef.current?.setSelectionRange(position, position);
    });
  };


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

  // The reasoning level is the conversation's (saved on the session), not the next message's.
  useEffect(() => {
    const saved = session?.metadata?.reasoning_effort;
    setReasoningEffort(typeof saved === "string" && saved ? saved : undefined);
  }, [session?.session_id, session?.metadata?.reasoning_effort]);
  const chooseReasoning = (value: string | undefined) => {
    setReasoningEffort(value);
    if (!session || session.is_draft || session.session_id === "draft") return;
    fetch(`/v1/sessions/${session.session_id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ metadata: { reasoning_effort: value ?? null } }),
    }).catch(() => undefined);
  };

  const runCommand = (command: { name: string; argument: string }) => {
    setPrompt("");
    saveDraft(draftSessionId, "");
    void runBuiltin(command, () => {
      useMissionStore.getState().initDraftSession();
      router.push("/chat/draft");
    }).then(setCommandNotice);
  };

  const pickSlash = (command: SlashCommand) => {
    setSlashIndex(0);
    // A command that needs nothing more runs at once; the others wait for their argument.
    if (command.builtin && command.name !== "model" && command.name !== "agent") {
      runCommand({ name: command.name, argument: "" });
      return;
    }
    setPrompt(`/${command.name} `);
    textareaRef.current?.focus();
  };

  const handleIgnite = (delivery: "steer" | "queue" = "steer") => {
    const trimmed = prompt.trim();
    if (!trimmed && pendingAttachments.length === 0) return;
    const command = builtinCommand(trimmed);
    if (command) {
      runCommand(command);
      return;
    }
    setCommandNotice("");

    // Never dispatch while a file is still uploading: the turn would start without it.
    if (pendingAttachments.some((a) => a.status === "uploading")) {
      setStatusMessage("Waiting for attachments to finish uploading...");
      return;
    }

    const readyAttachments = pendingAttachments.filter((a) => a.status === "ready" || a.status === "staged");
    const attachmentIds = readyAttachments.filter((a) => a.status === "ready").map((a) => a.attachmentId);
    const stagedFiles = readyAttachments.flatMap((a) => (a.file ? [a.file] : []));

    // While a turn runs, the message goes to it at its next step (the server starts it as the
    // next turn when it cannot): the running turn's state is left as it is.
    if (!isExecuting) {
      // Trigger cool rocket lift-off on the first user message of a session
      const hasUserMessages = flightLog.some((e) => e.role === "USER");
      if (!hasUserMessages) {
        const run = document.querySelector('[data-testid="ignite-button"]')?.getBoundingClientRect();
        if (run) launchRocket({ x: run.left + run.width / 2, y: run.top + run.height / 2 });
      }

      // Trigger thrust pulse on send button
      setIsThrustPulsing(true);
      setTimeout(() => setIsThrustPulsing(false), 300);

      clearReasoning();
      setIsExecuting(true);
      setStatusMessage("Sending your message…");
    }

    const messageId = newMessageId();
    // During a turn the message waits above the composer until a turn takes it; it enters the
    // conversation where the agent read it, not where it was typed.
    const waitsForATurn = isExecuting;
    if (waitsForATurn) {
      addPendingMessage({
        messageId,
        content: trimmed,
        delivery,
        ...(readyAttachments.length ? { attachments: readyAttachments.map((a) => a.name) } : {}),
      });
    } else {
      appendOwnUserMessage({
        messageId,
        content: trimmed,
        authorUserId: user?.user_id ?? null,
        attachments: readyAttachments,
      });
    }

    const targetModel = inferenceMode === "agent" ? activeAgent?.model || selectedModel : selectedModel;
    onIgnite(
      trimmed,
      targetModel,
      messageId,
      attachmentIds.length ? attachmentIds : undefined,
      delivery,
      stagedFiles.length ? stagedFiles : undefined,
      reasoningEffort,
    );
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

    if (slashMatches.length > 0) {
      if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        e.preventDefault();
        const step = e.key === "ArrowDown" ? 1 : -1;
        setSlashIndex((i) => (i + step + slashMatches.length) % slashMatches.length);
        return;
      }
      if (e.key === "Tab" || (e.key === "Enter" && !e.shiftKey)) {
        e.preventDefault();
        pickSlash(slashMatches[Math.min(slashIndex, slashMatches.length - 1)]);
        return;
      }
    }

    if (mentionOpen) {
      if (e.key === "ArrowDown") {
        e.preventDefault();
        setMentionIndex((i) => Math.min(i + 1, mentionItems.length - 1));
        return;
      }
      if (e.key === "ArrowUp") {
        e.preventDefault();
        setMentionIndex((i) => Math.max(i - 1, 0));
        return;
      }
      if (e.key === "Enter" || e.key === "Tab") {
        e.preventDefault();
        pickMention(mentionItems[Math.min(mentionIndex, mentionItems.length - 1)]);
        return;
      }
      if (e.key === "Escape") {
        e.preventDefault();
        setCaret(0);
        return;
      }
    }

    // Enter or ⌘⏎ / Ctrl⏎ to run, or to send to the running turn; Alt⏎ queues it after the turn
    if (e.key === "Enter") {
      if (!e.shiftKey) {
        e.preventDefault();
        handleIgnite(isExecuting && e.altKey ? "queue" : "steer");
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
        {isDragging && (
          <p data-testid="drop-hint" className="mb-2 rounded-lg border border-dashed border-brand px-3 py-2 text-center text-xs text-brand">
            Drop files to attach them to your next message
          </p>
        )}
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

        {/* Messages sent during the turn, waiting for a turn to take them */}
        {pendingMessages.length > 0 && (
          <ul data-testid="pending-messages" aria-label="Messages waiting for the agent" className="flex flex-col gap-1.5 px-3 pt-3">
            {pendingMessages.map((message) => (
              <li
                key={message.messageId}
                data-testid={`pending-message-${message.delivery}`}
                className="flex items-center gap-2 rounded-xl border border-primary/30 bg-primary/5 px-2.5 py-1.5 text-xs"
              >
                <span className="shrink-0 font-medium text-foreground">
                  {message.delivery === "queue" ? "Queued · after this turn" : "Steer · delivered at the next step"}
                </span>
                <span className="min-w-0 flex-1 truncate text-muted-foreground">{message.content}</span>
                {message.attachments && message.attachments.length > 0 && (
                  <span className="shrink-0 text-muted-foreground" title={message.attachments.join(", ")}>
                    <Paperclip className="inline h-3 w-3" aria-hidden="true" /> {message.attachments.length}
                  </span>
                )}
                {onCancelPending && (
                  <button
                    type="button"
                    data-testid="pending-message-cancel"
                    onClick={() => onCancelPending(message.messageId)}
                    className="shrink-0 rounded p-0.5 text-muted-foreground hover:bg-elevated hover:text-foreground cursor-pointer"
                    aria-label={`Withdraw the message: ${message.content}`}
                    title="Withdraw it before the agent reads it"
                  >
                    <X className="h-3.5 w-3.5" />
                  </button>
                )}
              </li>
            ))}
          </ul>
        )}

        {/* Text Input Area & Ghost Autocomplete Overlay */}
        <div className="relative">
          <MentionMenu items={mentionItems} active={mentionIndex} onPick={pickMention} />
          <SlashCommandMenu matches={slashMatches} active={Math.min(slashIndex, slashMatches.length - 1)} onPick={pickSlash} />
          {commandNotice && (
            <p role="status" data-testid="command-notice" className="mb-1 text-xs text-neutral-700 dark:text-neutral-300">
              {commandNotice}
            </p>
          )}
          <textarea
            ref={textareaRef}
            data-testid="instruction-input"
            readOnly={loading}
            aria-busy={loading}
            value={prompt}
            onChange={(e) => {
              setPrompt(e.target.value);
              setSlashIndex(0);
              setCaret(e.target.selectionStart ?? e.target.value.length);
              setMentionIndex(0);
            }}
            onSelect={(e) => setCaret((e.target as HTMLTextAreaElement).selectionStart ?? 0)}
            onKeyDown={handleKeyDown}
            onPaste={handlePaste}
            aria-label="Message the agent"
            aria-controls={slashMatches.length > 0 ? "slash-menu" : undefined}
            aria-activedescendant={slashMatches.length > 0 ? `slash-option-${Math.min(slashIndex, slashMatches.length - 1)}` : undefined}
            placeholder={
              loading
                ? "Opening the conversation…"
                : isExecuting
                ? "Send a message: the agent reads it at its next step"
                : suggestedFollowup
                ? ""
                : "What should we work on?"
            }
            rows={2}
            style={{ maxHeight: "40vh" }}
            className="w-full resize-none overflow-y-auto bg-transparent text-sm font-sans text-foreground placeholder:text-neutral-500 focus:outline-none leading-relaxed p-1 relative z-10"
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
        <div className="mt-1.5 pt-1.5 sm:mt-2.5 sm:pt-2 border-t border-border/50 flex flex-wrap items-center justify-between gap-1.5 sm:gap-2 text-xs">
          {/* Controls: Mode Selector (Agent OR Direct Model) */}
          <div className="flex min-w-0 flex-wrap items-center gap-1.5 sm:gap-2">
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
              className="flex items-center gap-1.5 px-2 py-1 sm:px-2.5 sm:py-1.5 rounded-lg bg-surface hover:bg-elevated border border-border text-muted-foreground hover:text-foreground hover:border-primary/40 transition-colors cursor-pointer"
              title="Attach a screenshot or log file (or drag / paste)"
            >
              <Paperclip className="w-3.5 h-3.5" />
              <span className="sr-only">Attach</span>
            </button>
            {/* One status line, read left to right: how closely it is supervised, who works, where. */}
            <div className="flex min-w-0 flex-wrap items-center divide-x divide-border rounded-xl border border-border bg-surface">
            <PermissionModePicker disabled={isExecuting} bare />
            <div className="relative" ref={dropdownRef}>
              <button
                type="button"
                data-testid="agent-model-picker"
                aria-label="Agent or model for your next message"
                aria-expanded={isModeDropdownOpen}
                onClick={() => setIsModeDropdownOpen(!isModeDropdownOpen)}
                className="flex items-center gap-1 px-2 py-1 text-foreground hover:bg-elevated transition-colors cursor-pointer select-none first:rounded-l-xl sm:gap-1.5 sm:px-2.5 sm:py-1.5"
                title="Agent or model for your next message"
              >
                {inferenceMode === "agent" ? (
                  <>
                    {getAgentIcon(activeAgent?.icon)}
                    <span className="font-medium text-xs truncate max-w-[92px] sm:max-w-[200px]">
                      {activeAgent?.name || "Agent"}
                    </span>
                    <span className="text-[10px] text-muted-foreground font-mono hidden md:inline">
                      ({activeAgent?.model?.split("/").pop() || "default"})
                    </span>
                  </>
                ) : (
                  <>
                    <Zap className="w-3.5 h-3.5 text-amber" />
                    <span className="font-medium text-xs truncate max-w-[92px] sm:max-w-[200px]">
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

            {!session || session.is_draft ? <SandboxPicker disabled={isExecuting} bare /> : null}
            <SandboxState status={session?.sandbox_status} isDraft={!session || !!session.is_draft} />

            {/* Inline Reasoning Effort Selector */}
            <div className="flex items-center gap-1.5 px-2 py-1 text-[11px] text-muted-foreground sm:px-2.5 sm:py-1.5">
              <Brain className="w-3.5 h-3.5 text-brand" />
              <label htmlFor="composer-reasoning" className="sr-only">
                Reasoning for this conversation
              </label>
              <select
                id="composer-reasoning"
                data-testid="reasoning-effort-selector"
                value={reasoningEffort ?? ""}
                onChange={(e) => chooseReasoning(e.target.value || undefined)}
                title="How much the model reasons in this conversation (Auto follows the agent and the organization)"
                className="cursor-pointer bg-transparent font-medium text-foreground focus:outline-none"
              >
                {REASONING_CHOICES.map((choice) => (
                  <option key={choice.value} value={choice.value}>
                    {choice.label}
                  </option>
                ))}
              </select>
            </div>

            {/* Git Branch Badge (Displayed ONLY when a Git repository/branch is bound to the session) */}
            {session?.git_branch && (
              <span className="hidden md:flex items-center gap-1 px-2.5 py-1.5 text-muted-foreground text-[11px] font-mono">
                <GitBranch className="w-3 h-3 text-muted-foreground" />
                <span>{session.git_branch}</span>
              </span>
            )}
            </div>
          </div>

          {/* Execution Controls: Stop, Send to the running turn, Run */}
          <div className="flex items-center gap-1.5">
            <ComposerHints />
            {isExecuting && (
              <button
                type="button"
                onClick={onCancel}
                data-testid="stop-button"
                className="relative px-3 py-1.5 rounded-xl font-medium text-xs flex items-center gap-1.5 transition-all active:scale-95 border border-rose-500/60 text-rose-600 hover:bg-rose-500/10 dark:text-rose-400 cursor-pointer"
                title="Stop the agent now"
              >
                <Square className="w-3 h-3 fill-current" />
                <span>Stop</span>
              </button>
            )}

            {isExecuting ? (
              <button
                type="button"
                onClick={() => handleIgnite("queue")}
                disabled={!prompt.trim()}
                data-testid="queue-button"
                className={`relative px-3 py-1.5 rounded-xl font-medium text-xs flex items-center gap-1.5 transition-all active:scale-95 border ${
                  !prompt.trim()
                    ? "border-border text-muted-foreground cursor-not-allowed"
                    : "border-primary/50 text-foreground hover:bg-primary/10 cursor-pointer"
                }`}
                title="Run this after the current turn, as a turn of its own (Alt⏎)"
              >
                <ListPlus className="w-3.5 h-3.5" />
                <span>Queue</span>
              </button>
            ) : null}
            {isExecuting ? (
              <button
                type="button"
                onClick={() => handleIgnite("steer")}
                disabled={!prompt.trim()}
                data-testid="steer-button"
                className={`relative px-3.5 py-1.5 rounded-xl font-medium text-xs flex items-center gap-1.5 transition-all shadow-sm active:scale-95 ${
                  !prompt.trim()
                    ? "border border-border text-muted-foreground cursor-not-allowed"
                    : "bg-primary hover:bg-primary-hover text-white cursor-pointer"
                }`}
                title="Send this to the running agent: it reads it at its next step"
              >
                <MessageSquarePlus className="w-3.5 h-3.5" />
                <span>Send</span>
              </button>
            ) : (
              <button
                data-testid="ignite-button"
                onClick={() => handleIgnite()}
                disabled={!prompt.trim() && pendingAttachments.length === 0}
                className={`relative px-4 py-1.5 rounded-xl font-medium text-xs flex items-center gap-1.5 transition-all shadow-sm active:scale-95 overflow-hidden ${
                  isThrustPulsing ? "animate-thrust-pulse" : ""
                } ${
                  !prompt.trim() && pendingAttachments.length === 0
                    ? "border border-border bg-surface text-muted-foreground cursor-not-allowed"
                    : "bg-primary hover:bg-primary-hover text-white cursor-pointer rocket-thrust-glow"
                }`}
              >
                <Rocket
                  className={`w-3.5 h-3.5 -rotate-45 transition-transform ${
                    isThrustPulsing ? "translate-x-0.5 -translate-y-0.5 scale-110" : ""
                  }`}
                />
                <span>Run</span>
                <span className="text-[10px] opacity-70 font-mono" aria-hidden="true">⏎</span>
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

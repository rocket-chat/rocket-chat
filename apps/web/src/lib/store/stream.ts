import type { StateCreator } from "zustand";
import { formatEventTime, matchesPendingEcho } from "./helpers";
import type { MissionStore, StreamSlice, TurnProgress } from "./types";

const TERMINAL_MAX_CHARS = 400_000;
const TERMINAL_KEEP_CHARS = 300_000;
const LIVE_OUTPUT_TAIL_CHARS = 6_000;
const DEDUPE_WINDOW = 200;
const NO_TURN_PROGRESS: TurnProgress = { startedAt: null, step: null, lastActivityAt: null, currentTool: null };

/** Steps kept on a subagent card (the newest); the Subagents tab reads the whole transcript. */
const MAX_SUBAGENT_STEPS = 50;

export const createStreamSlice: StateCreator<MissionStore, [], [], StreamSlice> = (set, get) => ({
  connected: false,
  statusMessage: "System Ready. Standing by for instructions.",
  isExecuting: false,
  isStreamContinuous: true,
  pendingSelfPrompts: [],
  flightLog: [],
  reasoningStream: "",
  isReasoningActive: false,
  activeDecision: null,
  activeApproval: null,
  activeWait: null,
  setActiveWait: (activeWait) => set({ activeWait }),
  checklist: [],
  fileDiffs: [],
  goal: null,
  processesVersion: 0,
  bumpProcesses: () => set((state) => ({ processesVersion: state.processesVersion + 1 })),
  setGoal: (goal) => set({ goal }),
  selectedChangePath: null,
  changesVersion: 0,
  terminalLogs: "\x1b[38;5;208m[SYSTEM]\x1b[0m Session workspace attached.\r\n",
  terminalBase: 0,
  terminalEpoch: 0,
  liveToolOutput: {},
  tokenRate: 0,
  contextTokens: null,
  contextLimit: null,
  contextModelLimit: null,
  contextTargetTokens: null,
  contextSource: null,
  contextTier2Tokens: null,
  contextTier3Tokens: null,
  setContextUsage: (contextTokens, contextLimit, details) =>
    set({
      contextTokens,
      contextLimit,
      contextModelLimit: details?.modelLimit ?? null,
      contextTargetTokens: details?.targetTokens ?? null,
      contextSource: details?.source ?? null,
      contextTier2Tokens: details?.tier2Tokens ?? null,
      contextTier3Tokens: details?.tier3Tokens ?? null,
    }),
  turnProgress: { ...NO_TURN_PROGRESS },
  noteTurnProgress: (update) => set((state) => ({ turnProgress: { ...state.turnProgress, ...update } })),
  resetTurnProgress: () => set({ turnProgress: { ...NO_TURN_PROGRESS } }),
  turnBinding: null,
  artifacts: [],
  upsertArtifact: (artifact) =>
    set((state) => ({
      artifacts: [artifact, ...state.artifacts.filter((a) => a.id !== artifact.id)],
    })),
  setArtifacts: (artifacts) => set({ artifacts }),
  selectedArtifactId: null,
  selectArtifact: (selectedArtifactId) => set({ selectedArtifactId }),
  openArtifact: (id) => {
    set({ selectedArtifactId: id, inspectorTab: "artifacts" });
    get().setIsRightPanelOpen(true);
  },
  setTurnBinding: (turnBinding) => set({ turnBinding }),
  setConnected: (connected) => set({ connected }),
  setStatusMessage: (msg) => set({ statusMessage: msg }),
  setIsExecuting: (exec) => set({ isExecuting: exec }),
  updateToolCall: (nameOrId, data) =>
    set((state) => {
      const logs = [...state.flightLog];
      for (let i = logs.length - 1; i >= 0; i--) {
        const item = logs[i];
        if (item.toolCall && (item.toolCall.id === nameOrId || item.toolCall.name === nameOrId)) {
          logs[i] = {
            ...item,
            toolCall: {
              ...item.toolCall,
              ...data,
            },
          };
          return { flightLog: logs };
        }
      }
      return state;
    }),
  updateSubagent: (subagentId, data) =>
    set((state) => {
      const logs = [...state.flightLog];
      for (let i = logs.length - 1; i >= 0; i--) {
        const item = logs[i];
        if (item.subagent && item.subagent.subagentId === subagentId) {
          logs[i] = {
            ...item,
            subagent: {
              ...item.subagent,
              ...data,
            },
          };
          return { flightLog: logs };
        }
      }
      return state;
    }),
  upsertSubagentStep: (subagentId, step) =>
    set((state) => {
      const index = state.flightLog.findLastIndex((item) => item.subagent?.subagentId === subagentId);
      if (index < 0) return state;
      const logs = [...state.flightLog];
      const item = logs[index];
      const steps = [...(item.subagent?.steps ?? [])];
      const at = steps.findIndex((s) => s.id === step.id);
      if (at >= 0) steps[at] = { ...steps[at], ...step };
      // A child's step list is bounded: its transcript has every step.
      else steps.push(step);
      logs[index] = { ...item, subagent: { ...item.subagent!, steps: steps.slice(-MAX_SUBAGENT_STEPS) } };
      return { flightLog: logs };
    }),
  updateWait: (waitId, data) => {
    const index = get().flightLog.findLastIndex((item) => item.wait?.waitId === waitId);
    if (index < 0) return false;
    set((state) => {
      const logs = [...state.flightLog];
      const item = logs[index];
      if (item?.wait) logs[index] = { ...item, wait: { ...item.wait, ...data } };
      return { flightLog: logs };
    });
    return true;
  },
  appendFlightLog: (entry) =>
    set((state) => {
      // The same event applied twice (a replay overlapping a snapshot) must not show twice.
      const recent = state.flightLog.slice(-DEDUPE_WINDOW);
      if (entry.id && recent.some((item) => item.id === entry.id)) return state;
      return { flightLog: [...state.flightLog, entry] };
    }),
  appendOwnUserMessage: (message) =>
    set((state) => {
      const content = message.content.trim();
      if (!content) return state;
      return {
        flightLog: [
          ...state.flightLog,
          {
            id: message.messageId,
            role: "USER" as const,
            content,
            timestamp: formatEventTime(),
            messageId: message.messageId,
            authorUserId: message.authorUserId ?? null,
            attachments: message.attachments,
          },
        ],
        pendingSelfPrompts: [...state.pendingSelfPrompts, { ...message, content }],
      };
    }),
  ingestUserMessage: (message) =>
    set((state) => {
      const content = message.content.trim();
      if (!content) return state;

      const pendingIndex = state.pendingSelfPrompts.findIndex((pending) =>
        matchesPendingEcho(pending, message, content)
      );
      if (pendingIndex >= 0) {
        // This tab already rendered the prompt optimistically. Consume the pending
        // entry so a second echo of the same prompt is not deduped against it.
        return {
          pendingSelfPrompts: state.pendingSelfPrompts.filter((_, index) => index !== pendingIndex),
        };
      }

      if (message.messageId && state.flightLog.some((entry) => entry.messageId === message.messageId)) {
        return state;
      }

      // A reconnect rebuilds the transcript from the session snapshot, and the replay can
      // re-deliver a prompt that snapshot already preserved. Only persisted entries are
      // compared, so a genuine repeat of the same text in the live stream still renders.
      if (
        state.flightLog.some(
          (entry) => entry.role === "USER" && entry.isPersisted && entry.content.trim() === content
        )
      ) {
        return state;
      }

      return {
        flightLog: [
          ...state.flightLog,
          {
            id: message.messageId || `user-${Date.now()}-${state.flightLog.length}`,
            role: "USER" as const,
            content,
            timestamp: message.timestamp || formatEventTime(),
            messageId: message.messageId || undefined,
            authorUserId: message.authorUserId ?? null,
          },
        ],
      };
    }),
  setStreamContinuous: (isStreamContinuous) => set({ isStreamContinuous }),
  updateLastAgentLog: (delta, timestamp) =>
    set((state) => {
      const logs = [...state.flightLog];
      const last = logs[logs.length - 1];
      if (last && last.role === "AGENT" && !last.isPersisted) {
        logs[logs.length - 1] = {
          ...last,
          content: last.content + delta,
          isReasoningActive: false,
        };
        return { flightLog: logs, isReasoningActive: false };
      }
      return {
        flightLog: [
          ...logs,
          {
            id: `agent-${Date.now()}`,
            role: "AGENT",
            content: delta,
            timestamp: timestamp || formatEventTime(),
            isReasoningActive: false,
          },
        ],
        isReasoningActive: false,
      };
    }),
  appendReasoning: (delta, timestamp) =>
    set((state) => {
      const logs = [...state.flightLog];
      const last = logs[logs.length - 1];
      if (last && last.role === "AGENT" && !last.isPersisted && !last.content) {
        logs[logs.length - 1] = {
          ...last,
          reasoning: (last.reasoning || "") + delta,
          isReasoningActive: true,
        };
        return {
          flightLog: logs,
          reasoningStream: state.reasoningStream + delta,
          isReasoningActive: true,
        };
      }
      return {
        flightLog: [
          ...logs,
          {
            id: `agent-${Date.now()}`,
            role: "AGENT",
            content: "",
            reasoning: delta,
            timestamp: timestamp || formatEventTime(),
            isReasoningActive: true,
          },
        ],
        reasoningStream: state.reasoningStream + delta,
        isReasoningActive: true,
      };
    }),
  clearReasoning: () => set({ reasoningStream: "", isReasoningActive: false, suggestedFollowup: null }),
  discardPartialTokens: () =>
    set((state) => {
      const logs = [...state.flightLog];
      const last = logs[logs.length - 1];
      if (last && last.role === "AGENT" && !last.isPersisted) {
        logs.pop();
        return { flightLog: logs, reasoningStream: "", isReasoningActive: false };
      }
      return { reasoningStream: "", isReasoningActive: false };
    }),
  setIsReasoningActive: (active) => set({ activeDecision: null, isReasoningActive: active }),
  setActiveDecision: (decision) => set({ activeDecision: decision }),
  setActiveApproval: (approval) => set({ activeApproval: approval }),
  setChecklist: (tasks) => set({ checklist: tasks }),
  addFileDiff: (diff) =>
    set((state) => {
      // One entry per file: a step shows the edit that was reported for it.
      const existing = state.fileDiffs.findIndex((d) => d.path === diff.path);
      const fileDiffs = [...state.fileDiffs];
      if (existing >= 0) fileDiffs[existing] = diff;
      else fileDiffs.push(diff);
      return { fileDiffs, changesVersion: state.changesVersion + 1 };
    }),
  selectChange: (selectedChangePath) => set({ selectedChangePath }),
  bumpChanges: () => set((state) => ({ changesVersion: state.changesVersion + 1 })),
  appendTerminalLog: (text) =>
    set((state) => {
      const logs = state.terminalLogs + text;
      if (logs.length <= TERMINAL_MAX_CHARS) return { terminalLogs: logs, terminalActivity: true };
      // A long session prints for hours: keep the latest output, not all of it.
      const trimmed = logs.length - TERMINAL_KEEP_CHARS;
      return { terminalLogs: logs.slice(trimmed), terminalBase: state.terminalBase + trimmed, terminalActivity: true };
    }),
  appendToolOutput: (callId, text) =>
    set((state) => {
      if (!callId) return state;
      const next = ((state.liveToolOutput[callId] ?? "") + text).slice(-LIVE_OUTPUT_TAIL_CHARS);
      return { liveToolOutput: { ...state.liveToolOutput, [callId]: next } };
    }),
  clearToolOutput: (callId) =>
    set((state) => {
      if (!(callId in state.liveToolOutput)) return state;
      const rest = { ...state.liveToolOutput };
      delete rest[callId];
      return { liveToolOutput: rest };
    }),
  setTokenRate: (rate) => set({ tokenRate: rate }),
});

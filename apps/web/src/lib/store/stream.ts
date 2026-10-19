import type { StateCreator } from "zustand";
import { formatEventTime, matchesPendingEcho } from "./helpers";
import type { MissionStore, StreamSlice } from "./types";

export const createStreamSlice: StateCreator<MissionStore, [], [], StreamSlice> = (set) => ({
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
  activeDiffIndex: 0,
  terminalLogs: "\x1b[38;5;208m[SYSTEM]\x1b[0m Session workspace attached.\r\n",
  tokenRate: 0,
  turnBinding: null,
  artifacts: [],
  upsertArtifact: (artifact) =>
    set((state) => ({
      artifacts: [artifact, ...state.artifacts.filter((a) => a.id !== artifact.id)],
    })),
  setArtifacts: (artifacts) => set({ artifacts }),
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
  appendFlightLog: (entry) =>
    set((state) => ({
      flightLog: [...state.flightLog, entry],
    })),
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
      for (let i = logs.length - 1; i >= 0; i--) {
        if (logs[i].role === "AGENT") {
          if (logs[i].isPersisted) return state;
          logs[i] = {
            ...logs[i],
            content: logs[i].content + delta,
            isReasoningActive: false,
          };
          return { flightLog: logs, isReasoningActive: false };
        }
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
      for (let i = logs.length - 1; i >= 0; i--) {
        if (logs[i].role === "AGENT") {
          if (logs[i].isPersisted) return state;
          logs[i] = {
            ...logs[i],
            reasoning: (logs[i].reasoning || "") + delta,
            isReasoningActive: true,
          };
          return {
            flightLog: logs,
            reasoningStream: state.reasoningStream + delta,
            isReasoningActive: true,
          };
        }
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
      const existingIdx = state.fileDiffs.findIndex((d) => d.path === diff.path);
      if (existingIdx >= 0) {
        const updated = [...state.fileDiffs];
        updated[existingIdx] = diff;
        return { fileDiffs: updated, activeDiffIndex: existingIdx };
      }
      return {
        fileDiffs: [...state.fileDiffs, diff],
        activeDiffIndex: state.fileDiffs.length,
      };
    }),
  setActiveDiffIndex: (idx) => set({ activeDiffIndex: idx }),
  appendTerminalLog: (text) =>
    set((state) => ({
      terminalLogs: state.terminalLogs + text,
    })),
  setTokenRate: (rate) => set({ tokenRate: rate }),
});

import type { StateCreator } from "zustand";
import { postAttachment } from "../attachmentUpload";
import { newMessageId } from "../message-id";
import type {
  FlightLogEntry,
  PermissionMode,
  SessionData,
} from "../../types/mission";
import { convertHistoryToFlightLogs } from "./helpers";
import type { MissionStore, SessionsSlice } from "./types";

// Listing requests overlap (switching scope or the archived view while a CLEAR re-lists): only
// the latest one may write the list, or a slow earlier response shows the view just left.
let latestListing = 0;

interface FirstTurn {
  prompt: string;
  model: string;
  agentId: string;
  mode: string;
}

/** Upload the files chosen before the session existed, then start its first turn with them. */
async function startFirstTurnWithFiles(sessionId: string, files: File[], turn: FirstTurn): Promise<void> {
  try {
    const uploaded = await Promise.all(files.map((file) => postAttachment(sessionId, file)));
    const res = await fetch(`/v1/sessions/${sessionId}/turns`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      signal: AbortSignal.timeout(8000),
      body: JSON.stringify({
        prompt: turn.prompt,
        model: turn.model,
        agent_id: turn.agentId,
        mode: turn.mode,
        attachment_ids: uploaded.map((item) => item.attachment_id),
      }),
    });
    if (!res.ok) throw new Error((await res.text()) || `HTTP ${res.status}`);
  } catch (err) {
    // The empty session would otherwise stay in the list with nothing in it.
    void fetch(`/v1/sessions/${sessionId}`, { method: "DELETE" }).catch(() => {});
    throw err;
  }
}

/** The composer's state after a failed first send: the files are staged again, with the reason. */
function restoreStagedFiles(files: File[], err: unknown): Partial<MissionStore> {
  const reason = err instanceof Error ? err.message : String(err);
  return {
    attachmentError: `Could not send with attachments: ${reason}`,
    pendingAttachments: files.map((file, index) => ({
      attachmentId: `pending-${Date.now()}-${index}`,
      name: file.name,
      mime: file.type || "application/octet-stream",
      size: file.size,
      sha256: "",
      workspacePath: "",
      status: "staged" as const,
      file,
    })),
  };
}

/** What one session's context-window readout showed; another session starts without it. */
const NO_CONTEXT_USAGE = {
  contextTokens: null,
  contextLimit: null,
  contextModelLimit: null,
  contextTargetTokens: null,
  contextSource: null,
  contextTier2Tokens: null,
  contextTier3Tokens: null,
};

const SESSIONS_CACHE_KEY = "rocket_sessions_cache_v1";

function readCachedSessions(): SessionData[] {
  if (typeof window === "undefined") return [];
  try {
    const raw = localStorage.getItem(SESSIONS_CACHE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

function writeCachedSessions(sessions: SessionData[]): void {
  if (typeof window === "undefined") return;
  try {
    const lightweight = sessions.slice(0, 50).map((s) => ({
      ...s,
      conversation_history: undefined,
    }));
    localStorage.setItem(SESSIONS_CACHE_KEY, JSON.stringify(lightweight));
  } catch {}
}

export const createSessionsSlice: StateCreator<MissionStore, [], [], SessionsSlice> = (set, get) => ({
  session: null,
  sessions: readCachedSessions(),
  isLoadingSessions: false,
  sessionsNextCursor: null,
  sessionsIncludeArchived: false,
  sessionsScope: "mine",
  hasOlderHistory: false,
  isLoadingOlderHistory: false,
  historyStartIndex: 0,
  hydratedSessionId: null,
  setSession: (session) => {
    if (!session) {
      set({ session: null, hydratedSessionId: null, hasOlderHistory: false, historyStartIndex: 0 });
      return;
    }
    if (
      typeof window !== "undefined" &&
      session.session_id &&
      !session.is_draft &&
      session.session_id !== "draft" &&
      session.session_id !== "new"
    ) {
      try {
        localStorage.setItem("rocket_last_session_id", session.session_id);
      } catch {}
    }
    const agents = get().availableAgents;
    const meta = (session.metadata as Record<string, unknown>) || {};
    const mode = (session.inference_mode || meta.inference_mode || "agent") as "agent" | "direct";
    const agentId = session.agent_id || (meta.agent_id as string);
    const targetAgent = agents.find((a) => a.id === agentId) || agents[0];
    const targetModel =
      session.selected_model ||
      (meta.selected_model as string) ||
      (mode === "agent" && targetAgent ? targetAgent.model : get().selectedModel);

    // Rehydrate flight log from conversation history with reasoning and tool calls
    const history = session.conversation_history || [];
    let initialLogs: FlightLogEntry[];
    if (history.length > 0) {
      initialLogs = convertHistoryToFlightLogs(history, session.session_id);
    } else {
      initialLogs = [];
    }

    // A pending human gate must survive re-hydration of the *same* session
    // (reconnect sync / snapshot refetch); only switching sessions clears it.
    const sessionChanged = get().session?.session_id !== session.session_id;

    set({
      session,
      hydratedSessionId: session.session_id,
      permissionMode: (meta.permission_mode as PermissionMode | undefined) ?? "",
      inferenceMode: mode,
      activeAgent: targetAgent || get().activeAgent,
      selectedModel: mode === "agent" && targetAgent ? targetAgent.model : targetModel,
      flightLog: initialLogs,
      reasoningStream: "",
      isReasoningActive: false,
      isExecuting: false,
      hasOlderHistory: Boolean(session.has_older_history),
      historyStartIndex: session.total_messages && session.conversation_history
        ? session.total_messages - session.conversation_history.length
        : 0,
      turnBinding: sessionChanged ? null : get().turnBinding,
      activeDecision: sessionChanged ? null : get().activeDecision,
      activeApproval: sessionChanged ? null : get().activeApproval,
      // A reconnect re-hydrates the same session, so a discontinuity recorded for it
      // must survive; switching sessions starts from a fresh, complete snapshot.
      isStreamContinuous: sessionChanged ? true : get().isStreamContinuous,
      pendingSelfPrompts: sessionChanged ? [] : get().pendingSelfPrompts,
      pendingAttachments: sessionChanged ? [] : get().pendingAttachments,
      attachmentError: sessionChanged ? null : get().attachmentError,
      // Per-session ephemeral state: reset on switch
      fileDiffs: sessionChanged ? [] : get().fileDiffs,
      goal: sessionChanged ? null : get().goal,
      selectedChangePath: sessionChanged ? null : get().selectedChangePath,
      changesVersion: sessionChanged ? get().changesVersion + 1 : get().changesVersion,
      terminalLogs: sessionChanged
        ? "\x1b[38;5;208m[SYSTEM]\x1b[0m Session workspace attached.\r\n"
        : get().terminalLogs,
      terminalBase: sessionChanged ? 0 : get().terminalBase,
      terminalActivity: sessionChanged ? false : get().terminalActivity,
      terminalOpen: sessionChanged ? null : get().terminalOpen,
      inspectorAutoOpened: sessionChanged ? false : get().inspectorAutoOpened,
      runningProcesses: sessionChanged ? 0 : get().runningProcesses,
      terminalEpoch: sessionChanged ? get().terminalEpoch + 1 : get().terminalEpoch,
      liveToolOutput: sessionChanged ? {} : get().liveToolOutput,
      // The pre-fetch below lists this session's artifacts; until then none of another's show.
      ...(sessionChanged ? { artifacts: [], selectedArtifactId: null, ...NO_CONTEXT_USAGE } : {}),
      checklist: session.checklist ?? [],
      suggestedFollowup:
        session.suggested_followup ||
        ((meta.suggested_followup as string) ?? null),
      statusMessage: "System Ready. Standing by for instructions.",
    });

    // Pre-fetch the session's artifacts so the list is visible without opening the drawer.
    if (!session.is_draft && session.session_id !== "draft" && session.session_id !== "new") {
      fetch(`/v1/sessions/${session.session_id}/artifacts`)
        .then((res) => (res.ok ? res.json() : null))
        .then((data: unknown) => {
          if (Array.isArray(data) && session.session_id === get().session?.session_id) {
            set({ artifacts: data });
          }
        })
        .catch(() => {});
    }

    // If conversation_history was not pre-loaded on this session object, fetch it from backend
    if (!session.conversation_history) {
      fetch(`/v1/sessions/${session.session_id}?tail=50`)
        .then((res) => (res.ok ? res.json() : null))
        .then((fullData: SessionData | null) => {
          if (fullData && fullData.session_id === get().session?.session_id && fullData.conversation_history) {
            const rehydrated = convertHistoryToFlightLogs(fullData.conversation_history, fullData.session_id);
            const followup =
              fullData.suggested_followup ||
              ((fullData.metadata?.suggested_followup as string) ?? get().suggestedFollowup);
            const shouldSetLogs = !get().isExecuting && get().flightLog.length === 0;
            const startIdx = fullData.total_messages && fullData.conversation_history
              ? fullData.total_messages - fullData.conversation_history.length
              : 0;
            set({
              ...(shouldSetLogs && rehydrated.length > 0 ? { flightLog: rehydrated } : {}),
              suggestedFollowup: followup,
              hasOlderHistory: Boolean(fullData.has_older_history),
              historyStartIndex: startIdx,
              session: { ...get().session!, conversation_history: fullData.conversation_history },
            });
          }
        })
        .catch(() => {
          // Ignore network errors on background history fetch
        });
    }
  },
  loadOlderHistory: async () => {
    const { session, historyStartIndex, isLoadingOlderHistory, hasOlderHistory, flightLog } = get();
    if (!session || isLoadingOlderHistory || !hasOlderHistory) return;
    set({ isLoadingOlderHistory: true });
    try {
      const res = await fetch(
        `/v1/sessions/${session.session_id}/messages?before_index=${historyStartIndex}&limit=50`
      );
      if (!res.ok) return;
      const data: {
        messages: NonNullable<SessionData["conversation_history"]>;
        has_more: boolean;
        start_index: number;
      } = await res.json();
      if (!Array.isArray(data.messages) || data.messages.length === 0) {
        set({ hasOlderHistory: false });
        return;
      }
      const olderLogs = convertHistoryToFlightLogs(data.messages, session.session_id);
      set({
        flightLog: [...olderLogs, ...flightLog],
        hasOlderHistory: data.has_more,
        historyStartIndex: data.start_index,
      });
    } catch {
      // Ignore network errors
    } finally {
      set({ isLoadingOlderHistory: false });
    }
  },
  setSessions: (sessions) => set({ sessions }),
  fetchSessions: async (includeArchived = get().sessionsIncludeArchived, scope = get().sessionsScope) => {
    const ticket = ++latestListing;
    set({ isLoadingSessions: true, sessionsIncludeArchived: includeArchived, sessionsScope: scope });
    try {
      // The backend always lists the authenticated identity's organization; the client
      // only chooses between its own sessions and (administrators) the whole organization.
      const params = new URLSearchParams({ limit: "50", scope });
      if (includeArchived) params.set("archived", "true");
      const res = await fetch(`/v1/sessions?${params.toString()}`, {
        signal: AbortSignal.timeout(8000),
      });
      if (res.ok) {
        const data = await res.json();
        if (ticket !== latestListing) return;
        if (Array.isArray(data)) {
          set({
            sessions: data,
            sessionsNextCursor: res.headers.get("X-Next-Cursor"),
          });
          writeCachedSessions(data);
        }
      }
    } catch {
      // Offline fallback
    } finally {
      if (ticket === latestListing) set({ isLoadingSessions: false });
    }
  },
  refreshSessionStatuses: async () => {
    const { sessionsIncludeArchived, sessionsScope } = get();
    const ticket = latestListing;
    try {
      const params = new URLSearchParams({ limit: "50", scope: sessionsScope });
      if (sessionsIncludeArchived) params.set("archived", "true");
      const res = await fetch(`/v1/sessions?${params.toString()}`, { signal: AbortSignal.timeout(8000) });
      if (!res.ok) return;
      const fresh: unknown = await res.json();
      // An explicit listing started meanwhile (another scope, a reload) wins.
      if (ticket !== latestListing || !Array.isArray(fresh)) return;
      set((state) => {
        const listed = new Set((fresh as SessionData[]).map((s) => s.session_id));
        // The first page in the server's order, then the pages already loaded further down: a
        // refresh must not forget "load more".
        const loadedLater = state.sessions.filter((s) => !listed.has(s.session_id));
        const sessions = [...(fresh as SessionData[]), ...loadedLater];
        writeCachedSessions(sessions);
        return { sessions };
      });
    } catch {
      // The next tick tries again.
    }
  },
  loadMoreSessions: async () => {
    const { sessionsNextCursor, sessionsIncludeArchived, sessionsScope, isLoadingSessions } = get();
    if (!sessionsNextCursor || isLoadingSessions) return;
    const ticket = ++latestListing;
    set({ isLoadingSessions: true });
    try {
      const params = new URLSearchParams({
        limit: "50",
        cursor: sessionsNextCursor,
        scope: sessionsScope,
      });
      if (sessionsIncludeArchived) params.set("archived", "true");
      const res = await fetch(`/v1/sessions?${params.toString()}`, {
        signal: AbortSignal.timeout(8000),
      });
      if (res.ok) {
        const data = await res.json();
        if (ticket !== latestListing) return;
        if (Array.isArray(data)) {
          set((state) => {
            const known = new Set(state.sessions.map((s) => s.session_id));
            return {
              sessions: [...state.sessions, ...data.filter((s) => !known.has(s.session_id))],
              sessionsNextCursor: res.headers.get("X-Next-Cursor"),
            };
          });
        }
      }
    } catch {
      // Keep the pages already loaded; the button stays available for a retry.
    } finally {
      if (ticket === latestListing) set({ isLoadingSessions: false });
    }
  },
  initDraftSession: () => {
    const active = get().activeAgent;
    const draft: SessionData = {
      session_id: "draft",
      title: "New Conversation",
      tenant_org_id: "default_org",
      tenant_user_id: "default_user",
      sandbox_status: "non_existent",
      agent_id: active.id,
      is_draft: true,
    };
    set({
      session: draft,
      hydratedSessionId: null,
      artifacts: [],
      selectedArtifactId: null,
      ...NO_CONTEXT_USAGE,
      flightLog: [],
      terminalLogs: "[AVIONICS] Ready for prompt dispatch.\r\n",
      terminalBase: 0,
      terminalActivity: false,
      terminalOpen: null,
      inspectorAutoOpened: false,
      runningProcesses: 0,
      terminalEpoch: get().terminalEpoch + 1,
      liveToolOutput: {},
      fileDiffs: [],
      reasoningStream: "",
      isReasoningActive: false,
      isExecuting: false,
      activeDecision: null,
      activeApproval: null,
      checklist: [],
      suggestedFollowup: null,
      statusMessage: "System Ready. Standing by for instructions.",
    });
    return draft;
  },
  createSessionWithPrompt: async (prompt: string, model?: string, messageId?: string, files: File[] = []) => {
    const active = get().activeAgent;
    const targetAgentId = active.id;
    const cleanPrompt = prompt.trim();
    const promptMessageId = messageId || newMessageId();
    set({ isExecuting: true, statusMessage: "Initializing mission session and dispatching..." });
    try {
      const res = await fetch("/v1/sessions", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        signal: AbortSignal.timeout(8000),
        body: JSON.stringify({
          // Files can only be uploaded to a session that exists: with some, the session is
          // created empty and its first turn starts below, once they are uploaded.
          ...(files.length ? {} : { initial_prompt: cleanPrompt }),
          agent_id: targetAgentId,
          mode: get().inferenceMode,
          model: model || active.model || get().selectedModel,
          message_id: promptMessageId,
          ...(get().permissionMode ? { permission_mode: get().permissionMode } : {}),
          ...(get().selectedSandboxId ? { sandbox_id: get().selectedSandboxId } : {}),
        }),
      });
      if (res.ok) {
        const newSession: SessionData = await res.json();
        if (files.length) {
          await startFirstTurnWithFiles(newSession.session_id, files, {
            prompt: cleanPrompt,
            model: model || active.model || get().selectedModel,
            agentId: targetAgentId,
            mode: get().inferenceMode,
          });
        }
        const initialLogs: FlightLogEntry[] = [
          {
            id: promptMessageId,
            role: "USER",
            content: cleanPrompt,
            timestamp: new Date().toLocaleTimeString(),
            messageId: promptMessageId,
            authorUserId: null,
          },
        ];
        set((state) => ({
          sessions: [
            newSession,
            ...state.sessions.filter(
              (s) => s.session_id !== "draft" && s.session_id !== newSession.session_id
            ),
          ],
          session: newSession,
          // Created here, so already complete: the prompt shows at once, and the snapshot follows.
          hydratedSessionId: newSession.session_id,
          artifacts: [],
          selectedArtifactId: null,
          ...NO_CONTEXT_USAGE,
          flightLog: initialLogs,
          pendingSelfPrompts: [
            ...state.pendingSelfPrompts,
            { messageId: promptMessageId, content: cleanPrompt, authorUserId: null },
          ],
          terminalLogs: `\x1b[38;5;208m[AVIONICS]\x1b[0m Session initialized for ${newSession.session_id}.\r\n`,
          terminalBase: 0,
          terminalActivity: false,
          terminalOpen: null,
          inspectorAutoOpened: false,
          runningProcesses: 0,
          terminalEpoch: get().terminalEpoch + 1,
          liveToolOutput: {},
          fileDiffs: [],
          isExecuting: true,
          statusMessage: "Executing mission turn...",
        }));
        if (typeof window !== "undefined" && newSession.session_id) {
          try {
            localStorage.setItem("rocket_last_session_id", newSession.session_id);
          } catch {}
        }
        return newSession;
      }
      throw new Error("Backend session creation failed");
    } catch (err) {
      console.error("Failed to create session with prompt:", err);
      set({
        isExecuting: false,
        statusMessage: "Failed to initialize session",
        ...(files.length ? restoreStagedFiles(files, err) : {}),
      });
      return null;
    }
  },
  createSession: async (title?: string, agentId?: string) => {
    const active = get().activeAgent;
    const targetAgentId = agentId || active.id;
    try {
      const res = await fetch("/v1/sessions", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        signal: AbortSignal.timeout(8000),
        body: JSON.stringify({
          title: title || `Mission ${new Date().toLocaleDateString()}`,
          agent_id: targetAgentId,
        }),
      });
      if (res.ok) {
        const newSession: SessionData = await res.json();
        set((state) => ({
          sessions: [newSession, ...state.sessions],
          session: newSession,
          hydratedSessionId: newSession.session_id,
          artifacts: [],
          selectedArtifactId: null,
          ...NO_CONTEXT_USAGE,
          flightLog: [],
          terminalLogs: `\x1b[38;5;208m[AVIONICS]\x1b[0m Workspace initialized for ${newSession.session_id}.\r\n`,
          terminalBase: 0,
          terminalActivity: false,
          terminalOpen: null,
          inspectorAutoOpened: false,
          runningProcesses: 0,
          terminalEpoch: get().terminalEpoch + 1,
          liveToolOutput: {},
          fileDiffs: [],
        }));
        return newSession;
      }
      throw new Error("Backend session creation failed");
    } catch {
      // Local fallback session
      const fallback: SessionData = {
        session_id: `session_${Date.now().toString(36)}`,
        title: title || "Offline Mission Session",
        tenant_org_id: "default_org",
        tenant_user_id: "default_user",
        sandbox_status: "running",
        agent_id: targetAgentId,
      };
      set((state) => ({
        sessions: [fallback, ...state.sessions],
        session: fallback,
        flightLog: [],
        terminalLogs: `\x1b[38;5;208m[AVIONICS]\x1b[0m Workspace initialized for ${fallback.session_id}.\r\n`,
        terminalBase: 0,
        terminalActivity: false,
        terminalOpen: null,
        inspectorAutoOpened: false,
        runningProcesses: 0,
        terminalEpoch: get().terminalEpoch + 1,
        liveToolOutput: {},
        fileDiffs: [],
      }));
      return fallback;
    }
  },
  deleteSession: async (sessionId: string) => {
    try {
      const res = await fetch(`/v1/sessions/${sessionId}`, {
        method: "DELETE",
        signal: AbortSignal.timeout(8000),
      });
      if (res.ok) {
        const current = get().session;
        const remaining = get().sessions.filter((s) => s.session_id !== sessionId);
        set({
          sessions: remaining,
          session: current?.session_id === sessionId ? (remaining[0] || null) : current,
        });
        return true;
      }
    } catch {
      // Optimistic local deletion
      const current = get().session;
      const remaining = get().sessions.filter((s) => s.session_id !== sessionId);
      set({
        sessions: remaining,
        session: current?.session_id === sessionId ? (remaining[0] || null) : current,
      });
      return true;
    }
    return false;
  },
  resetAllSessions: async () => {
    try {
      const res = await fetch("/v1/sessions/reset", {
        method: "POST",
        signal: AbortSignal.timeout(30000),
      });
      if (!res.ok) return null;
      const data = await res.json();
      const newSession: SessionData = data.new_session;
      set({
        session: newSession,
        hydratedSessionId: newSession.session_id,
        flightLog: [],
        checklist: [],
        fileDiffs: [],
        artifacts: [],
        selectedArtifactId: null,
        ...NO_CONTEXT_USAGE,
      });
      // Only the caller's sessions were deleted; re-list so sessions shared with them (and,
      // in the organization view, other members' sessions) stay in the sidebar.
      await get().fetchSessions();
      return newSession;
    } catch {
      return null;
    }
  },
  updateSessionTitle: (sessionId, newTitle) => {
    set((state) => {
      const updatedSessions = state.sessions.map((s) =>
        s.session_id === sessionId ? { ...s, title: newTitle } : s
      );
      const updatedSession =
        state.session?.session_id === sessionId
          ? { ...state.session, title: newTitle }
          : state.session;
      return { sessions: updatedSessions, session: updatedSession };
    });
  },
  applySessionUpdate: (sessionId, update) => {
    set((state) => {
      const patch = <T extends SessionData>(session: T): T => ({
        ...session,
        ...(update.title !== undefined ? { title: update.title } : {}),
        ...(update.is_pinned !== undefined ? { is_pinned: update.is_pinned } : {}),
        ...(update.is_archived !== undefined ? { is_archived: update.is_archived } : {}),
        ...(update.sandbox_status !== undefined ? { sandbox_status: update.sandbox_status } : {}),
        ...(update.is_processing !== undefined ? { is_processing: update.is_processing } : {}),
        ...(update.awaiting_input !== undefined || update.last_turn_status !== undefined
          ? {
              metadata: {
                ...(session.metadata ?? {}),
                ...(update.awaiting_input !== undefined ? { awaiting_input: update.awaiting_input || undefined } : {}),
                ...(update.last_turn_status !== undefined ? { last_turn_status: update.last_turn_status } : {}),
              },
            }
          : {}),
      });
      return {
        sessions: state.sessions.map((s) => (s.session_id === sessionId ? patch(s) : s)),
        session: state.session?.session_id === sessionId ? patch(state.session) : state.session,
      };
    });
  },
  setSessionFlags: async (sessionId, flags) => {
    const applyLocally = () => get().applySessionUpdate(sessionId, flags);
    try {
      const res = await fetch(`/v1/sessions/${sessionId}/flags`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(flags),
        signal: AbortSignal.timeout(8000),
      });
      if (!res.ok) return false;
      applyLocally();
      return true;
    } catch {
      // A failed pin/archive must be visible: the caller re-fetches the list instead of
      // rendering a state the server never accepted.
      return false;
    }
  },
});

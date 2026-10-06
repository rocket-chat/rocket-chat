import { create } from "zustand";
import {
  AgentPersonaConfig,
  ChecklistTask,
  DecisionQuestion,
  FileDiffItem,
  FlightLogEntry,
  ModelOption,
  SessionData,
  SubagentExecutionData,
  ToolCallData,
} from "../types/mission";

export const DEFAULT_AGENTS: AgentPersonaConfig[] = [
  {
    id: "persona-general",
    name: "General Software Engineer",
    role_title: "Senior Autonomous Engineer",
    description:
      "Autonomous full-stack engineering agent with full access to terminal execution, file inspection and editing, web lookup, git operations, and interactive decision gates.",
    system_prompt:
      "You are a Senior Autonomous Software Engineer. You write clean, modular, production-grade code adhering to strict types and software craftsmanship. Plan methodically, inspect files, apply surgical diffs, and verify your work with automated tests.",
    model: "openrouter/deepseek/deepseek-v4.1-flash",
    whitelisted_tools: [
      "bash_exec",
      "file_read",
      "file_write",
      "file_edit",
      "apply_patch",
      "web_fetch",
      "web_search",
      "ask_question",
      "session_rename",
      "update_task_checklist",
    ],
    icon: "bot",
  },
  {
    id: "persona-security",
    name: "Security & Penetration Auditor",
    role_title: "Application Security Specialist",
    description:
      "Audits source code for injection attacks, RLS boundary leaks, secret exposure, and permissions.",
    system_prompt:
      "You are a dedicated Security Auditor persona. Inspect source files and configuration for vulnerabilities (OWASP Top 10, SQL injection, RLS bypasses, secret leakage).",
    model: "openrouter/anthropic/claude-3.5-sonnet",
    whitelisted_tools: ["file_read", "web_search", "web_fetch"],
    icon: "shield",
  },
];

interface MissionStore {
  session: SessionData | null;
  sessions: SessionData[];
  isLoadingSessions: boolean;
  isLeftSidebarOpen: boolean;
  activeAgent: AgentPersonaConfig;
  availableAgents: AgentPersonaConfig[];

  connected: boolean;
  statusMessage: string;
  isExecuting: boolean;
  flightLog: FlightLogEntry[];
  reasoningStream: string;
  isReasoningActive: boolean;
  activeDecision: DecisionQuestion | null;
  checklist: ChecklistTask[];
  fileDiffs: FileDiffItem[];
  activeDiffIndex: number;
  terminalLogs: string;
  tokenRate: number;
  selectedModel: string;
  availableModels: ModelOption[];
  isSettingsOpen: boolean;
  isRightPanelOpen: boolean;
  inferenceMode: "agent" | "direct";
  suggestedFollowup: string | null;

  setSession: (session: SessionData | null) => void;
  setSessions: (sessions: SessionData[]) => void;
  setIsLeftSidebarOpen: (open: boolean) => void;
  setActiveAgent: (agent: AgentPersonaConfig) => void;
  setAvailableAgents: (agents: AgentPersonaConfig[]) => void;
  setInferenceMode: (mode: "agent" | "direct") => void;
  selectAgent: (agent: AgentPersonaConfig) => void;
  selectDirectModel: (modelId: string) => void;
  fetchSessions: () => Promise<void>;
  createSession: (title?: string, agentId?: string) => Promise<SessionData | null>;
  deleteSession: (sessionId: string) => Promise<boolean>;
  resetAllSessions: () => Promise<SessionData | null>;
  fetchAgents: () => Promise<void>;
  fetchModels: () => Promise<void>;
  updateSessionTitle: (sessionId: string, newTitle: string) => void;

  setConnected: (connected: boolean) => void;
  setStatusMessage: (msg: string) => void;
  setIsExecuting: (exec: boolean) => void;
  appendFlightLog: (entry: FlightLogEntry) => void;
  updateLastAgentLog: (delta: string) => void;
  appendReasoning: (delta: string) => void;
  clearReasoning: () => void;
  setIsReasoningActive: (active: boolean) => void;
  setActiveDecision: (decision: DecisionQuestion | null) => void;
  setChecklist: (tasks: ChecklistTask[]) => void;
  addFileDiff: (diff: FileDiffItem) => void;
  setActiveDiffIndex: (idx: number) => void;
  appendTerminalLog: (text: string) => void;
  setTokenRate: (rate: number) => void;
  setSelectedModel: (model: string) => void;
  setAvailableModels: (models: ModelOption[]) => void;
  setIsSettingsOpen: (open: boolean) => void;
  setIsRightPanelOpen: (open: boolean) => void;
  setSuggestedFollowup: (followup: string | null) => void;
  updateToolCall: (nameOrId: string, data: Partial<ToolCallData>) => void;
  updateSubagent: (subagentId: string, data: Partial<SubagentExecutionData>) => void;
}

function convertHistoryToFlightLogs(
  history: NonNullable<SessionData["conversation_history"]>,
  sessionId: string
): FlightLogEntry[] {
  const logs: FlightLogEntry[] = [];
  history.forEach((m, idx) => {
    const roleUpper = m.role.toUpperCase();
    if (roleUpper === "USER") {
      logs.push({
        id: `msg-${sessionId}-${idx}-user`,
        role: "USER",
        content: m.content,
        timestamp: new Date().toLocaleTimeString(),
      });
    } else if (roleUpper === "SYSTEM") {
      logs.push({
        id: `msg-${sessionId}-${idx}-sys`,
        role: "SYSTEM",
        content: m.content,
        timestamp: new Date().toLocaleTimeString(),
      });
    } else {
      if (m.tool_calls && m.tool_calls.length > 0) {
        m.tool_calls.forEach((tc, tIdx) => {
          logs.push({
            id: `msg-${sessionId}-${idx}-tool-${tIdx}`,
            role: "AGENT",
            content: `Executing tool: ${tc.name}`,
            toolCall: tc,
            timestamp: new Date().toLocaleTimeString(),
          });
        });
      }
      if (m.reasoning) {
        logs.push({
          id: `msg-${sessionId}-${idx}-thought`,
          role: "AGENT",
          content: "",
          reasoning: m.reasoning,
          timestamp: new Date().toLocaleTimeString(),
        });
      }
      if (m.content) {
        logs.push({
          id: `msg-${sessionId}-${idx}-ans`,
          role: "AGENT",
          content: m.content,
          timestamp: new Date().toLocaleTimeString(),
        });
      }
    }
  });
  return logs;
}

export const useMissionStore = create<MissionStore>((set, get) => ({
  session: null,
  sessions: [],
  isLoadingSessions: false,
  isLeftSidebarOpen: true,
  activeAgent: DEFAULT_AGENTS[0],
  availableAgents: DEFAULT_AGENTS,
  inferenceMode: "agent",

  connected: false,
  statusMessage: "System Ready. Standing by for instructions.",
  isExecuting: false,
  flightLog: [
    {
      id: "initial-log",
      role: "SYSTEM",
      content: "Rocket Chat Mission Control initialized. Connected to Docker Sandbox runtime.",
      timestamp: "00:00:00",
    },
  ],
  reasoningStream: "",
  isReasoningActive: false,
  activeDecision: null,
  checklist: [],
  fileDiffs: [],
  activeDiffIndex: 0,
  terminalLogs: "\x1b[38;5;208m[SYSTEM]\x1b[0m Session workspace attached.\r\n",
  tokenRate: 0,
  selectedModel: "openrouter/deepseek/deepseek-v4.1-flash",
  availableModels: [
    { id: "openrouter/deepseek/deepseek-v4.1-flash", name: "DeepSeek V4.1 Flash", context_window: 131072, provider: "DeepSeek" },
    { id: "anthropic/claude-3.5-sonnet", name: "Claude 3.5 Sonnet", context_window: 200000, provider: "Anthropic" },
    { id: "anthropic/claude-3.7-sonnet", name: "Claude 3.7 Sonnet", context_window: 200000, provider: "Anthropic" },
    { id: "openai/gpt-4o", name: "GPT-4o Omnimodel", context_window: 128000, provider: "OpenAI" },
    { id: "openai/gpt-4o-mini", name: "GPT-4o Mini", context_window: 128000, provider: "OpenAI" },
  ],
  isSettingsOpen: false,
  isRightPanelOpen: false,
  suggestedFollowup: null,

  setSession: (session) => {
    if (!session) {
      set({ session: null });
      return;
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
      initialLogs = [
        {
          id: `initial-log-${session.session_id}`,
          role: "SYSTEM",
          content: `Mission Control initialized for session ${session.title || session.session_id.slice(0, 12)}. Standing by for instructions.`,
          timestamp: new Date().toLocaleTimeString(),
        },
      ];
    }

    set({
      session,
      inferenceMode: mode,
      activeAgent: targetAgent || get().activeAgent,
      selectedModel: mode === "agent" && targetAgent ? targetAgent.model : targetModel,
      flightLog: initialLogs,
      reasoningStream: "",
      isReasoningActive: false,
      isExecuting: false,
      activeDecision: null,
      checklist: session.checklist ?? [],
      suggestedFollowup:
        session.suggested_followup ||
        ((meta.suggested_followup as string) ?? null),
      statusMessage: "System Ready. Standing by for instructions.",
    });

    // If conversation_history was not pre-loaded on this session object, fetch it from backend
    if (!session.conversation_history) {
      fetch(`/v1/sessions/${session.session_id}`)
        .then((res) => (res.ok ? res.json() : null))
        .then((fullData: SessionData | null) => {
          if (fullData && fullData.session_id === get().session?.session_id && fullData.conversation_history) {
            const rehydrated = convertHistoryToFlightLogs(fullData.conversation_history, fullData.session_id);
            const followup =
              fullData.suggested_followup ||
              ((fullData.metadata?.suggested_followup as string) ?? get().suggestedFollowup);
            if (rehydrated.length > 0) {
              set({
                flightLog: rehydrated,
                suggestedFollowup: followup,
                session: { ...get().session!, conversation_history: fullData.conversation_history },
              });
            }
          }
        })
        .catch(() => {
          // Ignore network errors on background history fetch
        });
    }
  },
  setSessions: (sessions) => set({ sessions }),
  setIsLeftSidebarOpen: (isLeftSidebarOpen) => set({ isLeftSidebarOpen }),
  setActiveAgent: (activeAgent) => {
    set({
      activeAgent,
      inferenceMode: "agent",
      selectedModel: activeAgent.model || get().selectedModel,
    });
  },
  setAvailableAgents: (availableAgents) => set({ availableAgents }),
  setInferenceMode: (inferenceMode) => set({ inferenceMode }),

  selectAgent: (agent: AgentPersonaConfig) => {
    const currentSession = get().session;
    const updatedModel = agent.model || get().selectedModel;
    set({
      activeAgent: agent,
      inferenceMode: "agent",
      selectedModel: updatedModel,
    });
    if (currentSession) {
      const updatedSession: SessionData = {
        ...currentSession,
        agent_id: agent.id,
        inference_mode: "agent",
        selected_model: updatedModel,
        metadata: {
          ...(currentSession.metadata || {}),
          agent_id: agent.id,
          inference_mode: "agent",
          selected_model: updatedModel,
        },
      };
      set({ session: updatedSession });
      fetch(`/v1/sessions/${currentSession.session_id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ agent_id: agent.id, metadata: updatedSession.metadata }),
      }).catch(() => {});
    }
  },

  selectDirectModel: (modelId: string) => {
    const currentSession = get().session;
    set({
      inferenceMode: "direct",
      selectedModel: modelId,
    });
    if (currentSession) {
      const updatedSession: SessionData = {
        ...currentSession,
        inference_mode: "direct",
        selected_model: modelId,
        metadata: {
          ...(currentSession.metadata || {}),
          inference_mode: "direct",
          selected_model: modelId,
        },
      };
      set({ session: updatedSession });
      fetch(`/v1/sessions/${currentSession.session_id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ metadata: updatedSession.metadata }),
      }).catch(() => {});
    }
  },

  fetchSessions: async () => {
    set({ isLoadingSessions: true });
    try {
      const res = await fetch("/v1/sessions?tenant_org_id=default_org&limit=50", {
        signal: AbortSignal.timeout(8000),
      });
      if (res.ok) {
        const data = await res.json();
        if (Array.isArray(data)) {
          set({ sessions: data });
          if (!get().session && data.length > 0) {
            set({ session: data[0] });
          }
        }
      }
    } catch {
      // Offline fallback
    } finally {
      set({ isLoadingSessions: false });
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
          flightLog: [
            {
              id: "initial-log",
              role: "SYSTEM",
              content: `Mission Control initialized for session "${newSession.title}". Assigned Agent: ${active.name}.`,
              timestamp: new Date().toLocaleTimeString(),
            },
          ],
          terminalLogs: `\x1b[38;5;208m[AVIONICS]\x1b[0m Workspace initialized for ${newSession.session_id}.\r\n`,
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
        flightLog: [
          {
            id: "initial-log",
            role: "SYSTEM",
            content: `Mission Control initialized for session "${fallback.title}". Assigned Agent: ${active.name}.`,
            timestamp: new Date().toLocaleTimeString(),
          },
        ],
        terminalLogs: `\x1b[38;5;208m[AVIONICS]\x1b[0m Workspace initialized for ${fallback.session_id}.\r\n`,
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
        signal: AbortSignal.timeout(10000),
      });
      if (res.ok) {
        const data = await res.json();
        const newSession: SessionData = data.new_session;
        set({
          sessions: [newSession],
          session: newSession,
          flightLog: [
            {
              id: `initial-log-${newSession.session_id}`,
              role: "SYSTEM",
              content: `Mission Control initialized for session "${newSession.title}". Standing by for instructions.`,
              timestamp: new Date().toLocaleTimeString(),
            },
          ],
          checklist: [],
          fileDiffs: [],
        });
        return newSession;
      }
    } catch {
      // Local fallback reset
      const fallback: SessionData = {
        session_id: `session_${Date.now().toString(36)}`,
        title: "New Mission",
        tenant_org_id: "default_org",
        tenant_user_id: "default_user",
        sandbox_status: "non_existent",
      };
      set({
        sessions: [fallback],
        session: fallback,
        flightLog: [
          {
            id: `initial-log-${fallback.session_id}`,
            role: "SYSTEM",
            content: `Mission Control initialized for session "${fallback.title}". Standing by for instructions.`,
            timestamp: new Date().toLocaleTimeString(),
          },
        ],
        checklist: [],
        fileDiffs: [],
      });
      return fallback;
    }
    return null;
  },

  fetchAgents: async () => {
    try {
      const res = await fetch("/v1/agents", {
        signal: AbortSignal.timeout(5000),
      });
      if (res.ok) {
        const data = await res.json();
        if (Array.isArray(data) && data.length > 0) {
          set({ availableAgents: data });
          const current = get().activeAgent;
          const matching = data.find((a: AgentPersonaConfig) => a.id === current.id);
          if (matching) {
            set({ activeAgent: matching });
          }
        }
      }
    } catch {
      // Maintain defaults
    }
  },

  fetchModels: async () => {
    try {
      const res = await fetch("/v1/models", { signal: AbortSignal.timeout(5000) });
      if (res.ok) {
        const data = await res.json();
        if (data.models && Array.isArray(data.models) && data.models.length > 0) {
          set({ availableModels: data.models });
          if (data.default_model && !get().selectedModel) {
            set({ selectedModel: data.default_model });
          }
        }
      }
    } catch {
      // Maintain default models
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

  setConnected: (connected) => set({ connected }),
  setStatusMessage: (msg) => set({ statusMessage: msg }),
  setIsExecuting: (exec) => set({ isExecuting: exec }),
  setSelectedModel: (selectedModel) => set({ selectedModel }),
  setAvailableModels: (availableModels) => set({ availableModels }),
  setIsSettingsOpen: (isSettingsOpen) => set({ isSettingsOpen }),
  setIsRightPanelOpen: (isRightPanelOpen) => set({ isRightPanelOpen }),

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

  updateLastAgentLog: (delta) =>
    set((state) => {
      const logs = [...state.flightLog];
      const last = logs[logs.length - 1];
      if (last && last.role === "AGENT") {
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
            timestamp: new Date().toLocaleTimeString(),
            isReasoningActive: false,
          },
        ],
        isReasoningActive: false,
      };
    }),

  appendReasoning: (delta) =>
    set((state) => {
      const logs = [...state.flightLog];
      const last = logs[logs.length - 1];
      if (last && last.role === "AGENT") {
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
            timestamp: new Date().toLocaleTimeString(),
            isReasoningActive: true,
          },
        ],
        reasoningStream: state.reasoningStream + delta,
        isReasoningActive: true,
      };
    }),

  clearReasoning: () => set({ reasoningStream: "", isReasoningActive: false, suggestedFollowup: null }),
  setSuggestedFollowup: (suggestedFollowup) => set({ suggestedFollowup }),
  setIsReasoningActive: (active) => set({ isReasoningActive: active }),
  setActiveDecision: (decision) => set({ activeDecision: decision }),

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
}));

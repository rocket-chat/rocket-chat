import type { StateCreator } from "zustand";
import type {
  AgentPersonaConfig,
  PermissionMode,
  SessionData,
} from "../../types/mission";
import { DEFAULT_AGENTS } from "./helpers";
import type { MissionStore, AgentsSlice } from "./types";

export const createAgentsSlice: StateCreator<MissionStore, [], [], AgentsSlice> = (set, get) => ({
  activeAgent: DEFAULT_AGENTS[0],
  availableAgents: DEFAULT_AGENTS,
  inferenceMode: "agent",
  selectedModel: "openrouter/deepseek/deepseek-v4.1-flash",
  permissionMode: "",
  selectedSandboxId: "",
  availableModels: [
    { id: "openrouter/deepseek/deepseek-v4.1-flash", name: "DeepSeek V4.1 Flash", context_window: 131072, provider: "DeepSeek" },
    { id: "anthropic/claude-3.5-sonnet", name: "Claude 3.5 Sonnet", context_window: 200000, provider: "Anthropic" },
    { id: "anthropic/claude-3.7-sonnet", name: "Claude 3.7 Sonnet", context_window: 200000, provider: "Anthropic" },
    { id: "openai/gpt-4o", name: "GPT-4o Omnimodel", context_window: 128000, provider: "OpenAI" },
    { id: "openai/gpt-4o-mini", name: "GPT-4o Mini", context_window: 128000, provider: "OpenAI" },
  ],
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
      if (!currentSession.is_draft && currentSession.session_id !== "draft") {
        fetch(`/v1/sessions/${currentSession.session_id}`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ agent_id: agent.id, metadata: updatedSession.metadata }),
        }).catch(() => {});
      }
    }
  },
  setSelectedSandboxId: (id: string) => set({ selectedSandboxId: id }),
  setPermissionMode: (mode: PermissionMode) => {
    const currentSession = get().session;
    set({ permissionMode: mode });
    if (!currentSession) return;
    const metadata = { ...(currentSession.metadata || {}), permission_mode: mode };
    set({ session: { ...currentSession, metadata } });
    if (!currentSession.is_draft && currentSession.session_id !== "draft") {
      fetch(`/v1/sessions/${currentSession.session_id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ metadata: { permission_mode: mode } }),
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
      if (!currentSession.is_draft && currentSession.session_id !== "draft") {
        fetch(`/v1/sessions/${currentSession.session_id}`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ metadata: updatedSession.metadata }),
        }).catch(() => {});
      }
    }
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
  setSelectedModel: (selectedModel) => set({ selectedModel }),
  setAvailableModels: (availableModels) => set({ availableModels }),
});

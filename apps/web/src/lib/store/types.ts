import type {
  TurnBinding,
  AgentPersonaConfig,
  ApprovalRequestInfo,
  ArtifactItem,
  AttachmentInfo,
  ChecklistTask,
  DecisionQuestion,
  FileDiffItem,
  FlightLogEntry,
  ModelOption,
  PermissionMode,
  SessionData,
  SubagentExecutionData,
  ToolCallData,
  WaitStartedPayload,
} from "../../types/mission";

export interface OwnUserMessage {
  messageId: string;
  content: string;
  authorUserId?: string | null;
  attachments?: AttachmentInfo[];
}

export interface IncomingUserMessage {
  messageId?: string | null;
  content: string;
  authorUserId?: string | null;
  timestamp?: string;
}

export type SessionsScope = "mine" | "organization";

export interface SessionsSlice {
  session: SessionData | null;
  sessions: SessionData[];
  isLoadingSessions: boolean;
  /** Keyset cursor for the next sidebar page; null when fully loaded. */
  sessionsNextCursor: string | null;
  sessionsIncludeArchived: boolean;
  /** "mine": own and shared sessions; "organization": every session (administrators). */
  sessionsScope: SessionsScope;
  setSession: (session: SessionData | null) => void;
  setSessions: (sessions: SessionData[]) => void;
  /** Re-lists page one; arguments left out keep the current archived view and scope. */
  fetchSessions: (includeArchived?: boolean, scope?: SessionsScope) => Promise<void>;
  loadMoreSessions: () => Promise<void>;
  initDraftSession: () => SessionData;
  createSessionWithPrompt: (
    prompt: string,
    model?: string,
    messageId?: string
  ) => Promise<SessionData | null>;
  createSession: (title?: string, agentId?: string) => Promise<SessionData | null>;
  deleteSession: (sessionId: string) => Promise<boolean>;
  /** Deletes every session the caller owns; null when the API refused. */
  resetAllSessions: () => Promise<SessionData | null>;
  updateSessionTitle: (sessionId: string, newTitle: string) => void;
  /** Pins or archives one session; returns false when the API rejects it. */
  setSessionFlags: (
    sessionId: string,
    flags: { is_pinned?: boolean; is_archived?: boolean }
  ) => Promise<boolean>;
  /** Applies a `session_updated` broadcast to the local list so other tabs converge. */
  applySessionUpdate: (
    sessionId: string,
    update: { title?: string; is_pinned?: boolean; is_archived?: boolean; sandbox_status?: string; is_processing?: boolean }
  ) => void;
}

export interface AgentsSlice {
  activeAgent: AgentPersonaConfig;
  availableAgents: AgentPersonaConfig[];
  selectedModel: string;
  availableModels: ModelOption[];
  inferenceMode: "agent" | "direct";
  /** Supervision level chosen for this session; "" follows the member/organization default. */
  permissionMode: PermissionMode | "";
  /** Sandbox the next new session starts in ("" = the organization default). */
  selectedSandboxId: string;
  setActiveAgent: (agent: AgentPersonaConfig) => void;
  setAvailableAgents: (agents: AgentPersonaConfig[]) => void;
  setInferenceMode: (mode: "agent" | "direct") => void;
  selectAgent: (agent: AgentPersonaConfig) => void;
  selectDirectModel: (modelId: string) => void;
  setPermissionMode: (mode: PermissionMode) => void;
  setSelectedSandboxId: (id: string) => void;
  fetchAgents: () => Promise<void>;
  /** The full catalogue arrived (the placeholder list is shown until then). */
  modelsLoaded: boolean;
  fetchModels: () => Promise<void>;
  setSelectedModel: (model: string) => void;
  setAvailableModels: (models: ModelOption[]) => void;
}

export interface StreamSlice {
  connected: boolean;
  statusMessage: string;
  isExecuting: boolean;
  flightLog: FlightLogEntry[];
  reasoningStream: string;
  isReasoningActive: boolean;
  activeDecision: DecisionQuestion | null;
  activeApproval: ApprovalRequestInfo | null;
  checklist: ChecklistTask[];
  fileDiffs: FileDiffItem[];
  activeDiffIndex: number;
  terminalLogs: string;
  tokenRate: number;
  /** The agent and model of the turn running now (from its "running" status). */
  turnBinding: TurnBinding | null;
  setTurnBinding: (binding: TurnBinding | null) => void;
  setConnected: (connected: boolean) => void;
  setStatusMessage: (msg: string) => void;
  setIsExecuting: (exec: boolean) => void;
  appendFlightLog: (entry: FlightLogEntry) => void;
  /**
   * Appends a prompt this tab just submitted and remembers it as awaiting its
   * server echo, so the broadcast `user_message` event is not rendered twice.
   */
  appendOwnUserMessage: (message: OwnUserMessage) => void;
  /** Renders a `user_message` broadcast, deduped against our own optimistic entry. */
  ingestUserMessage: (message: IncomingUserMessage) => void;
  pendingSelfPrompts: OwnUserMessage[];
  isStreamContinuous: boolean;
  setStreamContinuous: (continuous: boolean) => void;
  updateLastAgentLog: (delta: string, timestamp?: string) => void;
  appendReasoning: (delta: string, timestamp?: string) => void;
  clearReasoning: () => void;
  discardPartialTokens: () => void;
  setIsReasoningActive: (active: boolean) => void;
  setActiveDecision: (decision: DecisionQuestion | null) => void;
  setActiveApproval: (approval: ApprovalRequestInfo | null) => void;
  activeWait: WaitStartedPayload | null;
  setActiveWait: (wait: WaitStartedPayload | null) => void;
  setChecklist: (tasks: ChecklistTask[]) => void;
  addFileDiff: (diff: FileDiffItem) => void;
  setActiveDiffIndex: (idx: number) => void;
  appendTerminalLog: (text: string) => void;
  setTokenRate: (rate: number) => void;
  updateToolCall: (nameOrId: string, data: Partial<ToolCallData>) => void;
  updateSubagent: (subagentId: string, data: Partial<SubagentExecutionData>) => void;
  artifacts: ArtifactItem[];
  upsertArtifact: (artifact: ArtifactItem) => void;
  setArtifacts: (artifacts: ArtifactItem[]) => void;
}

export interface ComposerSlice {
  suggestedFollowup: string | null;
  setSuggestedFollowup: (followup: string | null) => void;
  /** Text the empty state or a template drops into the composer; the nonce re-fires an identical seed. */
  composerSeed: { text: string; nonce: number } | null;
  seedComposer: (text: string) => void;
  /** A seed is applied once; leaving it in the store would refill every composer that mounts later. */
  clearComposerSeed: () => void;
  queuedPrompt: string | null;
  setQueuedPrompt: (prompt: string | null) => void;
  popQueuedPrompt: () => string | null;
  /** Files staged in the composer for the *next* turn. */
  pendingAttachments: AttachmentInfo[];
  attachmentError: string | null;
  uploadAttachment: (file: File) => Promise<void>;
  removeAttachment: (attachmentId: string) => void;
  clearAttachments: () => void;
}

export interface LayoutSlice {
  isLeftSidebarOpen: boolean;
  isRightPanelOpen: boolean;
  isTurnInspectorOpen: boolean;
  isMemoryInspectorOpen: boolean;
  isArtifactsDrawerOpen: boolean;
  setIsLeftSidebarOpen: (open: boolean) => void;
  isSettingsNavOpen: boolean;
  setIsSettingsNavOpen: (open: boolean) => void;
  setIsRightPanelOpen: (open: boolean) => void;
  setIsTurnInspectorOpen: (open: boolean) => void;
  setIsMemoryInspectorOpen: (open: boolean) => void;
  setIsArtifactsDrawerOpen: (open: boolean) => void;
}

export type MissionStore = SessionsSlice &
  AgentsSlice &
  StreamSlice &
  ComposerSlice &
  LayoutSlice;

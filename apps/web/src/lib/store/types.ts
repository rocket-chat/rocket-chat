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

export interface GoalSummary {
  status: string;
  consumedTokens: number;
  consumedCostUsd: number;
  continuationCount: number;
  maxContinuations: number;
}

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
    messageId?: string,
    files?: File[]
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
    update: {
      title?: string;
      is_pinned?: boolean;
      is_archived?: boolean;
      sandbox_status?: string;
      is_processing?: boolean;
      /** "approval" or "question" while a turn waits on a person, "" when it does not. */
      awaiting_input?: string;
      last_turn_status?: string;
    }
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
  /** Which changed file the Changes view should show (set from a step in the conversation). */
  selectedChangePath: string | null;
  /** The long task's budget so far (null when no task has needed continuation turns). */
  goal: GoalSummary | null;
  setGoal: (goal: GoalSummary | null) => void;
  /** Increases when a background process starts or ends, so the Processes tab reads its list. */
  processesVersion: number;
  bumpProcesses: () => void;
  /** Increases whenever the agent reports a change, telling the Changes view to read again. */
  changesVersion: number;
  terminalLogs: string;
  /** Characters trimmed from the front of terminalLogs so far (it keeps only the latest). */
  terminalBase: number;
  /** Changes whenever the terminal is reset, so the view clears instead of appending. */
  terminalEpoch: number;
  /** What running tool calls have printed so far, by call id (the tail only). */
  liveToolOutput: Record<string, string>;
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
  selectChange: (path: string | null) => void;
  bumpChanges: () => void;
  appendTerminalLog: (text: string) => void;
  appendToolOutput: (callId: string, text: string) => void;
  clearToolOutput: (callId: string) => void;
  setTokenRate: (rate: number) => void;
  updateToolCall: (nameOrId: string, data: Partial<ToolCallData>) => void;
  updateSubagent: (subagentId: string, data: Partial<SubagentExecutionData>) => void;
  artifacts: ArtifactItem[];
  upsertArtifact: (artifact: ArtifactItem) => void;
  setArtifacts: (artifacts: ArtifactItem[]) => void;
}

/** A message sent while a turn ran, not yet taken by a turn. */
export interface PendingMessage {
  messageId: string;
  content: string;
  /** "steer": taken by the running turn at its next step; "queue": run after it. */
  delivery: "steer" | "queue";
}

export interface ComposerSlice {
  /** Messages sent during the running turn that no turn has taken yet, oldest first. */
  pendingMessages: PendingMessage[];
  addPendingMessage: (message: PendingMessage) => void;
  /** What the server holds for the session (a reload or another session). */
  setPendingMessages: (messages: PendingMessage[]) => void;
  /** Taken by a turn, or withdrawn: it leaves the composer's list. */
  removePendingMessage: (messageId: string) => void;
  suggestedFollowup: string | null;
  setSuggestedFollowup: (followup: string | null) => void;
  /** Text the empty state or a template drops into the composer; the nonce re-fires an identical seed. */
  composerSeed: { text: string; nonce: number } | null;
  seedComposer: (text: string) => void;
  /** A seed is applied once; leaving it in the store would refill every composer that mounts later. */
  clearComposerSeed: () => void;
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
  /** The inspector's current tab, so the app can bring up the one that matters. */
  inspectorTab: InspectorTab;
  setInspectorTab: (tab: InspectorTab) => void;
  /** Whether the app already opened the inspector by itself for this session (it does so once). */
  inspectorAutoOpened: boolean;
  /** Opens the inspector on `tab` the first time something worth seeing happens (wide screens only). */
  autoOpenInspector: (tab: InspectorTab) => void;
  /** The agent-output terminal: null follows whether anything was printed. */
  terminalOpen: boolean | null;
  setTerminalOpen: (open: boolean | null) => void;
  /** Whether a command has printed in this session. */
  terminalActivity: boolean;
  /** Background processes running now, from process_started / process_exited. */
  runningProcesses: number;
  adjustRunningProcesses: (delta: number) => void;
}

export type InspectorTab = "changes" | "files" | "processes" | "subagents" | "artifacts" | "preview";

export type MissionStore = SessionsSlice &
  AgentsSlice &
  StreamSlice &
  ComposerSlice &
  LayoutSlice;

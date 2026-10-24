import { useMissionStore } from "./store";
import { formatEventTime } from "./store/helpers";
import {
  ArtifactItem,
  ChecklistTask,
  DecisionQuestion,
  FileDiffItem,
  ReplayFrame,
  SessionData,
  SyncFrame,
} from "../types/mission";
import {
  advanceCursor,
  adoptCursor,
  isAlreadyApplied,
  readCursor,
  rewindCursor,
  selectUnseenEvents,
} from "./session-cursor";
import { planSync, type SyncPlan } from "./sync-plan";

/**
 * The transport boundary. Server frames are untyped JSON whose payload shape is
 * discriminated by `event_type`; declaring the known fields here keeps that boundary
 * explicit instead of spreading casts through every event branch.
 */
interface EventPayload {
  delta?: string;
  status?: string;
  created_at?: number;
  state?: string;
  message?: string;
  error?: string;
  reason?: string;
  turns_taken?: number;
  max_turns?: number;
  name?: string;
  id?: string;
  arguments?: Record<string, unknown>;
  result?: unknown;
  duration_ms?: number;
  subagent_id?: string;
  role?: string;
  task?: string;
  turn?: number;
  thought?: string;
  summary?: string;
  text?: string;
  tool_call_id?: string;
  awaiting_input?: string;
  last_turn_status?: string;
  is_processing?: boolean;
  consumed_tokens?: number;
  consumed_cost_usd?: number;
  continuation_count?: number;
  max_continuations?: number;
  path?: string;
  diff_content?: string;
  additions?: number;
  deletions?: number;
  is_new_file?: boolean;
  question_id?: string;
  question_text?: string;
  options?: string[];
  is_multi_select?: boolean;
  default_recommended_option?: string | null;
  action_id?: string;
  action_type?: string;
  command_or_path?: string;
  description?: string;
  risk_level?: string;
  tasks?: ChecklistTask[];
  suggestion?: string;
  session_id?: string;
  title?: string;
  message_id?: string;
  content?: string;
  author_user_id?: string | null;
  /** On a turn's "running" status: the agent (empty for a raw model) and model it runs with. */
  agent_id?: string;
  agent_name?: string;
  model?: string;
  wait_id?: string;
  wait_type?: string;
  target?: string;
  timeout_seconds?: number | null;
  [field: string]: unknown;
}

export function getDefaultWsBaseUrl(): string {
  if (typeof window !== "undefined") {
    const hostname = window.location.hostname;
    const isLocal = hostname === "localhost" || hostname === "127.0.0.1";
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";

    // If explicit custom NEXT_PUBLIC_WS_URL is provided, only honor it if it's not a baked localhost default on a remote domain
    const configuredWs = process.env.NEXT_PUBLIC_WS_URL;
    if (configuredWs) {
      const isConfiguredLocal =
        configuredWs.includes("localhost") || configuredWs.includes("127.0.0.1");
      if (!isConfiguredLocal || isLocal) {
        return configuredWs;
      }
    }

    // In local development and testing (e.g. Next.js on port 3000, FastAPI on 8000)
    if (isLocal && (window.location.port === "3000" || window.location.port === "3333")) {
      return `ws://${hostname}:8000`;
    }

    // In all deployed environments (Kubernetes, production, reverse proxy):
    // Use the current origin host (e.g. wss://rocket-chat.int-tools.fifteen.eu)
    return `${protocol}//${window.location.host}`;
  }
  return "ws://127.0.0.1:8000";
}

let activeMissionWebSocket: MissionWebSocket | null = null;

export function getActiveMissionWebSocket(): MissionWebSocket | null {
  return activeMissionWebSocket;
}

export class MissionWebSocket {
  private ws: WebSocket | null = null;
  private url: string;
  private sessionId: string;
  private token: string;
  private reconnectAttempts = 0;
  private maxReconnectAttempts = 5;
  private reconnectTimer: NodeJS.Timeout | null = null;
  private isExplicitlyClosed = false;
  /** Cursor this connection asked the server to replay from, if any. */
  private resumeRequestedSeq: number | null = null;
  /** True while the transcript is being rebuilt from the session snapshot. */
  private isResyncingFromSnapshot = false;

  constructor(sessionId: string, baseUrl?: string, token?: string) {
    this.sessionId = sessionId;
    this.token = token || "";
    const base = baseUrl || getDefaultWsBaseUrl();
    const tokenQuery = token ? `&token=${encodeURIComponent(token)}` : "";
    this.url = `${base}/v1/sessions/${sessionId}/ws?session_id=${sessionId}${tokenQuery}`;
    // eslint-disable-next-line @typescript-eslint/no-this-alias
    activeMissionWebSocket = this;
  }

  public getSessionId(): string {
    return this.sessionId;
  }

  public getToken(): string {
    return this.token;
  }

  public isConnectedOrConnecting(): boolean {
    return !!this.ws && (this.ws.readyState === WebSocket.OPEN || this.ws.readyState === WebSocket.CONNECTING);
  }

  public connect(): void {
    if (this.ws && (this.ws.readyState === WebSocket.OPEN || this.ws.readyState === WebSocket.CONNECTING)) {
      return;
    }
    this.isExplicitlyClosed = false;

    try {
      this.ws = new WebSocket(this.url);

      this.ws.onopen = () => {
        useMissionStore.getState().setConnected(true);
        this.reconnectAttempts = 0;
        this.requestMissedEvents();
      };

      this.ws.onclose = (event: CloseEvent) => {
        useMissionStore.getState().setConnected(false);
        if (event.code === 1008 || event.reason === "Session not found") {
          this.isExplicitlyClosed = true;
          return;
        }
        if (!this.isExplicitlyClosed) {
          this.scheduleReconnect();
        }
      };

      this.ws.onerror = () => {
        useMissionStore.getState().setConnected(false);
      };

      this.ws.onmessage = (event) => {
        this.handleMessage(event.data);
      };
    } catch {
      if (!this.isExplicitlyClosed) {
        this.scheduleReconnect();
      }
    }
  }

  private scheduleReconnect(): void {
    if (this.isExplicitlyClosed) return;
    if (this.reconnectAttempts >= this.maxReconnectAttempts) return;
    this.reconnectAttempts++;
    const delay = Math.min(1000 * Math.pow(2, this.reconnectAttempts), 5000);
    this.reconnectTimer = setTimeout(() => this.connect(), delay);
  }

  private handleMessage(dataStr: string): void {
    let data: Record<string, unknown>;
    try {
      const parsed: unknown = JSON.parse(dataStr);
      if (typeof parsed !== "object" || parsed === null) return;
      data = parsed as Record<string, unknown>;
    } catch {
      // Discard invalid frames
      return;
    }

    if (data.type === "sync") {
      this.handleSyncFrame(data as unknown as SyncFrame);
      return;
    }
    if (data.type === "replay") {
      this.handleReplayFrame(data as unknown as ReplayFrame);
      return;
    }
    if (data.type === "pong") return;

    const seq = typeof data.seq === "number" ? data.seq : null;
    // Frames published while the snapshot refetch is in flight are superseded by that
    // snapshot, and a frame whose seq we already applied is a replay overlap or a
    // redelivery of the same event.
    if (this.isResyncingFromSnapshot) return;
    if (seq !== null && isAlreadyApplied(this.sessionId, seq)) return;

    const timestampMs = typeof data.timestamp_ms === "number" ? data.timestamp_ms : undefined;
    this.applyEvent(
      String(data.event_type || ""),
      (data.payload || {}) as EventPayload,
      String(data.session_id || ""),
      timestampMs,
      seq ?? undefined
    );
    if (seq !== null) advanceCursor(this.sessionId, seq);
  }

  /**
   * Asks for the events published while this tab was away. Sent on every open: the
   * server answers with a `replay` control frame, which is what turns a reconnect
   * into a lossless resume instead of a restart from "now".
   */
  private requestMissedEvents(): void {
    const socket = this.ws;
    if (!socket || socket.readyState !== WebSocket.OPEN) return;
    const cursor = readCursor(this.sessionId);
    this.resumeRequestedSeq = cursor;
    if (cursor === null) return;
    try {
      socket.send(JSON.stringify({ type: "resume", last_seq: cursor }));
    } catch (err) {
      console.warn("[MissionWebSocket] Failed to request missed events:", err);
    }
  }

  /**
   * Adopts the handshake snapshot. `sync.session` is the full session record, so when
   * this connection did not ask for a replay it is the cockpit's baseline and the
   * server's mark can be trusted. While a resume is in flight the gap must stay
   * unclaimed until the replayed events have been applied.
   */
  private handleSyncFrame(frame: SyncFrame): void {
    const store = useMissionStore.getState();
    const active = store.session;
    if (frame.session && (!active || active.session_id === this.sessionId)) {
      store.setSession(frame.session);
    }
    if (frame.running_turn && frame.running_turn.status === "running") {
      store.setIsExecuting(true);
    }
    this.applySyncPlan(planSync(frame));
    // A turn paused on a person before this tab connected: show the card it is waiting on.
    if (frame.pending_approval) {
      this.applyEvent("approval_required", frame.pending_approval, this.sessionId);
    }
    if (frame.pending_decision) {
      this.applyEvent("decision_required", frame.pending_decision, this.sessionId);
    }
    void this.loadPendingMessages();
  }

  /** The messages no turn has taken yet: kept by the server, so a reload still shows them. */
  private async loadPendingMessages(): Promise<void> {
    const sessionId = this.sessionId;
    const headers: Record<string, string> = {};
    if (this.token) headers["Authorization"] = `Bearer ${this.token}`;
    try {
      const res = await fetch(`/v1/sessions/${sessionId}/queued-messages`, { headers });
      if (!res.ok) return;
      const pending: { message_id: string; delivery: string; content: string }[] = await res.json();
      const store = useMissionStore.getState();
      if (store.session?.session_id !== sessionId) return;
      const held = pending.map((m) => ({
        messageId: m.message_id,
        content: m.content,
        delivery: m.delivery === "queue" ? ("queue" as const) : ("steer" as const),
      }));
      // A message sent from this tab while the list was in flight is not in it yet: keep it.
      const heldIds = new Set(held.map((m) => m.messageId));
      store.setPendingMessages([...held, ...store.pendingMessages.filter((m) => !heldIds.has(m.messageId))]);
    } catch {
      // Pending messages are shown again by the next sync; nothing else depends on them.
    }
  }

  /**
   * Folds in what the snapshot record cannot hold yet, so a tab that connects while a turn
   * runs (or as it ends) still shows the answer. Tokens of an already stored answer are
   * ignored by the store, so overlap with the record is harmless.
   */
  private applySyncPlan(plan: SyncPlan): void {
    if (plan.rewindCursorTo !== null) rewindCursor(this.sessionId, plan.rewindCursorTo);
    if (plan.runningTurn && Array.isArray(plan.runningTurn.steps)) {
      const store = useMissionStore.getState();
      const existingToolIds = new Set(
        store.flightLog.map((l) => l.toolCall?.id).filter(Boolean)
      );
      for (const step of plan.runningTurn.steps) {
        if (step.id && existingToolIds.has(step.id)) continue;
        const stepTs = formatEventTime(
          step.created_at ?? (plan.runningTurn.started_at ? plan.runningTurn.started_at * 1000 : null)
        );
        if (step.reasoning) {
          store.appendFlightLog({
            id: step.id ? `thought-${step.id}` : `thought-${Date.now()}-${Math.random()}`,
            role: "AGENT",
            content: "",
            reasoning: step.reasoning,
            timestamp: stepTs,
            isPersisted: true,
          });
        }
        store.appendFlightLog({
          id: step.id ? `tool-${step.id}` : `tool-${Date.now()}-${Math.random()}`,
          role: "CODER",
          content: `Executing tool: ${step.name}`,
          timestamp: stepTs,
          toolCall: {
            id: step.id,
            name: step.name,
            arguments: step.arguments,
            status: step.status === "error" ? "error" : "success",
            result: typeof step.result === "string" ? step.result : JSON.stringify(step.result, null, 2),
            duration_ms: step.duration_ms,
            created_at: step.created_at,
          },
        });
      }
      if (plan.runningTurn.current_reasoning) {
        store.appendReasoning(plan.runningTurn.current_reasoning);
        store.setIsReasoningActive(true);
      }
    }
    for (const event of selectUnseenEvents(plan.events, readCursor(this.sessionId))) {
      this.applyEvent(
        event.event_type,
        event.payload || {},
        event.session_id || this.sessionId,
        event.timestamp_ms,
        typeof event.seq === "number" ? event.seq : undefined
      );
      if (typeof event.seq === "number") advanceCursor(this.sessionId, event.seq);
    }
    if (plan.continueFrom !== null) adoptCursor(this.sessionId, plan.continueFrom);
  }

  /** Applies a `replay` frame's events in order, exactly once, then resumes live delivery. */
  private handleReplayFrame(frame: ReplayFrame): void {
    if (frame.continuous === false) {
      void this.resyncFromSnapshot(frame.last_seq ?? null);
      return;
    }
    const events = Array.isArray(frame.events) ? frame.events : [];
    for (const event of selectUnseenEvents(events, readCursor(this.sessionId))) {
      this.applyEvent(
        event.event_type,
        event.payload || {},
        event.session_id || this.sessionId,
        event.timestamp_ms,
        typeof event.seq === "number" ? event.seq : undefined
      );
      if (typeof event.seq === "number") advanceCursor(this.sessionId, event.seq);
    }
  }

  /**
   * Rebuilds the transcript for a discontinuous replay.
   *
   * `continuous: false` means the server can no longer prove that the suffix it holds
   * is complete (its buffer wrapped, or our cursor is ahead of a restarted bus), so the
   * partial events are discarded and the authoritative session snapshot replaces the
   * transcript. Live frames are ignored while this runs: they belong to the window the
   * snapshot is about to cover.
   */
  private async resyncFromSnapshot(replayLastSeq: number | null): Promise<void> {
    const store = useMissionStore.getState();
    store.setStreamContinuous(false);
    this.isResyncingFromSnapshot = true;
    try {
      const headers: Record<string, string> = {};
      if (this.token) headers["Authorization"] = `Bearer ${this.token}`;
      const res = await fetch(`/v1/sessions/${this.sessionId}`, {
        headers,
        signal: AbortSignal.timeout(8000),
      });
      if (!res.ok) return;
      const snapshot = (await res.json()) as SessionData | null;
      if (!snapshot || snapshot.session_id !== this.sessionId) return;
      store.setSession(snapshot);
      // The snapshot was read after the replay frame was built, so it covers every
      // sequence that frame accounted for.
      if (replayLastSeq !== null) adoptCursor(this.sessionId, replayLastSeq);
    } catch (err) {
      console.warn("[MissionWebSocket] Snapshot resync failed:", err);
    } finally {
      this.isResyncingFromSnapshot = false;
    }
  }

  private applyEvent(
    eventType: string,
    payload: EventPayload,
    frameSessionId: string,
    timestampMs?: number,
    seq?: number
  ): void {
    const store = useMissionStore.getState();
    const eventTime = formatEventTime(timestampMs);
    // Entries the conversation derives from an event are named by its sequence number, so a
    // replayed or re-synced event lands on the entry it made the first time.
    const eventKey = seq ?? Date.now();

    switch (eventType) {
      case "token":
        store.updateLastAgentLog(payload.delta || "", eventTime);
        store.setIsExecuting(true);
        break;

      case "reasoning_token":
        store.appendReasoning(payload.delta || "", eventTime);
        store.setIsReasoningActive(true);
        store.setIsExecuting(true);
        break;

      case "status_changed": {
        const rawStatus = (payload.status || payload.state || "").toLowerCase();
        const message = payload.message || payload.error || rawStatus || "";
        store.setStatusMessage(message);

        if (payload.discard_partial || rawStatus === "retrying") {
          store.discardPartialTokens();
        }

        if (rawStatus === "running") {
          store.setActiveWait(null);
          store.setIsExecuting(true);
          if (typeof payload.model === "string") {
            store.setTurnBinding({ agentName: payload.agent_name || "", model: payload.model });
          }
        } else if (rawStatus === "retrying" || rawStatus === "ready") {
          store.setIsExecuting(true);
        } else if (
          rawStatus === "completed" ||
          rawStatus === "error" ||
          rawStatus === "failed" ||
          rawStatus === "cancelled" ||
          rawStatus === "waiting" ||
          payload.final === true
        ) {
          store.setIsExecuting(false);
          store.setIsReasoningActive(false);
          // A turn that ended (answered, failed, cancelled, or waiting) waits for nobody any more, so a
          // question or approval card left on screen would answer nothing.
          store.setActiveDecision(null);
          store.setActiveApproval(null);

          if (rawStatus === "error" || rawStatus === "failed" || payload.error) {
            const errText = payload.error || payload.message || "Unknown execution error";
            store.appendFlightLog({
              id: `err-${eventKey}`,
              role: "SYSTEM",
              content: `⚠️ Mission Execution Error: ${errText}`,
              timestamp: eventTime,
            });
          }

        }
        break;
      }

      case "post_failure_synthesis": {
        const turns = payload.turns_taken || payload.max_turns || 60;
        const reason = payload.reason || "Turn limit reached";
        store.setStatusMessage(`Synthesizing findings: ${reason}`);
        store.appendFlightLog({
          id: `synthesis-${eventKey}`,
          role: "SYSTEM",
          content: `⚡ Turn Budget Exceeded (${turns} turns) — Synthesizing final answer from gathered context...`,
          timestamp: eventTime,
        });
        break;
      }

      case "sandbox_state": {
        if (payload.state === "failed") {
          const reason = payload.message || "The sandbox could not start.";
          store.setStatusMessage(reason);
          store.appendFlightLog({
            id: `sandbox-${eventKey}`,
            role: "SYSTEM",
            content: `⚠️ ${reason}`,
            timestamp: eventTime,
          });
        } else if (payload.state) {
          store.applySessionUpdate(frameSessionId ?? "", {
            sandbox_status: payload.state,
          });
        }
        break;
      }

      case "github_event": {
        const target = payload.pr_number ? `${payload.repo}#${payload.pr_number}` : payload.repo;
        const headline = String(payload.summary || "").split("\n")[0];
        store.appendFlightLog({
          id: `github-${payload.at ?? Date.now()}-${payload.kind}`,
          role: "SYSTEM",
          content: `GitHub ${target}: ${headline}${payload.woke ? " — the agent is picking it up" : ""}`,
          timestamp: eventTime,
          githubEvent: {
            target: String(target),
            headline,
            outcome: String(payload.outcome || ""),
            url: String(payload.url || ""),
          },
        });
        break;
      }

      case "error": {
        const errText = payload.error || payload.message || "An error occurred";
        store.setStatusMessage(`Error: ${errText}`);
        store.setIsExecuting(false);
        store.setIsReasoningActive(false);
        store.appendFlightLog({
          id: `err-${eventKey}`,
          role: "SYSTEM",
          content: `⚠️ Mission Execution Error: ${errText}`,
          timestamp: eventTime,
        });
        break;
      }

      case "tool_started": {
        const toolName = payload.name || "";
        const toolId = payload.id;
        const toolCreatedAt =
          typeof payload.created_at === "number"
            ? payload.created_at
            : timestampMs
              ? (timestampMs < 1e11 ? timestampMs : Math.floor(timestampMs / 1000))
              : undefined;
        store.appendFlightLog({
          id: toolId ? `tool-${toolId}` : `tool-${eventKey}`,
          role: "CODER",
          content: `Executing tool: ${toolName}`,
          timestamp: eventTime,
          toolCall: {
            id: toolId,
            name: toolName,
            arguments: payload.arguments,
            status: "pending",
            created_at: toolCreatedAt,
          },
        });
        break;
      }

      case "tool_completed": {
        const toolName = payload.name || "";
        const toolId = payload.id;
        const toolCreatedAt =
          typeof payload.created_at === "number"
            ? payload.created_at
            : timestampMs
              ? (timestampMs < 1e11 ? timestampMs : Math.floor(timestampMs / 1000))
              : undefined;
        if (toolId) store.clearToolOutput(toolId);
        store.updateToolCall(toolId || toolName || "", {
          id: toolId,
          status: payload.status === "error" ? "error" : "success",
          result: typeof payload.result === "string" ? payload.result : JSON.stringify(payload.result, null, 2),
          duration_ms: payload.duration_ms,
          arguments: payload.arguments,
          created_at: toolCreatedAt,
        });
        break;
      }

      case "subagent_started": {
        const subagentId = payload.subagent_id || `subagent-${eventKey}`;
        const role = payload.role || "specialist";
        const task = payload.task || "";
        store.appendFlightLog({
          id: `subagent-${subagentId}`,
          role: "CODER",
          content: `Delegating to subagent [${role}]: ${task}`,
          timestamp: eventTime,
          subagent: {
            subagentId,
            role,
            task,
            status: "running",
          },
        });
        break;
      }

      case "subagent_progress": {
        const subagentId = payload.subagent_id;
        if (subagentId) {
          store.updateSubagent(subagentId, {
            turn: payload.turn,
            thought: payload.thought,
          });
        }
        break;
      }

      case "subagent_completed": {
        const subagentId = payload.subagent_id;
        if (subagentId) {
          store.updateSubagent(subagentId, {
            status: payload.status === "error" ? "error" : "completed",
            summary: payload.summary,
            duration_ms: payload.duration_ms,
          });
        }
        break;
      }

      case "log_chunk":
        store.appendTerminalLog(payload.text || "");
        if (typeof payload.tool_call_id === "string" && payload.tool_call_id) {
          store.appendToolOutput(payload.tool_call_id, payload.text || "");
        }
        break;

      case "file_diff": {
        const diffItem: FileDiffItem = {
          path: payload.path || "",
          diff_content: payload.diff_content || "",
          additions: payload.additions || 0,
          deletions: payload.deletions || 0,
          is_new_file: payload.is_new_file || false,
        };
        store.addFileDiff(diffItem);
        store.autoOpenInspector("changes");
        break;
      }

      case "decision_required": {
        const question: DecisionQuestion = {
          question_id: payload.question_id || "",
          question_text: payload.question_text || "",
          options: payload.options || [],
          is_multi_select: payload.is_multi_select || false,
          default_recommended_option: payload.default_recommended_option,
        };
        store.setActiveDecision(question);
        break;
      }

      case "decision_submitted":
        store.setActiveDecision(null);
        store.setActiveApproval(null);
        break;

      case "approval_required": {
        store.setActiveApproval({
          action_id: payload.action_id || "",
          action_type: payload.action_type || "action",
          description: payload.command_or_path || payload.description || "",
          risk_level: payload.risk_level || "medium",
        });
        break;
      }

      case "plan_updated":
        store.setChecklist(Array.isArray(payload.tasks) ? payload.tasks : []);
        break;

      case "artifact_created":
      case "artifact_updated": {
        const art = payload as unknown as ArtifactItem;
        if (art && art.id) {
          store.upsertArtifact(art);
          store.appendFlightLog({
            id: `artifact-${art.id}-${art.version || Date.now()}`,
            role: "SYSTEM",
            content: `Deliverable: ${art.title} (${art.filename})`,
            timestamp: eventTime,
          });
        }
        break;
      }

      case "context_compacted": {
        const count = payload.compacted_messages_count ?? 0;
        const tokensBefore = payload.tokens_before ?? 0;
        const tokensAfter = payload.tokens_after ?? 0;
        const summary = typeof payload.summary === "string" ? payload.summary : "Working context compacted";
        store.appendFlightLog({
          id: `compacted-${eventKey}`,
          role: "SYSTEM",
          content: `Context Compacted: Compressed ${count} messages from ${Number(tokensBefore).toLocaleString()} to ${Number(tokensAfter).toLocaleString()} tokens.${
            payload.summary_status === "digest" ? " The model's summary was unusable, so it was rebuilt from the tool history." : ""
          }\n\n${summary}`,
          timestamp: eventTime,
        });
        break;
      }

      case "continuation_scheduled": {
        const turnIdx = payload.continuation_index ?? 1;
        const maxTurns = payload.max_continuations ?? 10;
        const reason = payload.reason || "Turn step limit reached with unfinished tasks";
        store.appendFlightLog({
          id: `continuation-${eventKey}`,
          role: "SYSTEM",
          content: `Goal Continuation (${turnIdx}/${maxTurns}): ${reason}`,
          timestamp: eventTime,
        });
        break;
      }

      case "process_started":
        store.bumpProcesses();
        store.adjustRunningProcesses(1);
        store.autoOpenInspector("processes");
        break;

      case "process_exited":
        store.bumpProcesses();
        store.adjustRunningProcesses(-1);
        break;

      case "goal_updated": {
        // Shown as a chip in the header: a budget readout, not something to scroll past.
        store.setGoal({
          status: payload.status || "active",
          consumedTokens: typeof payload.consumed_tokens === "number" ? payload.consumed_tokens : 0,
          consumedCostUsd: typeof payload.consumed_cost_usd === "number" ? payload.consumed_cost_usd : 0,
          continuationCount: typeof payload.continuation_count === "number" ? payload.continuation_count : 0,
          maxContinuations: typeof payload.max_continuations === "number" ? payload.max_continuations : 0,
        });
        break;
      }

      case "wait_started": {
        const waitId = typeof payload.wait_id === "string" ? payload.wait_id : `wait-${eventKey}`;
        const waitType = typeof payload.wait_type === "string" ? payload.wait_type : "time";
        const target = typeof payload.target === "string" ? payload.target : "";
        const timeoutSeconds = typeof payload.timeout_seconds === "number" ? payload.timeout_seconds : null;
        const reason = payload.reason || target || "Waiting for external event";
        store.setActiveWait({
          wait_id: waitId,
          wait_type: waitType,
          target,
          timeout_seconds: timeoutSeconds,
          reason: typeof payload.reason === "string" ? payload.reason : null,
        });
        store.appendFlightLog({
          id: `wait-${eventKey}`,
          role: "SYSTEM",
          content: `Waiting for condition: ${reason}`,
          timestamp: eventTime,
          wait: {
            waitId,
            waitType,
            target,
            sha: typeof payload.sha === "string" ? payload.sha : undefined,
            timeoutSeconds,
            status: "waiting",
          },
        });
        break;
      }

      case "wait_completed": {
        store.setActiveWait(null);
        const summary = payload.summary || "External condition satisfied";
        const waitId = typeof payload.wait_id === "string" ? payload.wait_id : "";
        const settled = {
          status: typeof payload.status === "string" ? payload.status : "completed",
          summary,
        };
        // The wait's own card settles in place; a completion whose start this tab never saw
        // (a snapshot began after it) gets a card of its own.
        if (waitId && useMissionStore.getState().updateWait(waitId, settled)) break;
        store.appendFlightLog({
          id: `wait-done-${eventKey}`,
          role: "SYSTEM",
          content: `Wait completed: ${summary}`,
          timestamp: eventTime,
          wait: {
            waitId: waitId || `wait-done-${eventKey}`,
            waitType: typeof payload.wait_type === "string" ? payload.wait_type : "time",
            target: typeof payload.target === "string" ? payload.target : "",
            timeoutSeconds: null,
            ...settled,
          },
        });
        break;
      }

      case "followup_suggestion": {
        const suggestion = typeof payload.suggestion === "string" ? payload.suggestion.trim() : null;
        store.setSuggestedFollowup(suggestion || null);
        break;
      }

      case "session_updated": {
        const targetSessionId = payload.session_id || frameSessionId;
        if (targetSessionId) {
          store.applySessionUpdate(targetSessionId, {
            title: typeof payload.title === "string" ? payload.title : undefined,
            is_pinned: typeof payload.is_pinned === "boolean" ? payload.is_pinned : undefined,
            is_archived: typeof payload.is_archived === "boolean" ? payload.is_archived : undefined,
            is_processing: typeof payload.is_processing === "boolean" ? payload.is_processing : undefined,
            awaiting_input: typeof payload.awaiting_input === "string" ? payload.awaiting_input : undefined,
            last_turn_status: typeof payload.last_turn_status === "string" ? payload.last_turn_status : undefined,
          });
        }
        break;
      }

      case "message_pending": {
        if (typeof payload.message_id === "string" && typeof payload.content === "string") {
          store.addPendingMessage({
            messageId: payload.message_id,
            content: payload.content,
            delivery: payload.delivery === "queue" ? "queue" : "steer",
          });
        }
        break;
      }

      case "message_cancelled": {
        if (typeof payload.message_id === "string") store.removePendingMessage(payload.message_id);
        break;
      }

      case "user_message": {
        const content = payload.content;
        // Taken by a turn: it leaves the pending list and enters the conversation here.
        if (typeof payload.message_id === "string") store.removePendingMessage(payload.message_id);
        if (typeof content === "string" && content.trim()) {
          store.ingestUserMessage({
            messageId: typeof payload.message_id === "string" ? payload.message_id : null,
            content,
            authorUserId: typeof payload.author_user_id === "string" ? payload.author_user_id : null,
            timestamp: eventTime,
          });
        }
        break;
      }

      default:
        break;
    }
  }

  /** Withdraw a message sent during a turn that no turn has taken yet. */
  public cancelPendingMessage(messageId: string): void {
    useMissionStore.getState().removePendingMessage(messageId);
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify({ type: "cancel_message", message_id: messageId }));
      return;
    }
    const headers: Record<string, string> = {};
    if (this.token) headers["Authorization"] = `Bearer ${this.token}`;
    void fetch(`/v1/sessions/${this.sessionId}/queued-messages/${encodeURIComponent(messageId)}`, {
      method: "DELETE",
      headers,
    });
  }

  public async sendTurn(
    content: string,
    model?: string,
    messageId?: string,
    attachmentIds?: string[],
    delivery: "steer" | "queue" = "steer"
  ): Promise<void> {
    const store = useMissionStore.getState();
    store.setIsExecuting(true);
    const ids = attachmentIds && attachmentIds.length > 0 ? attachmentIds : undefined;
    // The turn names its own agent (or a raw model): the selection it was sent with answers,
    // even if the session's stored selection is updated a moment later.
    const choice =
      store.inferenceMode === "direct"
        ? { mode: "direct" as const }
        : { mode: "agent" as const, agent_id: store.activeAgent?.id };

    // If WebSocket is currently connecting, await briefly for handshake before falling back
    if (this.ws && this.ws.readyState === WebSocket.CONNECTING) {
      await new Promise<void>((resolve) => {
        const timeout = setTimeout(resolve, 2000);
        const check = () => {
          if (!this.ws || this.ws.readyState !== WebSocket.CONNECTING) {
            clearTimeout(timeout);
            resolve();
          } else {
            setTimeout(check, 50);
          }
        };
        check();
      });
    }

    // If WebSocket is open and ready, transmit through WebSocket frame
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      try {
        this.ws.send(
          JSON.stringify({
            type: "turn",
            content,
            model,
            message_id: messageId,
            attachment_ids: ids,
            delivery,
            ...choice,
          })
        );
        return;
      } catch (err) {
        console.warn("[MissionWebSocket] Failed to send via socket, falling back to HTTP:", err);
      }
    }

    // HTTP fallback / universal guarantee: dispatch turn via /v1/sessions/:id/turns
    try {
      const headers: Record<string, string> = { "Content-Type": "application/json" };
      if (this.token) {
        headers["Authorization"] = `Bearer ${this.token}`;
      }
      const res = await fetch(`/v1/sessions/${this.sessionId}/turns`, {
        method: "POST",
        headers,
        body: JSON.stringify({
          prompt: content,
          model,
          message_id: messageId,
          attachment_ids: ids,
          delivery,
          ...choice,
        }),
      });
      if (!res.ok) {
        const errorText = await res.text();
        throw new Error(`HTTP ${res.status}: ${errorText || res.statusText}`);
      }
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : String(err);
      store.setIsExecuting(false);
      store.setIsReasoningActive(false);
      store.setStatusMessage(`Failed to dispatch message: ${msg}`);
      store.appendFlightLog({
        id: `err-send-${Date.now()}`,
        role: "SYSTEM",
        content: `⚠️ Dispatch Error: Failed to transmit instruction to backend (${msg}).`,
        timestamp: new Date().toLocaleTimeString(),
      });
    }
  }

  public async cancelTurn(): Promise<void> {
    const store = useMissionStore.getState();
    store.setIsExecuting(false);
    store.setIsReasoningActive(false);
    store.setStatusMessage("Turn interrupted by user.");
    store.appendFlightLog({
      id: `cancel-${Date.now()}`,
      role: "SYSTEM",
      content: "🛑 Mission Turn Cancelled / Interrupted.",
      timestamp: new Date().toLocaleTimeString(),
    });

    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      try {
        this.ws.send(JSON.stringify({ type: "cancel" }));
      } catch (err) {
        console.warn("[MissionWebSocket] Failed to send cancel via socket:", err);
      }
    }

    try {
      const headers: Record<string, string> = {};
      if (this.token) {
        headers["Authorization"] = `Bearer ${this.token}`;
      }
      await fetch(`/v1/sessions/${this.sessionId}/turns`, {
        method: "DELETE",
        headers,
      });
    } catch (err) {
      console.warn("[MissionWebSocket] Failed to cancel turn via HTTP DELETE:", err);
    }
  }

  public answerQuestion(questionId: string, selectedOptions: string[], customText?: string): void {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(
        JSON.stringify({
          type: "answer_question",
          question_id: questionId,
          selected_options: selectedOptions,
          custom_text: customText,
        })
      );
    }
  }

  public submitApproval(actionId: string, approved: boolean): void {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(
        JSON.stringify({
          type: "approval",
          action_id: actionId,
          approved,
        })
      );
    }
  }

  public sendTerminalInput(data: string): void {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(
        JSON.stringify({
          type: "terminal_input",
          data,
        })
      );
    }
  }

  public disconnect(): void {
    this.isExplicitlyClosed = true;
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    if (this.ws) {
      this.ws.onopen = null;
      this.ws.onclose = null;
      this.ws.onerror = null;
      this.ws.onmessage = null;
      this.ws.close();
      this.ws = null;
    }
  }
}

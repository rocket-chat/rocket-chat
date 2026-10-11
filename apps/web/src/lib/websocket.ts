import { useMissionStore } from "./store";
import {
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
import { newMessageId } from "./message-id";

/**
 * The transport boundary. Server frames are untyped JSON whose payload shape is
 * discriminated by `event_type`; declaring the known fields here keeps that boundary
 * explicit instead of spreading casts through every event branch.
 */
interface EventPayload {
  delta?: string;
  status?: string;
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

    this.applyEvent(
      String(data.event_type || ""),
      (data.payload || {}) as EventPayload,
      String(data.session_id || "")
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
    this.applySyncPlan(planSync(frame, this.resumeRequestedSeq !== null));
    // A turn paused on a person before this tab connected: show the card it is waiting on.
    if (frame.pending_approval) {
      this.applyEvent("approval_required", frame.pending_approval, this.sessionId);
    }
    if (frame.pending_decision) {
      this.applyEvent("decision_required", frame.pending_decision, this.sessionId);
    }
  }

  /**
   * Folds in what the snapshot record cannot hold yet, so a tab that connects while a turn
   * runs (or as it ends) still shows the answer. Tokens of an already stored answer are
   * ignored by the store, so overlap with the record is harmless.
   */
  private applySyncPlan(plan: SyncPlan): void {
    if (plan.rewindCursorTo !== null) rewindCursor(this.sessionId, plan.rewindCursorTo);
    for (const event of selectUnseenEvents(plan.events, readCursor(this.sessionId))) {
      this.applyEvent(event.event_type, event.payload || {}, event.session_id || this.sessionId);
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
      this.applyEvent(event.event_type, event.payload || {}, event.session_id || this.sessionId);
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

  private applyEvent(eventType: string, payload: EventPayload, frameSessionId: string): void {
    const store = useMissionStore.getState();

    switch (eventType) {
      case "token":
        store.updateLastAgentLog(payload.delta || "");
        store.setIsExecuting(true);
        break;

      case "reasoning_token":
        store.appendReasoning(payload.delta || "");
        store.setIsReasoningActive(true);
        store.setIsExecuting(true);
        break;

      case "status_changed": {
        const rawStatus = (payload.status || payload.state || "").toLowerCase();
        const message = payload.message || payload.error || rawStatus || "";
        store.setStatusMessage(message);

        if (rawStatus === "running") {
          store.setIsExecuting(true);
        } else if (
          rawStatus === "completed" ||
          rawStatus === "error" ||
          rawStatus === "failed" ||
          rawStatus === "cancelled"
        ) {
          store.setIsExecuting(false);
          store.setIsReasoningActive(false);

          if (rawStatus === "error" || rawStatus === "failed" || payload.error) {
            const errText = payload.error || payload.message || "Unknown execution error";
            store.appendFlightLog({
              id: `err-${Date.now()}`,
              role: "SYSTEM",
              content: `⚠️ Mission Execution Error: ${errText}`,
              timestamp: new Date().toLocaleTimeString(),
            });
          }

          // If a next prompt was queued during execution, automatically dispatch it
          if (rawStatus === "completed" || rawStatus === "cancelled") {
            const nextPrompt = store.popQueuedPrompt();
            if (nextPrompt) {
              const messageId = newMessageId();
              store.appendOwnUserMessage({
                messageId,
                content: nextPrompt,
                authorUserId: null,
              });
              setTimeout(() => {
                this.sendTurn(nextPrompt, undefined, messageId);
              }, 100);
            }
          }
        }
        break;
      }

      case "post_failure_synthesis": {
        const turns = payload.turns_taken || payload.max_turns || 60;
        const reason = payload.reason || "Turn limit reached";
        store.setStatusMessage(`Synthesizing findings: ${reason}`);
        store.appendFlightLog({
          id: `synthesis-${Date.now()}`,
          role: "SYSTEM",
          content: `⚡ Turn Budget Exceeded (${turns} turns) — Synthesizing final answer from gathered context...`,
          timestamp: new Date().toLocaleTimeString(),
        });
        break;
      }

      case "sandbox_state": {
        if (payload.state === "failed") {
          const reason = payload.message || "The sandbox could not start.";
          store.setStatusMessage(reason);
          store.appendFlightLog({
            id: `sandbox-${Date.now()}`,
            role: "SYSTEM",
            content: `⚠️ ${reason}`,
            timestamp: new Date().toLocaleTimeString(),
          });
        }
        break;
      }

      case "error": {
        const errText = payload.error || payload.message || "An error occurred";
        store.setStatusMessage(`Error: ${errText}`);
        store.setIsExecuting(false);
        store.setIsReasoningActive(false);
        store.appendFlightLog({
          id: `err-${Date.now()}`,
          role: "SYSTEM",
          content: `⚠️ Mission Execution Error: ${errText}`,
          timestamp: new Date().toLocaleTimeString(),
        });
        break;
      }

      case "tool_started": {
        const toolName = payload.name || "";
        const toolId = payload.id;
        store.appendFlightLog({
          id: `tool-${Date.now()}`,
          role: "CODER",
          content: `Executing tool: ${toolName}`,
          timestamp: new Date().toLocaleTimeString(),
          toolCall: {
            id: toolId,
            name: toolName,
            arguments: payload.arguments,
            status: "pending",
          },
        });
        break;
      }

      case "tool_completed": {
        const toolName = payload.name || "";
        const toolId = payload.id;
        store.updateToolCall(toolId || toolName || "", {
          id: toolId,
          status: payload.status === "error" ? "error" : "success",
          result: typeof payload.result === "string" ? payload.result : JSON.stringify(payload.result, null, 2),
          duration_ms: payload.duration_ms,
          arguments: payload.arguments,
        });
        break;
      }

      case "subagent_started": {
        const subagentId = payload.subagent_id || `subagent-${Date.now()}`;
        const role = payload.role || "specialist";
        const task = payload.task || "";
        store.appendFlightLog({
          id: `subagent-${subagentId}`,
          role: "CODER",
          content: `Delegating to subagent [${role}]: ${task}`,
          timestamp: new Date().toLocaleTimeString(),
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

      case "followup_suggestion": {
        const suggestion = typeof payload.suggestion === "string" ? payload.suggestion.trim() : null;
        store.setSuggestedFollowup(suggestion || null);
        break;
      }

      case "session_updated": {
        const targetSessionId = payload.session_id || frameSessionId;
        if (targetSessionId) {
          // A metadata mutation (rename, pin, archive) broadcast so every other tab
          // converges without a reload.
          store.applySessionUpdate(targetSessionId, {
            title: typeof payload.title === "string" ? payload.title : undefined,
            is_pinned: typeof payload.is_pinned === "boolean" ? payload.is_pinned : undefined,
            is_archived: typeof payload.is_archived === "boolean" ? payload.is_archived : undefined,
          });
        }
        break;
      }

      case "user_message": {
        const content = payload.content;
        if (typeof content === "string" && content.trim()) {
          store.ingestUserMessage({
            messageId: typeof payload.message_id === "string" ? payload.message_id : null,
            content,
            authorUserId: typeof payload.author_user_id === "string" ? payload.author_user_id : null,
          });
        }
        break;
      }

      default:
        break;
    }
  }

  public async sendTurn(
    content: string,
    model?: string,
    messageId?: string,
    attachmentIds?: string[]
  ): Promise<void> {
    const store = useMissionStore.getState();
    store.setIsExecuting(true);
    const ids = attachmentIds && attachmentIds.length > 0 ? attachmentIds : undefined;

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
          JSON.stringify({ type: "turn", content, model, message_id: messageId, attachment_ids: ids })
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
        body: JSON.stringify({ prompt: content, model, message_id: messageId, attachment_ids: ids }),
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

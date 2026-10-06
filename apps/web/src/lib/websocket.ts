import { useMissionStore } from "./store";
import { DecisionQuestion, FileDiffItem } from "../types/mission";

export function getDefaultWsBaseUrl(): string {
  if (typeof window !== "undefined") {
    if (process.env.NEXT_PUBLIC_WS_URL) {
      return process.env.NEXT_PUBLIC_WS_URL;
    }
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const hostname = window.location.hostname;
    // In local development and testing (port 3000, 3333, etc.), FastAPI runs on 8000
    const isLocal = hostname === "localhost" || hostname === "127.0.0.1";
    const port =
      isLocal && (window.location.port === "3000" || window.location.port === "3333")
        ? "8000"
        : window.location.port || (protocol === "wss:" ? "443" : "80");
    return `${protocol}//${hostname}:${port}`;
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
  private reconnectAttempts = 0;
  private maxReconnectAttempts = 5;
  private reconnectTimer: NodeJS.Timeout | null = null;
  private isExplicitlyClosed = false;

  constructor(sessionId: string, baseUrl?: string) {
    this.sessionId = sessionId;
    const base = baseUrl || getDefaultWsBaseUrl();
    this.url = `${base}/v1/sessions/${sessionId}/ws?token=dev_token`;
    // eslint-disable-next-line @typescript-eslint/no-this-alias
    activeMissionWebSocket = this;
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
    try {
      const data = JSON.parse(dataStr);
      const store = useMissionStore.getState();

      if (data.type === "sync") {
        const active = store.session;
        if (data.session && (!active || active.session_id === this.sessionId)) {
          store.setSession(data.session);
        }
        return;
      }

      const eventType = data.event_type;
      const payload = data.payload || {};

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
          const toolName = payload.name;
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
          const toolName = payload.name;
          const toolId = payload.id;
          store.updateToolCall(toolId || toolName, {
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
            path: payload.path,
            diff_content: payload.diff_content,
            additions: payload.additions || 0,
            deletions: payload.deletions || 0,
            is_new_file: payload.is_new_file || false,
          };
          store.addFileDiff(diffItem);
          break;
        }

        case "decision_required": {
          const question: DecisionQuestion = {
            question_id: payload.question_id,
            question_text: payload.question_text,
            options: payload.options || [],
            is_multi_select: payload.is_multi_select || false,
            default_recommended_option: payload.default_recommended_option,
          };
          store.setActiveDecision(question);
          break;
        }

        case "decision_submitted":
          store.setActiveDecision(null);
          break;

        case "plan_updated":
          store.setChecklist(Array.isArray(payload.tasks) ? payload.tasks : []);
          break;

        case "followup_suggestion": {
          const suggestion = typeof payload.suggestion === "string" ? payload.suggestion.trim() : null;
          store.setSuggestedFollowup(suggestion || null);
          break;
        }

        case "session_updated": {
          const targetSessionId = payload.session_id || data.session_id;
          const newTitle = payload.title;
          if (targetSessionId && newTitle) {
            store.updateSessionTitle(targetSessionId, newTitle);
          }
          break;
        }

        default:
          break;
      }
    } catch {
      // Discard invalid frames
    }
  }

  public async sendTurn(content: string, model?: string): Promise<void> {
    const store = useMissionStore.getState();
    store.setIsExecuting(true);

    // If WebSocket is open and ready, transmit through WebSocket frame
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      try {
        this.ws.send(JSON.stringify({ type: "turn", content, model }));
        return;
      } catch (err) {
        console.warn("[MissionWebSocket] Failed to send via socket, falling back to HTTP:", err);
      }
    }

    // HTTP fallback / universal guarantee: dispatch turn via /v1/sessions/:id/turns
    try {
      const res = await fetch(`/v1/sessions/${this.sessionId}/turns`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ prompt: content, model }),
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

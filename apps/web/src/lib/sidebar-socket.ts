import { type SessionUpdate, sessionUpdateFrom } from "./session-update";
import { getDefaultWsBaseUrl } from "./websocket";

interface SidebarSocketHandlers {
  /** The server is relaying updates: list again to cover what happened while not connected. */
  onReady: () => void;
  onUpdate: (sessionId: string, update: SessionUpdate) => void;
}

const MAX_RECONNECT_DELAY_MS = 10_000;

/**
 * Where each session the member can see stands, pushed as it changes (`/v1/sidebar/ws`). It keeps
 * reconnecting for as long as the page is open: the list has no other live source.
 */
export class SidebarSocket {
  private ws: WebSocket | null = null;
  private attempts = 0;
  private timer: ReturnType<typeof setTimeout> | null = null;
  private closed = false;

  constructor(
    private readonly token: string,
    private readonly handlers: SidebarSocketHandlers
  ) {}

  public isOpen(): boolean {
    return this.ws?.readyState === WebSocket.OPEN;
  }

  public connect(): void {
    if (this.closed) return;
    const query = this.token ? `?token=${encodeURIComponent(this.token)}` : "";
    const ws = new WebSocket(`${getDefaultWsBaseUrl()}/v1/sidebar/ws${query}`);
    this.ws = ws;
    ws.onmessage = (event) => this.handleMessage(event.data);
    ws.onclose = (event) => {
      // Refused (signed out, no access): retrying would be refused again.
      if (event.code === 1008 || this.closed) return;
      this.attempts += 1;
      const delay = Math.min(500 * 2 ** this.attempts, MAX_RECONNECT_DELAY_MS);
      this.timer = setTimeout(() => this.connect(), delay);
    };
  }

  private handleMessage(raw: unknown): void {
    let frame: Record<string, unknown>;
    try {
      const parsed: unknown = JSON.parse(String(raw));
      if (typeof parsed !== "object" || parsed === null) return;
      frame = parsed as Record<string, unknown>;
    } catch {
      return;
    }
    if (frame.type === "ready") {
      this.attempts = 0;
      this.handlers.onReady();
      return;
    }
    if (frame.event_type !== "session_updated") return;
    const payload = (frame.payload ?? {}) as Record<string, unknown>;
    const sessionId = typeof payload.session_id === "string" ? payload.session_id : "";
    if (sessionId) this.handlers.onUpdate(sessionId, sessionUpdateFrom(payload));
  }

  public disconnect(): void {
    this.closed = true;
    if (this.timer) clearTimeout(this.timer);
    const ws = this.ws;
    this.ws = null;
    if (!ws) return;
    ws.onclose = null;
    ws.onmessage = null;
    // A socket still connecting (an effect torn down at once, as React's development mode does)
    // is closed once open: closing it mid-handshake makes the browser report an error.
    if (ws.readyState === WebSocket.CONNECTING) ws.onopen = () => ws.close();
    else ws.close();
  }
}

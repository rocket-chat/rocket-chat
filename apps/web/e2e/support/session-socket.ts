import type { Page, WebSocketRoute } from "@playwright/test";

/**
 * A session's live socket that a spec can talk through. The real backend still answers (its
 * `sync` frame arrives as usual); the spec adds frames the deterministic stub model cannot
 * produce (a command that prints for a while, a plan, a wait) and sees what the page sends.
 */
export interface SessionSocket {
  /** Resolves once the page's socket has received the backend's `sync` frame. */
  ready: Promise<void>;
  /** Frames the page sent, parsed. */
  sent: Record<string, unknown>[];
  /** Deliver an event to the page as if the backend had published it. */
  emit: (eventType: string, payload: Record<string, unknown>) => void;
}

export interface RouteOptions {
  /**
   * Frame types the backend must not receive: the spec answers them itself. The stub backend
   * knows no running turn, so it would start a real one for a message meant to wait.
   */
  intercept?: string[];
}

export async function routeSessionSocket(
  page: Page,
  sessionId: string,
  options: RouteOptions = {}
): Promise<SessionSocket> {
  let route: WebSocketRoute | null = null;
  let markReady: () => void = () => undefined;
  const ready = new Promise<void>((resolve) => {
    markReady = resolve;
  });
  const sent: Record<string, unknown>[] = [];

  await page.routeWebSocket(/\/v1\/sessions\/.*\/ws/, (ws) => {
    route = ws;
    const server = ws.connectToServer();
    server.onMessage((message) => {
      ws.send(message);
      if (typeof message === "string" && message.includes('"sync"')) markReady();
    });
    ws.onMessage((message) => {
      let frame: Record<string, unknown> | null = null;
      if (typeof message === "string") {
        try {
          frame = JSON.parse(message);
          sent.push(frame as Record<string, unknown>);
        } catch {
          // not JSON: forwarded all the same
        }
      }
      if (frame && options.intercept?.includes(String(frame.type))) return;
      server.send(message);
    });
  });

  return {
    ready,
    sent,
    emit: (eventType, payload) => {
      if (!route) throw new Error("The session socket is not open yet");
      route.send(JSON.stringify({ event_type: eventType, session_id: sessionId, payload }));
    },
  };
}

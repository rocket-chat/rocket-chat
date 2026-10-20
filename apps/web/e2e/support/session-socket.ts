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

export async function routeSessionSocket(page: Page, sessionId: string): Promise<SessionSocket> {
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
      if (typeof message === "string") {
        try {
          sent.push(JSON.parse(message));
        } catch {
          // not JSON: forwarded all the same
        }
      }
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

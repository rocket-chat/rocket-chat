import { test, expect, type Page, type WebSocketRoute } from "@playwright/test";

/** Parses a received frame, returning undefined for unparseable payloads. */
function parseFrame(payload: string | Buffer): Record<string, unknown> | undefined {
  if (typeof payload !== "string") return undefined;
  try {
    const parsed: unknown = JSON.parse(payload);
    return typeof parsed === "object" && parsed !== null
      ? (parsed as Record<string, unknown>)
      : undefined;
  } catch {
    return undefined;
  }
}

interface SocketHarness {
  opened: number;
  dropped: boolean;
  frames: Record<string, unknown>[];
}

/**
 * Forces a real mid-turn socket drop and lets the cockpit reconnect on its own.
 *
 * The route connects every page WebSocket to the actual backend so the resume/replay
 * handshake is exercised against the real protocol. The first streamed token is forwarded
 * to the page and then the client side is closed: the prompt has already been applied by
 * then, so the resulting gap (remaining reasoning/tokens/status) can only reach the
 * transcript through `resume{last_seq}` → `replay`.
 */
async function installForcedDrop(page: Page): Promise<SocketHarness> {
  const harness: SocketHarness = { opened: 0, dropped: false, frames: [] };
  const liveSockets = new Set<WebSocketRoute>();

  const forceDrop = () => {
    if (harness.dropped) return;
    harness.dropped = true;
    for (const socket of liveSockets) {
      // Closing every live socket matters because React's dev-mode double-mount can
      // leave more than one route open; the client must lose whichever one it is using.
      void socket.close({ code: 1006, reason: "forced-drop" }).catch(() => {});
    }
  };

  await page.routeWebSocket(/\/v1\/sessions\/.*\/ws/, (ws) => {
    harness.opened += 1;
    liveSockets.add(ws);
    const server = ws.connectToServer();
    ws.onClose(() => {
      liveSockets.delete(ws);
    });
    server.onMessage((message) => {
      const frame = parseFrame(message);
      if (frame) harness.frames.push(frame);
      try {
        ws.send(message);
      } catch {
        // The client side may already be closed on the drop frame; nothing to forward.
      }
      // Drop on the first streamed token, never on the prompt itself: the prompt frame is
      // guaranteed to have been processed by then, so the client's cursor is set and the
      // reconnect will actually issue `resume{last_seq}`. The remaining tokens land in the
      // gap and can only reach the transcript through the replay.
      if (
        frame?.event_type === "reasoning_token" ||
        frame?.event_type === "token"
      ) {
        forceDrop();
      }
    });
  });

  return harness;
}

test.describe("WebSocket Resume — forced reconnect loses nothing", () => {
  test("recovers the full turn exactly once after a forced socket drop", async ({
    page,
    request,
  }) => {
    const createRes = await request.post("http://localhost:8000/v1/sessions", {
      data: { title: "Forced Reconnect Mission" },
    });
    expect(createRes.ok()).toBeTruthy();
    const sessionId: string = (await createRes.json()).session_id;

    const harness = await installForcedDrop(page);

    await page.goto(`/chat/${sessionId}`);
    await expect(page).toHaveURL(`/chat/${sessionId}`);
    await expect.poll(() => harness.opened).toBeGreaterThanOrEqual(1);

    const prompt = `resume-probe-${Date.now()}`;
    const input = page.getByTestId("instruction-input");
    await expect(input).toBeEnabled();
    await input.fill(prompt);
    await page.getByTestId("ignite-button").click();

    // The drop fires on the first streamed token, so it happens mid-turn by construction.
    await expect.poll(() => harness.dropped).toBe(true);

    // The client reconnects on its own and resumes from the persisted cursor.
    await expect.poll(() => harness.opened, { timeout: 15000 }).toBeGreaterThanOrEqual(2);

    // The prompt is rendered exactly once on the originating client (deduped against
    // both the optimistic entry and the snapshot rebuild).
    const userBubble = page.getByTestId("user-message").filter({ hasText: prompt });
    await expect(userBubble).toHaveCount(1, { timeout: 15000 });

    // The turn's tokens crossed the gap and were replayed: the answer is complete and
    // appears exactly once (it must not be folded into the persisted history entry again).
    // The whole transcript is searched by text because a duplicate would be concatenated
    // *inside* the single answer entry, not rendered as a second element.
    const transcript = page.getByTestId("flight-log-stream");
    await expect(transcript.getByText(/Stub answer/).first()).toBeVisible({ timeout: 15000 });
    await expect
      .poll(async () => ((await transcript.innerText()).match(/Stub answer/g) ?? []).length)
      .toBe(1);

    // The replay was continuous, so the transcript was never marked discontinuous and
    // never fell back to a snapshot-only refetch.
    await expect(page.getByTestId("flight-log-stream")).toHaveAttribute(
      "data-stream-continuous",
      "true",
    );

    // A continuous replay frame really did arrive on the reconnected socket.
    await expect
      .poll(() =>
        harness.frames.some((frame) => frame.type === "replay" && frame.continuous !== false),
      )
      .toBe(true);
  });
});

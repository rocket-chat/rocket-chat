import { test, expect, type Page } from "@playwright/test";

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

/**
 * Records every mission WebSocket, its frames, and its open/close counters.
 *
 * Counters are the stability oracle: the historical bug reconnected every 40-100ms,
 * which shows up as open/close counts growing while a turn is in flight.
 */
function trackSessionSockets(page: Page) {
  const frames: Record<string, unknown>[] = [];
  let opened = 0;
  let closed = 0;

  page.on("websocket", (ws) => {
    if (!ws.url().includes("/v1/sessions/")) return;
    opened += 1;
    ws.on("framereceived", (event) => {
      const parsed = parseFrame(event.payload);
      if (parsed) frames.push(parsed);
    });
    ws.on("close", () => {
      closed += 1;
    });
  });

  return {
    frames,
    get opened() {
      return opened;
    },
    get closed() {
      return closed;
    },
  };
}

test.describe("WebSocket Stability & Continuous Telemetry Integrity", () => {
  test("survives a complete turn on a single WebSocket without reconnecting", async ({ page }) => {
    const tracker = trackSessionSockets(page);

    await page.goto("/");
    await expect(page).toHaveURL(/\/chat\/.+/, { timeout: 15000 });

    const input = page.getByTestId("instruction-input");
    await expect(input).toBeEnabled();
    await input.fill("Initialize test session");
    await page.getByTestId("ignite-button").click();
    await expect(page).toHaveURL(/\/chat\/session_.+/, { timeout: 15000 });

    // Wait for the handshake frame rather than a fixed sleep.
    await expect.poll(() => tracker.frames.some((frame) => frame.type === "sync")).toBe(true);
    expect(tracker.opened).toBeGreaterThanOrEqual(1);

    const openedAfterSync = tracker.opened;
    const closedAfterSync = tracker.closed;

    // Now run a full turn on this socket and wait for its telemetry to arrive: the
    // turn streams several frames, so a reconnect loop would have to open a socket
    // before it could finish. This replaces the previous "sleep 3s and hope" check.
    // Note the executing state itself is transient (the stub answers in milliseconds),
    // so the turn's *frames* are the only race-free signal.
    const framesAfterSync = tracker.frames.length;
    await expect(page.getByTestId("stop-button")).toBeHidden();
    await expect(input).toBeEnabled();
    await input.fill("Second instruction on the same socket");
    await page.getByTestId("ignite-button").click();
    await expect
      .poll(() =>
        tracker.frames
          .slice(framesAfterSync)
          .some((frame) => typeof frame.event_type === "string"),
      )
      .toBe(true);

    // The turn completed on the original socket: nothing was reopened or dropped.
    expect(tracker.opened).toBe(openedAfterSync);
    expect(tracker.closed).toBe(closedAfterSync);

    // Typing must not tear the socket down either.
    await input.fill("Typing without reconnection");
    await expect(page.getByTestId("flight-log-stream")).toBeVisible();
    expect(tracker.opened).toBe(openedAfterSync);
    expect(tracker.closed).toBe(closedAfterSync);
  });

  test("does not disconnect the WebSocket while the layout re-renders", async ({ page }) => {
    const tracker = trackSessionSockets(page);

    await page.goto("/");
    await expect(page).toHaveURL(/\/chat\/.+/, { timeout: 15000 });

    // A fresh browser context starts on the draft route, which has no session socket
    // yet, so ignite a turn to obtain a real session and its WebSocket.
    const input = page.getByTestId("instruction-input");
    await expect(input).toBeEnabled();
    await input.fill("Layout re-render stability");
    await page.getByTestId("ignite-button").click();
    await expect(page).toHaveURL(/\/chat\/session_.+/, { timeout: 15000 });

    await expect.poll(() => tracker.frames.some((frame) => frame.type === "sync")).toBe(true);
    const openedAfterSync = tracker.opened;

    // Toggle the right panel twice, waiting on the control's own state instead of a
    // sleep, so the assertion cannot pass before the re-render happened.
    const toggleButton = page.getByTitle(
      /Expand Right Panel|Show Panel|Collapse Right Panel|Hide Panel/i,
    );
    if (await toggleButton.isVisible()) {
      const initialTitle = await toggleButton.getAttribute("title");
      await toggleButton.click();
      await expect.poll(async () => toggleButton.getAttribute("title")).not.toBe(initialTitle);
      await toggleButton.click();
      await expect.poll(async () => toggleButton.getAttribute("title")).toBe(initialTitle);
    }

    // No socket may have been closed or replaced by the re-renders.
    expect(tracker.closed).toBe(0);
    expect(tracker.opened).toBe(openedAfterSync);
  });
});

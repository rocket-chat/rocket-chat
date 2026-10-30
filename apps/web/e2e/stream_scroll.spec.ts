import { test, expect, type Page, type WebSocketRoute } from "@playwright/test";

const BACKEND = "http://localhost:8000";
let stepCounter = 0;

/** A long conversation, so the stream has something to scroll through. */
async function openLongSession(page: Page): Promise<WebSocketRoute> {
  stepCounter = 0;
  let socket: WebSocketRoute | null = null;
  // Proxy the real socket (the transcript arrives in its `sync`), and keep a handle for injecting
  // extra frames into the page.
  await page.routeWebSocket(/\/v1\/sessions\/[^/]+\/ws/, (ws) => {
    ws.connectToServer();
    socket = ws;
  });

  const created = await page.request.post(`${BACKEND}/v1/sessions`, { data: { title: "Scroll" } });
  const sessionId = (await created.json()).session_id as string;
  const messages = Array.from({ length: 40 }, (_, i) => [
    { role: "user", content: `Question number ${i}` },
    { role: "assistant", content: `Answer number ${i}. ${"Some detail of the investigation. ".repeat(12)}` },
  ]).flat();
  await page.request.post(`${BACKEND}/v1/dev/sessions/${sessionId}/transcript`, { data: { messages } });

  await page.goto(`/chat/${sessionId}`);
  await expect(page.getByTestId("flight-log-stream")).toContainText("Answer number 39");
  await expect.poll(() => socket !== null, { message: "the session socket opens" }).toBe(true);
  if (!socket) throw new Error("no session socket");
  return socket;
}

const scrollState = (page: Page) =>
  page.getByTestId("flight-log-stream").evaluate((el) => ({
    top: el.scrollTop,
    distanceFromBottom: el.scrollHeight - el.scrollTop - el.clientHeight,
  }));

/**
 * Pushes one step into the page. The cockpit ignores frames while its first snapshot is still
 * being applied, so the frame is re-sent (same id, so it renders once) until it shows.
 */
async function pushStep(socket: WebSocketRoute, page: Page): Promise<void> {
  stepCounter += 1;
  const frame = JSON.stringify({
    event_type: "tool_started",
    session_id: page.url().split("/chat/")[1],
    payload: { id: `t${stepCounter}`, name: `step_${stepCounter}`, arguments: {} },
  });
  const stream = page.getByTestId("flight-log-stream");
  // Consecutive steps are summarised as "Used N tools".
  const summary = new RegExp(`Used ${stepCounter} tools?`);
  await expect
    .poll(
      async () => {
        const shown = summary.test(await stream.innerText());
        if (!shown) socket.send(frame);
        return shown;
      },
      { timeout: 15000, intervals: [200, 300, 500] },
    )
    .toBe(true);
}

test.describe("Following the stream", () => {
  test("new steps keep the view at the bottom while the reader is at the bottom", async ({ page }) => {
    const socket = await openLongSession(page);
    await expect.poll(async () => (await scrollState(page)).distanceFromBottom).toBeLessThan(80);

    await pushStep(socket, page);
    await pushStep(socket, page);

    await expect.poll(async () => (await scrollState(page)).distanceFromBottom).toBeLessThan(80);
    await expect(page.getByTestId("scroll-to-latest")).toHaveCount(0);
  });

  test("a reader who scrolled up is not pulled down, and can jump back with the arrow", async ({ page }) => {
    const socket = await openLongSession(page);
    await expect.poll(async () => (await scrollState(page)).distanceFromBottom).toBeLessThan(80);

    // Once one step has shown, the snapshot is applied and later frames are taken as sent.
    await pushStep(socket, page);
    await page.getByTestId("flight-log-stream").evaluate((el) => el.scrollTo({ top: 300 }));
    await expect(page.getByTestId("scroll-to-latest")).toBeVisible();

    // A long transcript is virtualised: rows far below the view are not in the page, so the steps
    // are confirmed by a status frame sent after them (frames are applied in order).
    const sessionId = page.url().split("/chat/")[1];
    for (const id of ["t2", "t3", "t4"]) {
      socket.send(JSON.stringify({ event_type: "tool_started", session_id: sessionId, payload: { id, name: `step_${id}`, arguments: {} } }));
    }
    socket.send(JSON.stringify({ event_type: "status_changed", session_id: sessionId, payload: { status: "running", message: "Working", step: 7 } }));
    await expect(page.getByTestId("turn-progress")).toContainText("step 7");
    stepCounter = 4;

    // Rows above the view are measured as they render and the virtualiser keeps them anchored,
    // which moves the offset by a few pixels; being pulled down would move it by thousands.
    const held = await scrollState(page);
    expect(Math.abs(held.top - 300)).toBeLessThan(100);
    expect(held.distanceFromBottom).toBeGreaterThan(1000);

    await page.getByTestId("scroll-to-latest").click();
    await expect.poll(async () => (await scrollState(page)).distanceFromBottom).toBeLessThan(80);
    await expect(page.getByTestId("scroll-to-latest")).toHaveCount(0);
    await expect(page.getByTestId("flight-log-stream")).toContainText("Used 4 tools");

    // Following resumes after the jump.
    await pushStep(socket, page);
    await expect.poll(async () => (await scrollState(page)).distanceFromBottom).toBeLessThan(80);
  });
});

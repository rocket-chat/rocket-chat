import { test, expect, type Page, type WebSocketRoute } from "@playwright/test";

const BACKEND = "http://localhost:8000";
let stepCounter = 0;

/** A long conversation, so the stream has something to scroll through. */
async function openLongSession(page: Page): Promise<WebSocketRoute> {
  stepCounter = 0;
  let socket: WebSocketRoute | null = null;
  // Proxy the real socket (the transcript arrives in its `sync`), and keep a handle for injecting
  // extra frames into the page.
  await page.routeWebSocket(/\/ws/, (ws) => {
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

    await page.getByTestId("flight-log-stream").evaluate((el) => el.scrollTo({ top: 300 }));
    await expect(page.getByTestId("scroll-to-latest")).toBeVisible();

    await pushStep(socket, page);
    await pushStep(socket, page);
    await pushStep(socket, page);

    expect((await scrollState(page)).top).toBeCloseTo(300, -1);

    await page.getByTestId("scroll-to-latest").click();
    await expect.poll(async () => (await scrollState(page)).distanceFromBottom).toBeLessThan(80);
    await expect(page.getByTestId("scroll-to-latest")).toHaveCount(0);

    // Following resumes after the jump.
    await pushStep(socket, page);
    await expect.poll(async () => (await scrollState(page)).distanceFromBottom).toBeLessThan(80);
  });
});

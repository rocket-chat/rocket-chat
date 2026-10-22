import { test, expect, type Page, type WebSocketRoute } from "@playwright/test";

const BACKEND = "http://localhost:8000";

async function openSession(page: Page): Promise<{ socket: WebSocketRoute; sessionId: string }> {
  let socket: WebSocketRoute | null = null;
  await page.routeWebSocket(/\/ws/, (ws) => {
    ws.connectToServer();
    socket = ws;
  });
  const created = await page.request.post(`${BACKEND}/v1/sessions`, { data: { title: "Thinking" } });
  const sessionId = (await created.json()).session_id as string;
  await page.request.post(`${BACKEND}/v1/dev/sessions/${sessionId}/transcript`, {
    data: { messages: [{ role: "user", content: "Look into it" }] },
  });
  await page.goto(`/chat/${sessionId}`);
  await expect(page.getByTestId("flight-log-stream")).toContainText("Look into it");
  if (!socket) throw new Error("no session socket");
  return { socket, sessionId };
}

/** The cockpit ignores frames while its first snapshot is applied: re-send until it shows. */
async function pushUntil(socket: WebSocketRoute, frame: object, shown: () => Promise<boolean>): Promise<void> {
  await expect
    .poll(
      async () => {
        const done = await shown();
        if (!done) socket.send(JSON.stringify(frame));
        return done;
      },
      { timeout: 15000, intervals: [200, 300, 500] },
    )
    .toBe(true);
}

test("a thought still being written looks different from a finished one", async ({ page }) => {
  const { socket, sessionId } = await openSession(page);
  const live = page.locator('[data-testid="activity-step"][aria-expanded="true"]').locator("xpath=ancestor::li[@data-live='true']");

  await pushUntil(
    socket,
    { event_type: "reasoning_token", session_id: sessionId, payload: { delta: "Checking the retry path. " } },
    async () => (await live.count()) > 0,
  );
  // Live: marked, titled "Thinking…", open on its text without a click.
  await expect(live).toContainText("Thinking…");
  await expect(live).toContainText("Checking the retry path.");
  // A rocket-flavoured filler line sits above the step message at the bottom loader (not in the
  // group header), with the rocket's plume; both are hidden from assistive tech.
  const indicator = page.getByTestId("processing-indicator");
  const quip = indicator.getByTestId("loading-quip");
  await expect(quip).toBeVisible();
  await expect(quip).toHaveAttribute("aria-hidden", "true");
  await expect(indicator.getByTestId("rocket-loader")).toBeVisible();
  await expect(page.getByTestId("action-group-toggle").getByTestId("loading-quip")).toHaveCount(0);
  const [quipBox, statusBox] = await Promise.all([
    quip.boundingBox(),
    indicator.locator("span.truncate").boundingBox(),
  ]);
  expect(quipBox!.y).toBeLessThan(statusBox!.y);

  socket.send(JSON.stringify({ event_type: "status_changed", session_id: sessionId, payload: { status: "completed", final: true } }));

  // Finished: the same thought is a plain "Thought" row in a collapsed group.
  await expect(page.locator("[data-live='true']")).toHaveCount(0);
  await expect(page.getByTestId("loading-quip")).toHaveCount(0);
  await page.getByTestId("action-group-toggle").click();
  await expect(page.getByTestId("activity-step").filter({ hasText: "Thought" })).toBeVisible();
  await expect(page.getByTestId("activity-step").filter({ hasText: "Thinking…" })).toHaveCount(0);
});

test("command output in the agent terminal starts every line at the left edge", async ({ page }) => {
  const { socket, sessionId } = await openSession(page);
  await page.getByTitle(/Expand Right Panel|Show Panel/i).click();
  const frame = {
    event_type: "log_chunk",
    session_id: sessionId,
    payload: { text: "first line of output\nsecond line of output\n 3 files changed\n" },
  };
  const rows = page.locator(".xterm-rows");
  await expect
    .poll(
      async () => {
        const shown = (await rows.allInnerTexts()).join("").includes("second line of output");
        if (!shown) socket.send(JSON.stringify(frame));
        return shown;
      },
      { timeout: 15000, intervals: [200, 300, 500] },
    )
    .toBe(true);

  const lines = await rows.first().evaluate((el) => Array.from(el.children).map((row) => row.textContent ?? ""));
  const second = lines.find((line) => line.includes("second line of output"))!;
  // A bare "\n" without a carriage return would indent each line by the length of the one above.
  expect(second.search(/\S/)).toBe(0);
});

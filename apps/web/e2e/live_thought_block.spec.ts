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
  // A rocket-flavoured filler line rides next to the step while it runs, hidden from assistive tech.
  const quip = page.getByTestId("loading-quip");
  await expect(quip).toBeVisible();
  await expect(quip).toHaveAttribute("aria-hidden", "true");

  socket.send(JSON.stringify({ event_type: "status_changed", session_id: sessionId, payload: { status: "completed", final: true } }));

  // Finished: the same thought is a plain "Thought" row in a collapsed group.
  await expect(page.locator("[data-live='true']")).toHaveCount(0);
  await expect(page.getByTestId("loading-quip")).toHaveCount(0);
  await page.getByTestId("action-group-toggle").click();
  await expect(page.getByTestId("activity-step").filter({ hasText: "Thought" })).toBeVisible();
  await expect(page.getByTestId("activity-step").filter({ hasText: "Thinking…" })).toHaveCount(0);
});

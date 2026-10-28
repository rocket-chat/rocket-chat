import { test, expect, type WebSocketRoute } from "@playwright/test";

/**
 * A provider that refuses the call (a bad key, an unknown model) ends the turn with an error the
 * person can act on. It is shown as an error notice, never as an answer from the assistant.
 */
const REFUSAL =
  "anthropic refused the credentials: add or fix the provider key in Settings > Models (invalid x-api-key)";

/** The session socket, once the cockpit has opened it (it may still be connecting). */
async function openSession(page: import("@playwright/test").Page): Promise<() => WebSocketRoute | null> {
  let socket: WebSocketRoute | null = null;
  await page.routeWebSocket(/\/v1\/sessions\/[^/]+\/ws/, (ws) => {
    socket = ws;
    ws.send(JSON.stringify({ type: "sync" }));
  });
  await page.goto("/");
  await page.getByTestId("instruction-input").fill("Say hello");
  await page.getByTestId("ignite-button").click();
  await expect(page).toHaveURL(/\/chat\/session_.+/, { timeout: 15000 });
  return () => socket;
}

test("a refused provider shows an actionable error, not an answer", async ({ page }) => {
  const socket = await openSession(page);
  const notice = page.getByText(/Mission Execution Error/);

  // The cockpit resets session state once the snapshot loads, so the frame is re-sent until
  // the notice shows (as in the approval-gate spec).
  await expect
    .poll(
      async () => {
        socket()?.send(
          JSON.stringify({
            event_type: "status_changed",
            payload: { status: "error", message: REFUSAL, error: REFUSAL, final: true },
          }),
        );
        return notice.count();
      },
      { timeout: 15000, intervals: [200, 400, 800, 1000] },
    )
    .toBeGreaterThan(0);

  await expect(notice.first()).toContainText("Settings > Models");
  // Only ever rendered as the error notice: no answer bubble repeats it.
  await expect(page.getByText(/x-api-key/)).toHaveCount(await notice.count());
});

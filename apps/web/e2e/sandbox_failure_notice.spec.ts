import { test, expect, type WebSocketRoute } from "@playwright/test";

const REASON =
  "The sandbox could not start: ImagePullBackOff for image 'ghcr.io/acme/sandbox:latest' (pod p).";

/**
 * The session socket is intercepted so the failure can be pushed without a cluster. The cockpit
 * resets session-scoped state once the snapshot loads, so the frame is re-sent until it shows.
 */
test("a sandbox that cannot start is explained in the conversation", async ({ page }) => {
  let socket: WebSocketRoute | null = null;
  await page.routeWebSocket(/\/ws/, (ws) => {
    socket = ws;
    ws.send(JSON.stringify({ type: "sync" }));
  });

  await page.goto("/");
  await page.getByTestId("instruction-input").fill("Run the tests");
  await page.getByTestId("ignite-button").click();
  await expect(page).toHaveURL(/\/chat\/session_.+/, { timeout: 15000 });
  if (!socket) throw new Error("The cockpit did not open a telemetry WebSocket");
  const channel: WebSocketRoute = socket;

  const notice = page.getByText(REASON);
  await expect
    .poll(
      async () => {
        channel.send(
          JSON.stringify({
            event_type: "sandbox_state",
            payload: { state: "failed", message: REASON },
          }),
        );
        return notice.count();
      },
      { timeout: 15000, intervals: [200, 200, 400, 600, 1000] },
    )
    .toBeGreaterThan(0);
  await expect(notice.first()).toBeVisible();
});

import { test, expect, type WebSocketRoute } from "@playwright/test";

const BACKEND = "http://localhost:8000";

/** The live view keeps reconnecting after an outage, says so meanwhile, and resumes the stream. */
test("a dropped live connection shows a banner and resumes when the network is back", async ({ page }) => {
  const sockets: WebSocketRoute[] = [];
  const live: WebSocketRoute[] = [];
  let refusing = false;
  await page.routeWebSocket(/\/v1\/sessions\/[^/]+\/ws/, (ws) => {
    if (refusing) {
      void ws.close({ code: 1011, reason: "unavailable" });
      sockets.push(ws);
      return;
    }
    ws.connectToServer();
    sockets.push(ws);
    live.push(ws);
  });

  const created = await page.request.post(`${BACKEND}/v1/sessions`, { data: { title: "Reconnect" } });
  const sessionId = (await created.json()).session_id as string;
  await page.goto(`/chat/${sessionId}`);
  await expect.poll(() => sockets.length, { message: "the session socket opens" }).toBeGreaterThan(0);
  const banner = page.getByTestId("connection-banner");
  await expect(banner).toHaveCount(0);

  refusing = true;
  for (const ws of live.splice(0)) await ws.close({ code: 1011, reason: "server restart" });
  await expect(banner).toContainText("Reconnecting");

  // More attempts than the five the client used to give up after; each "online" retries at once.
  for (let attempt = 0; attempt < 7; attempt++) {
    const before = sockets.length;
    await page.evaluate(() => window.dispatchEvent(new Event("online")));
    await expect.poll(() => sockets.length).toBeGreaterThan(before);
  }
  await expect(banner).toBeVisible();

  refusing = false;
  const before = sockets.length;
  await page.evaluate(() => window.dispatchEvent(new Event("focus")));
  await expect.poll(() => sockets.length).toBeGreaterThan(before);
  await expect(banner).toHaveCount(0);
});

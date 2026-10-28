import { test, expect } from "@playwright/test";

const API = "http://localhost:8000";

/** A session that came from Slack links back to its thread; a web session does not. */
test("a Slack session links to its thread in Slack", async ({ page, request }) => {
  const created = await request.post(`${API}/v1/sessions`, { data: { title: "From Slack" } });
  const sessionId = (await created.json()).session_id as string;
  // Only the Slack bridge writes these keys, so the socket's session snapshot is given them.
  await page.routeWebSocket(/\/v1\/sessions\/[^/]+\/ws/, (ws) => {
    const server = ws.connectToServer();
    ws.onMessage((message) => server.send(message));
    server.onMessage((message) => {
      const frame = JSON.parse(String(message));
      if (frame.session?.session_id === sessionId) {
        frame.session.metadata = {
          ...frame.session.metadata,
          slack_channel_id: "C123",
          slack_thread_ts: "1700000000.000100",
          slack_permalink: "https://acme.slack.com/archives/C123/p1700000000000100",
        };
      }
      ws.send(JSON.stringify(frame));
    });
  });

  await page.goto(`/chat/${sessionId}`);

  const link = page.getByTestId("open-in-slack");
  await expect(link).toBeVisible();
  await expect(link).toHaveAttribute("href", "https://acme.slack.com/archives/C123/p1700000000000100");
  await expect(link).toHaveAttribute("target", "_blank");
});

test("a web session has no Slack link", async ({ page, request }) => {
  const created = await request.post(`${API}/v1/sessions`, { data: { title: "From the web" } });
  const sessionId = (await created.json()).session_id as string;

  await page.goto(`/chat/${sessionId}`);

  await expect(page.getByRole("heading", { name: "From the web" })).toBeVisible();
  await expect(page.getByTestId("open-in-slack")).toHaveCount(0);
});

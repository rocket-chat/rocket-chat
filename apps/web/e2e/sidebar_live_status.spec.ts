import { test, expect } from "@playwright/test";

const BACKEND = "http://localhost:8000";

test("a sidebar row follows a change to another conversation without a reload", async ({ page }) => {
  const created = await page.request.post(`${BACKEND}/v1/sessions`, { data: { title: "Watched from the list" } });
  const watched = (await created.json()).session_id as string;
  const open = (await (await page.request.post(`${BACKEND}/v1/sessions`, { data: { title: "Open one" } })).json())
    .session_id as string;

  // Only the sidebar socket may refresh the list from here on: any read of it fails the test.
  const sidebarSocket = page.waitForEvent("websocket", (ws) => ws.url().includes("/v1/sidebar/ws"));
  await page.goto(`/chat/${open}`);
  await sidebarSocket;
  const row = page.locator(`[data-testid="session-row"][data-session-id="${watched}"]`);
  await expect(row).toHaveAttribute("data-pinned", "false");
  let listedAgain = false;
  page.on("request", (request) => {
    if (new URL(request.url()).pathname === "/v1/sessions" && request.method() === "GET") listedAgain = true;
  });

  // Changed elsewhere (another tab, a teammate, the agent): the open conversation's socket is not told.
  const pinned = await page.request.patch(`${BACKEND}/v1/sessions/${watched}/flags`, { data: { is_pinned: true } });
  expect(pinned.ok()).toBe(true);

  await expect(row).toHaveAttribute("data-pinned", "true", { timeout: 4000 });
  expect(listedAgain).toBe(false);
});

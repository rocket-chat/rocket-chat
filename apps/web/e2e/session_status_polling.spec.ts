import { test, expect } from "@playwright/test";

const BACKEND = "http://localhost:8000";

test("a session I am not looking at shows when it starts working", async ({ page }) => {
  const stamp = Date.now();
  const other = await page.request.post(`${BACKEND}/v1/sessions`, { data: { title: `Elsewhere ${stamp}` } });
  const otherId = (await other.json()).session_id as string;
  const open = await page.request.post(`${BACKEND}/v1/sessions`, { data: { title: `Focused ${stamp}` } });
  const openId = (await open.json()).session_id as string;

  // The server marks the other session as working after the page has loaded: nothing is sent to
  // this page's live connection for it, so only a re-read of the list can show it.
  let otherIsWorking = false;
  await page.route(/\/v1\/sessions\?/, async (route) => {
    const response = await route.fetch();
    if (!otherIsWorking || route.request().method() !== "GET") return route.fulfill({ response });
    const sessions = (await response.json()) as Array<{ session_id: string; is_processing?: boolean }>;
    return route.fulfill({
      response,
      json: sessions.map((s) => (s.session_id === otherId ? { ...s, is_processing: true } : s)),
    });
  });

  await page.goto(`/chat/${openId}`);
  const row = page.locator("[data-testid='session-row']", { hasText: `Elsewhere ${stamp}` });
  await expect(row).toBeVisible();
  await expect(row.getByTestId("session-status")).toHaveCount(0);

  otherIsWorking = true;

  await expect(row.getByTestId("session-status")).toHaveAttribute("data-status", "working", {
    timeout: 20000,
  });
});

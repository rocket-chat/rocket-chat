import { test, expect } from "@playwright/test";

/**
 * Switching the sidebar's scope while a listing is still loading must end on the scope chosen
 * last: a slow "whole organization" response used to overwrite the "mine" list that followed it.
 */
test("a slow organization listing does not overwrite the list switched to after it", async ({
  page,
  request,
}) => {
  const theirs = `Teammate's slow mission ${Date.now()}`;
  const created = await request.post("http://localhost:8000/v1/sessions", {
    headers: { "X-Tenant-User-Id": "teammate_slow_scope", "X-User-Role": "developer" },
    data: { title: theirs },
  });
  expect(created.ok()).toBeTruthy();
  await page.route(/\/v1\/sessions\?.*scope=organization/, async (route) => {
    await new Promise((resolve) => setTimeout(resolve, 1500));
    await route.continue();
  });

  await page.goto("/");
  await expect(page).toHaveURL(/\/chat\/.+/);
  const sidebar = page.getByTestId("session-sidebar");
  const everyone = page.getByTestId("toggle-org-sessions-btn");
  await everyone.click();
  await everyone.click();
  await expect(everyone).toHaveAttribute("aria-pressed", "false");

  // Past the delayed organization response: the sidebar still shows only the caller's view.
  await page.waitForTimeout(2500);
  await expect(sidebar.getByText(theirs)).toHaveCount(0);
});

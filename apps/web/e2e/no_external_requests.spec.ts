import { test, expect } from "@playwright/test";

/**
 * Air-gapped installs (doc/deployment-and-ops/air-gapped.md) need a cockpit that never reaches
 * for a CDN: fonts, the Monaco editor and the terminal stylesheet are all served by the app.
 */
test("the cockpit makes no request to a third-party origin", async ({ page }) => {
  const foreign: string[] = [];
  page.on("request", (request) => {
    const url = new URL(request.url());
    if (!["localhost", "127.0.0.1"].includes(url.hostname) && url.protocol.startsWith("http")) {
      foreign.push(request.url());
    }
  });

  await page.goto("/");
  await page.getByTestId("instruction-input").fill("[stub:tool] look around");
  await page.getByTestId("ignite-button").click();
  await expect(page).toHaveURL(/\/chat\/session_.+/, { timeout: 15000 });
  await expect(page.getByText(/Stub answer/).first()).toBeVisible({ timeout: 20000 });

  await page.getByRole("button", { name: /Expand Right Panel/ }).click();
  await page.getByTestId("tab-files").click();
  await page.getByTestId("ws-file").first().click();
  await expect(page.locator(".monaco-editor").first()).toBeVisible({ timeout: 30000 });

  for (const path of ["/missions", "/settings/github", "/settings/skills"]) {
    await page.goto(path);
    await page.waitForLoadState("networkidle");
  }

  expect(foreign).toEqual([]);
});

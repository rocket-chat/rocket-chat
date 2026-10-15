import { test, expect } from "@playwright/test";

/**
 * A file the agent writes shows up in the cockpit's list of changes. `[stub:write]` makes the
 * stub model call `file_write` for real (fake sandbox), so this exercises the tool's own diff
 * event end to end, not an injected frame.
 */
test("a file the agent wrote is listed in the changes panel", async ({ page }) => {
  await page.goto("/");
  await page.getByTestId("instruction-input").fill("[stub:write] save a note");
  await page.getByTestId("ignite-button").click();
  await expect(page).toHaveURL(/\/chat\/session_.+/, { timeout: 15000 });
  await expect(page.getByText(/Stub answer/).first()).toBeVisible({ timeout: 20000 });

  await page.getByRole("button", { name: /Expand Right Panel/ }).click();
  await page.getByTestId("tab-changes").click();

  await expect(page.getByText("notes/written_by_agent.md").first()).toBeVisible({ timeout: 15000 });
});
